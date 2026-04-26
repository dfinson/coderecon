"""Doc/config → code semantic linking via SPLADE chunk vectors.

Non-code files (markdown docs, YAML/TOML configs, etc.) are chunked
into structural segments, SPLADE-encoded, and dot-producted against
definition vectors to create DocCodeEdgeFact edges.

Chunking strategy:
  - Markdown: split on heading boundaries (# / ## / etc.)
  - YAML/TOML/INI/JSON: split on top-level keys
  - Fallback: paragraph-based splitting (double newline)
"""

from __future__ import annotations

import structlog
import re
import time
from typing import TYPE_CHECKING

from sqlmodel import col, select

from coderecon.index._internal.indexing.splade import (
    MODEL_VERSION,
    _get_encoder,
    _json_to_vec,
    _vec_to_json,
    load_all_vectors_fast,
    sparse_dot,
)
from coderecon.config.constants import DB_FLUSH_BATCH_SIZE
from coderecon.index.models import (
    DocCodeEdgeFact,
    File,
    FileChunkVec,
    LanguageFamily,
)

if TYPE_CHECKING:
    from coderecon.index._internal.db.database import Database

log = structlog.get_logger(__name__)

# ── Tuning constants ─────────────────────────────────────────────

# Minimum SPLADE dot-product score to persist a doc→code edge.
SIGMA_DOC_FLOOR = 12.0


# Maximum edges per chunk (cap noisy chunks).
MAX_EDGES_PER_CHUNK = 10

# Minimum chunk text length to bother encoding.
MIN_CHUNK_LENGTH = 30

# Maximum chunk text length (truncate long sections).
MAX_CHUNK_LENGTH = 2000

# Doc/config families that get chunk linking.
_DOC_FAMILIES: frozenset[str] = frozenset({
    LanguageFamily.MARKDOWN,
    LanguageFamily.RST,
    LanguageFamily.ASCIIDOC,
    LanguageFamily.YAML,
    LanguageFamily.TOML,
    LanguageFamily.JSON,
    LanguageFamily.DOCKER,
    LanguageFamily.MAKE,
    LanguageFamily.CMAKE,
    LanguageFamily.JUST,
    LanguageFamily.BAZEL,
})


# ── Chunking ─────────────────────────────────────────────────────


def _chunk_markdown(text: str) -> list[tuple[str, str, int, int]]:
    """Split markdown by heading boundaries.

    Returns [(chunk_key, chunk_text, start_line, end_line)].
    """
    lines = text.split("\n")
    chunks: list[tuple[str, str, int, int]] = []
    current_key = "preamble"
    current_lines: list[str] = []
    start = 1

    for i, line in enumerate(lines, 1):
        m = re.match(r"^(#{1,6})\s+(.+)", line)
        if m and current_lines:
            chunk_text = "\n".join(current_lines).strip()
            if len(chunk_text) >= MIN_CHUNK_LENGTH:
                chunks.append((current_key, chunk_text[:MAX_CHUNK_LENGTH], start, i - 1))
            current_key = m.group(2).strip()
            current_lines = [line]
            start = i
        else:
            current_lines.append(line)

    # Last chunk
    if current_lines:
        chunk_text = "\n".join(current_lines).strip()
        if len(chunk_text) >= MIN_CHUNK_LENGTH:
            chunks.append((current_key, chunk_text[:MAX_CHUNK_LENGTH], start, len(lines)))

    return chunks


def _chunk_keyvalue(text: str) -> list[tuple[str, str, int, int]]:
    """Split YAML/TOML/INI by top-level keys.

    Simple heuristic: lines starting at column 0 with word chars
    followed by colon or equals are key boundaries.
    """
    lines = text.split("\n")
    chunks: list[tuple[str, str, int, int]] = []
    current_key = "header"
    current_lines: list[str] = []
    start = 1
    key_pattern = re.compile(r"^([a-zA-Z_][\w.-]*)\s*[:=]")

    for i, line in enumerate(lines, 1):
        m = key_pattern.match(line)
        if m and current_lines:
            chunk_text = "\n".join(current_lines).strip()
            if len(chunk_text) >= MIN_CHUNK_LENGTH:
                chunks.append((current_key, chunk_text[:MAX_CHUNK_LENGTH], start, i - 1))
            current_key = m.group(1)
            current_lines = [line]
            start = i
        else:
            current_lines.append(line)

    if current_lines:
        chunk_text = "\n".join(current_lines).strip()
        if len(chunk_text) >= MIN_CHUNK_LENGTH:
            chunks.append((current_key, chunk_text[:MAX_CHUNK_LENGTH], start, len(lines)))

    return chunks


def _chunk_paragraphs(text: str) -> list[tuple[str, str, int, int]]:
    """Fallback: split on double newlines."""
    lines = text.split("\n")
    chunks: list[tuple[str, str, int, int]] = []
    current_lines: list[str] = []
    start = 1
    idx = 0

    for i, line in enumerate(lines, 1):
        if line.strip() == "" and current_lines and current_lines[-1].strip() == "":
            chunk_text = "\n".join(current_lines).strip()
            if len(chunk_text) >= MIN_CHUNK_LENGTH:
                chunks.append((f"para_{idx}", chunk_text[:MAX_CHUNK_LENGTH], start, i - 1))
                idx += 1
            current_lines = []
            start = i + 1
        else:
            current_lines.append(line)

    if current_lines:
        chunk_text = "\n".join(current_lines).strip()
        if len(chunk_text) >= MIN_CHUNK_LENGTH:
            chunks.append((f"para_{idx}", chunk_text[:MAX_CHUNK_LENGTH], start, len(lines)))

    return chunks


def chunk_file(text: str, language_family: str) -> list[tuple[str, str, int, int]]:
    """Chunk a non-code file into semantic segments.

    Returns [(chunk_key, chunk_text, start_line, end_line)].
    """
    if language_family in {LanguageFamily.MARKDOWN, LanguageFamily.RST, LanguageFamily.ASCIIDOC}:
        return _chunk_markdown(text)
    elif language_family in {LanguageFamily.YAML, LanguageFamily.TOML, LanguageFamily.JSON}:
        return _chunk_keyvalue(text)
    else:
        return _chunk_paragraphs(text)


# ── Core pipeline ────────────────────────────────────────────────


def index_doc_chunk_vectors(
    db: Database,
    *,
    file_ids: list[int] | None = None,
) -> int:
    """Chunk and SPLADE-encode non-code files.

    If file_ids is None, processes all doc/config files.
    Returns number of chunks encoded.
    """
    encoder = _get_encoder()
    chunks_encoded = 0

    with db.session() as session:
        query = select(File).where(
            col(File.language_family).in_(list(_DOC_FAMILIES))
        )
        if file_ids is not None:
            query = query.where(col(File.id).in_(file_ids))
        doc_files = list(session.exec(query).all())

    if not doc_files:
        return 0

    log.info("doc_chunks.encode_start", extra={"n_files": len(doc_files)})
    t0 = time.monotonic()

    with db.session() as session:
        for f in doc_files:
            if f.id is None:
                continue

            # Read file content from the stored text
            # File content is not stored in DB — read from disk
            try:
                content = _read_file_content(db, f)
            except (OSError, ValueError, RuntimeError):
                log.debug("doc_chunk_read_failed", exc_info=True)
                continue

            if not content or len(content.strip()) < MIN_CHUNK_LENGTH:
                continue

            chunks = chunk_file(content, f.language_family)
            if not chunks:
                continue

            # Clear existing chunks for this file
            existing = list(session.exec(
                select(FileChunkVec).where(FileChunkVec.file_id == f.id)
            ).all())
            for e in existing:
                session.delete(e)
            session.flush()

            # Encode chunks
            texts = [c[1] for c in chunks]
            vectors = encoder.encode_documents(texts)

            for (key, text, start, end), vec in zip(chunks, vectors):
                if not vec:  # Empty vector
                    continue
                session.add(FileChunkVec(
                    file_id=f.id,
                    chunk_key=key,
                    chunk_text=text,
                    start_line=start,
                    end_line=end,
                    vector_json=_vec_to_json(vec),
                    model_version=MODEL_VERSION,
                ))
                chunks_encoded += 1

            if chunks_encoded % 100 == 0:
                session.commit()

        session.commit()

    elapsed = time.monotonic() - t0
    log.info("doc_chunks.encode_done", extra={"chunks": chunks_encoded,
             "elapsed_s": round(elapsed, 1)})
    return chunks_encoded


def link_doc_chunks_to_defs(
    db: Database,
    *,
    file_ids: list[int] | None = None,
    sigma_floor: float = SIGMA_DOC_FLOOR,
    max_per_chunk: int = MAX_EDGES_PER_CHUNK,
) -> int:
    """Compute doc-chunk → code-def edges via SPLADE dot product.

    When *file_ids* is given, only re-link chunks belonging to those files
    (incremental mode) and only delete/replace their edges.  Otherwise
    re-links all chunks globally.

    Returns number of edges written.
    """
    # Load all def vectors (uses binary cache)
    all_vecs = load_all_vectors_fast(db)
    with db.session() as session:
        if file_ids is not None:
            chunk_rows = list(session.exec(
                select(FileChunkVec).where(col(FileChunkVec.file_id).in_(file_ids))
            ).all())
        else:
            chunk_rows = list(session.exec(select(FileChunkVec)).all())

    if not all_vecs or not chunk_rows:
        return 0

    log.info("doc_chunks.link_start", extra={"n_chunks": len(chunk_rows),
             "n_defs": len(all_vecs)})
    t0 = time.monotonic()

    # Parse vectors
    def_vecs = list(all_vecs.items())
    edges_written = 0

    with db.session() as session:
        # Clear existing edges (scoped to affected files when incremental)
        if file_ids is not None:
            existing_edges = session.exec(
                select(DocCodeEdgeFact).where(col(DocCodeEdgeFact.file_id).in_(file_ids))
            ).all()
        else:
            existing_edges = session.exec(
                select(DocCodeEdgeFact)
            ).all()
        for e in existing_edges:
            session.delete(e)
        session.flush()

        batch: list[DocCodeEdgeFact] = []

        for chunk in chunk_rows:
            chunk_vec = _json_to_vec(chunk.vector_json)
            if not chunk_vec:
                continue

            # Score against all defs
            scored: list[tuple[str, float]] = []
            for def_uid, dv in def_vecs:
                score = sparse_dot(chunk_vec, dv)
                if score >= sigma_floor:
                    scored.append((def_uid, score))

            scored.sort(key=lambda x: -x[1])
            for def_uid, score in scored[:max_per_chunk]:
                batch.append(DocCodeEdgeFact(
                    file_id=chunk.file_id,
                    chunk_key=chunk.chunk_key,
                    target_def_uid=def_uid,
                    score=round(score, 3),
                    model_version=MODEL_VERSION,
                ))
                edges_written += 1

                if len(batch) >= DB_FLUSH_BATCH_SIZE:
                    session.add_all(batch)
                    session.commit()
                    batch.clear()

        if batch:
            session.add_all(batch)
            session.commit()

    elapsed = time.monotonic() - t0
    log.info("doc_chunks.link_done", extra={"edges": edges_written,
             "elapsed_s": round(elapsed, 1)})
    return edges_written


def _read_file_content(db: Database, f: File) -> str | None:
    """Read file content from disk via the worktree path."""
    from pathlib import Path

    from coderecon.index.models import Worktree

    with db.session() as session:
        wt = session.get(Worktree, f.worktree_id)
        if wt is None:
            return None
        full_path = Path(wt.root_path) / f.path
        if not full_path.is_file():
            return None
        try:
            return full_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
