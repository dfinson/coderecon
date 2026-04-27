"""SPLADE scaffold building — identifier splitting and anglicised text generation."""

from __future__ import annotations

import re

import structlog
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session, col, select

from coderecon.index.models import DefFact, File

log = structlog.get_logger(__name__)

# ── Identifier splitting ─────────────────────────────────────────

_CAMEL_SPLIT = re.compile(r"[A-Z]?[a-z]+|[A-Z]+(?=[A-Z][a-z]|\d|\b)|[0-9]+")

def word_split(name: str) -> list[str]:
    """Split camelCase/snake_case into lowercase words."""
    words: list[str] = []
    for part in name.split("_"):
        if not part:
            continue
        camel = _CAMEL_SPLIT.findall(part)
        if camel:
            words.extend(w.lower() for w in camel)
        else:
            words.append(part.lower())
    return words

def _path_to_phrase(file_path: str) -> str:
    """Convert file path to natural-language phrase."""
    p = file_path.replace("\\", "/")
    for prefix in ("src/", "lib/", "app/", "pkg/", "internal/"):
        if p.startswith(prefix):
            p = p[len(prefix) :]
            break
    dot = p.rfind(".")
    if dot > 0:
        p = p[:dot]
    parts: list[str] = []
    for segment in p.split("/"):
        parts.extend(word_split(segment))
    return " ".join(parts)

def _compact_sig(name: str, sig: str) -> str:
    """Build compact anglicised signature."""
    words = " ".join(word_split(name))
    if sig:
        compact = sig.replace("self, ", "").replace("self,", "").replace("self", "")
        if compact and compact != "()":
            return f"{words}{compact}"
    return words

# ── Scaffold builder ─────────────────────────────────────────────

def build_def_scaffold(
    file_path: str,
    *,
    kind: str,
    name: str,
    signature_text: str | None = None,
    qualified_name: str | None = None,
    lexical_path: str | None = None,
    docstring: str | None = None,
    callee_names: list[str] | None = None,
    type_ref_names: list[str] | None = None,
) -> str:
    """Build an anglicised scaffold for a single DefFact.
    Fields present unconditionally.  Order follows measured marginal
    recall contribution from bge-small ablation.
    """
    if not name:
        return ""
    lines: list[str] = []
    path_phrase = _path_to_phrase(file_path)
    if path_phrase:
        lines.append(f"module {path_phrase}")
    sig = signature_text or ""
    if sig:
        lines.append(f"{kind} {_compact_sig(name, sig)}")
    else:
        lines.append(f"{kind} {' '.join(word_split(name))}")
    qualified = qualified_name or lexical_path or ""
    if qualified and "." in qualified:
        parent = qualified.rsplit(".", 1)[0]
        parent_words = " ".join(word_split(parent))
        if parent_words:
            lines.append(f"in {parent_words}")
    if callee_names:
        sorted_calls = sorted({c for c in callee_names if c and len(c) >= 2})
        if sorted_calls:
            lines.append(f"calls {', '.join(sorted_calls)}")
    if type_ref_names:
        callee_set = set(callee_names or [])
        unique_refs = sorted({r for r in type_ref_names if r and r not in callee_set})
        if unique_refs:
            lines.append(f"uses {', '.join(unique_refs)}")
    doc = (docstring or "").strip()
    if doc and len(doc) > 15:
        first = doc.split(".")[0].strip() if "." in doc else doc
        if first:
            lines.append(f"describes {first}")
    return "\n".join(lines) if lines else ""

# ── Scaffold extraction from index DB ─────────────────────────────

def build_scaffolds_for_defs(
    session: Session,
    def_facts: list[DefFact],
) -> dict[str, str]:
    """Build scaffolds for a batch of DefFacts using index data.
    Uses bulk queries for callees and type annotations instead of
    per-def queries — reduces ~2N SQL queries to 2 bulk queries.
    Returns {def_uid: scaffold_text}.
    """
    from coderecon.index.models import RefFact, TypeAnnotationFact
    result: dict[str, str] = {}
    if not def_facts:
        return result
    # Pre-fetch file paths for all defs
    file_ids = list({d.file_id for d in def_facts if d.file_id})
    file_map: dict[int, str] = {}
    if file_ids:
        files = session.exec(
            select(File).where(col(File.id).in_(file_ids))
        ).all()
        file_map = {f.id: f.path for f in files if f.id is not None}
    # ── Bulk callee query ────────────────────────────────────────
    # For each def, find resolved refs whose start_line falls within
    # [def.start_line, def.end_line] in the same file, then join to
    # the target DefFact to get the callee name.
    #
    # Instead of N individual queries, we do one query that returns
    # (caller_def_uid, callee_name, callee_def_uid) for all defs.
    # Build a lookup: (file_id, start_line, end_line) → def_uid
    callees_by_uid: dict[str, list[str]] = {d.def_uid: [] for d in def_facts}
    type_refs_by_uid: dict[str, list[str]] = {d.def_uid: [] for d in def_facts}
    # Process in chunks of file_ids to keep SQL manageable
    for fid_chunk_start in range(0, len(file_ids), 100):
        fid_chunk = file_ids[fid_chunk_start:fid_chunk_start + 100]
        # Get defs in this chunk for range-matching
        chunk_defs = [d for d in def_facts if d.file_id in set(fid_chunk)]
        # Bulk fetch all resolved refs in these files
        refs_with_targets = session.exec(
            select(
                RefFact.file_id,
                RefFact.start_line,
                RefFact.target_def_uid,
            ).where(
                col(RefFact.file_id).in_(fid_chunk),
                RefFact.target_def_uid.is_not(None),  # type: ignore[union-attr]
            )
        ).all()
        # Fetch target def names in bulk
        target_uids = list({r[2] for r in refs_with_targets if r[2]})
        target_names: dict[str, str] = {}
        for uid_start in range(0, len(target_uids), 500):
            uid_batch = target_uids[uid_start:uid_start + 500]
            rows = session.exec(
                select(DefFact.def_uid, DefFact.name).where(
                    col(DefFact.def_uid).in_(uid_batch)
                )
            ).all()
            for uid, name in rows:
                target_names[uid] = name
        # Assign refs to their enclosing defs by range containment
        for d in chunk_defs:
            d_callees: set[str] = set()
            for fid, line, tuid in refs_with_targets:
                if (
                    fid == d.file_id
                    and d.start_line <= line <= d.end_line
                    and tuid != d.def_uid
                    and tuid in target_names
                ):
                    d_callees.add(target_names[tuid])
            callees_by_uid[d.def_uid] = list(d_callees)[:30]
        # Bulk fetch type annotations in these files
        try:
            annotations = session.exec(
                select(
                    TypeAnnotationFact.file_id,
                    TypeAnnotationFact.start_line,
                    TypeAnnotationFact.base_type,
                ).where(
                    col(TypeAnnotationFact.file_id).in_(fid_chunk),
                )
            ).all()
            for d in chunk_defs:
                d_types: set[str] = set()
                for fid, line, btype in annotations:
                    if (
                        fid == d.file_id
                        and btype
                        and d.start_line <= line <= d.end_line
                    ):
                        d_types.add(btype)
                type_refs_by_uid[d.def_uid] = list(d_types)[:20]
        except (SQLAlchemyError, ValueError):
            log.debug("type_annotation_lookup_failed", exc_info=True)
    # ── Build scaffolds ──────────────────────────────────────────
    for d in def_facts:
        file_path = file_map.get(d.file_id, "")
        if not file_path:
            continue
        scaffold = build_def_scaffold(
            file_path,
            kind=d.kind,
            name=d.name,
            signature_text=d.signature_text,
            qualified_name=d.qualified_name,
            lexical_path=d.lexical_path,
            docstring=d.docstring,
            callee_names=callees_by_uid.get(d.def_uid),
            type_ref_names=type_refs_by_uid.get(d.def_uid),
        )
        if scaffold:
            result[d.def_uid] = scaffold
    return result
