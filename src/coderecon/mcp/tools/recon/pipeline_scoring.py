"""Snippet reading and cross-encoder scoring for the recon pipeline."""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from coderecon.index import Database

log = structlog.get_logger(__name__)


def _read_snippet(repo_root: Path, path: str, start_line: int, end_line: int) -> str | None:
    """Read lines from a file. Returns None if file doesn't exist or read fails."""
    full = repo_root / path
    if not full.exists():
        return None
    try:
        lines = full.read_text(encoding="utf-8", errors="replace").splitlines()
        start = max(0, start_line - 1)
        end = min(len(lines), end_line)
        return "\n".join(lines[start:end])
    except OSError:
        log.debug("read_lines_failed", exc_info=True)
        return None

def _read_signature(repo_root: Path, path: str, start_line: int, end_line: int) -> str | None:
    """Read just the first line (signature) + docstring of a def."""
    full = repo_root / path
    if not full.exists():
        return None
    try:
        lines = full.read_text(encoding="utf-8", errors="replace").splitlines()
        start = max(0, start_line - 1)
        end = min(len(lines), end_line)
        span = lines[start:end]
        if not span:
            return None
        sig_lines = [span[0]]
        for ln in span[1:]:
            stripped = ln.strip()
            if not stripped or stripped.startswith('"""') or stripped.startswith("'''"):
                sig_lines.append(ln)
                if stripped.startswith('"""') or stripped.startswith("'''"):
                    if stripped.count('"""') >= 2 or stripped.count("'''") >= 2:
                        break
                    for ln2 in span[len(sig_lines):]:
                        sig_lines.append(ln2)
                        if '"""' in ln2 or "'''" in ln2:
                            break
                break
            if stripped.endswith((":", "{", "->", ",")):
                sig_lines.append(ln)
            else:
                break
        return "\n".join(sig_lines[:10])
    except OSError:
        log.debug("read_signature_failed", exc_info=True)
        return None

def _fetch_scaffolds(
    candidates: list[dict[str, Any]],
    db: Database,
) -> dict[str, str]:
    """Fetch stored scaffold texts for candidates from SpladeVec table."""
    from coderecon.index.models import SpladeVec
    def_uids = [c["def_uid"] for c in candidates if c.get("def_uid")]
    if not def_uids:
        return {}
    try:
        with db.session() as session:
            from sqlmodel import col, select
            rows = list(session.exec(
                select(SpladeVec.def_uid, SpladeVec.scaffold_text)
                .where(col(SpladeVec.def_uid).in_(def_uids))
            ).all())
            return {uid: txt for uid, txt in rows if txt}
    except (ImportError, OSError):
        log.warning("cross_encoder.scaffold_lookup_failed", exc_info=True)
        return {}

def _build_ce_documents(
    candidates: list[dict[str, Any]],
    scaffolds: dict[str, str],
) -> list[str]:
    """Build CE input documents from stored scaffolds with metadata fallback."""
    documents = []
    for c in candidates:
        scaffold = scaffolds.get(c.get("def_uid", ""))
        if scaffold:
            documents.append(scaffold)
        else:
            parts = [c.get("path", ""), f"{c.get('kind', '')} {c.get('name', '')}"]
            documents.append("\n".join(parts))
    return documents

def _score_cross_encoder_tiny(
    candidates: list[dict[str, Any]],
    task: str,
    db: Database,
) -> list[dict[str, Any]]:
    """Run TinyBERT cross-encoder on ALL candidates before file pruning.
    Attaches ce_score_tiny to each candidate.  Also computes per-file
    aggregates (max/mean) so the file ranker can use CE signal.
    """
    if not candidates:
        return candidates
    try:
        from coderecon.ranking.cross_encoder import get_tiny_scorer
    except ImportError:
        log.debug("cross_encoder_tiny.unavailable", exc_info=True)
        return candidates
    scaffolds = _fetch_scaffolds(candidates, db)
    documents = _build_ce_documents(candidates, scaffolds)
    try:
        scorer = get_tiny_scorer()
        scores = scorer.score_pairs(task, documents)
        for c, s in zip(candidates, scores):
            c["ce_score_tiny"] = float(s)
    except (ValueError, RuntimeError):
        log.warning("cross_encoder_tiny.scoring_failed", exc_info=True)
        return candidates
    from collections import defaultdict
    file_scores: dict[str, list[float]] = defaultdict(list)
    for c in candidates:
        if "ce_score_tiny" in c:
            file_scores[c.get("path", "")].append(c["ce_score_tiny"])
    for c in candidates:
        path = c.get("path", "")
        fs = file_scores.get(path)
        if fs:
            c["ce_tiny_file_max"] = max(fs)
            c["ce_tiny_file_mean"] = sum(fs) / len(fs)
    return candidates

def _score_cross_encoder(
    candidates: list[dict[str, Any]],
    task: str,
    db: Database,
) -> list[dict[str, Any]]:
    """Run cross-encoder on filtered candidates and attach ce_score.
    Reads pre-built scaffold text from the SpladeVec table (persisted
    at index time).  scaffold_text is guaranteed to be populated by the
    consistency backfill system — no on-the-fly rebuilding.
    """
    if not candidates:
        return candidates
    try:
        from coderecon.ranking.cross_encoder import get_scorer
    except ImportError:
        log.debug("cross_encoder.unavailable", exc_info=True)
        return candidates
    scaffolds = _fetch_scaffolds(candidates, db)
    documents = _build_ce_documents(candidates, scaffolds)
    try:
        scorer = get_scorer()
        scores = scorer.score_pairs(task, documents)
        for c, s in zip(candidates, scores):
            c["ce_score"] = float(s)
    except (ValueError, RuntimeError):
        log.warning("cross_encoder.scoring_failed", exc_info=True)
    return candidates
