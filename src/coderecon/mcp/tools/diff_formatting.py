"""Diff result formatting and domain classification."""

from __future__ import annotations

import re
from dataclasses import asdict
from typing import Any

from coderecon.git.models import _DELTA_CHAR_MAP
from coderecon.index._internal.diff.models import (
    SemanticDiffResult,
    StructuralChange,
)

# Domain Classification

# Default: group by first 2 path segments (e.g. src/coderecon → "src/coderecon")
_DOMAIN_PREFIX_DEPTH = 2

def _domain_key(path: str) -> str:
    """Derive a domain key from a file path.
    Uses the first ``_DOMAIN_PREFIX_DEPTH`` path segments as the domain.
    Falls back to the directory name for shallow paths.
    """
    parts = path.split("/")
    if len(parts) <= _DOMAIN_PREFIX_DEPTH:
        return "/".join(parts[:-1]) if len(parts) > 1 else "(root)"
    return "/".join(parts[:_DOMAIN_PREFIX_DEPTH])

def _classify_domains(
    changes: list[StructuralChange],
    non_structural: list[Any] | None = None,
) -> list[dict[str, Any]]:
    """Group structural changes by directory-prefix domain.
    Each domain dict contains:
      - name: human-readable domain label (directory prefix)
      - root_path: common path prefix for files in this domain
      - files: unique file paths in this domain
      - change_count: total structural changes
      - breaking_count: changes with structural_severity == "breaking"
      - high_risk_count: body changes with risk in ("high", "medium")
      - additions: count of added symbols
      - removals: count of removed symbols
      - review_priority: int (1 = most critical, higher = less critical)
    """
    # Group changes by domain key
    domain_changes: dict[str, list[StructuralChange]] = {}
    domain_files: dict[str, set[str]] = {}
    for c in changes:
        key = _domain_key(c.path)
        domain_changes.setdefault(key, []).append(c)
        domain_files.setdefault(key, set()).add(c.path)
    # Also include non-structural files in domain file sets
    if non_structural:
        for f in non_structural:
            path = f.path if hasattr(f, "path") else f.get("path", "")
            if path:
                key = _domain_key(path)
                domain_files.setdefault(key, set()).add(path)
    # Build domain dicts with risk metrics
    domains: list[dict[str, Any]] = []
    for key in sorted(domain_changes):
        dcs = domain_changes[key]
        breaking = sum(1 for c in dcs if c.structural_severity == "breaking")
        high_risk = sum(
            1
            for c in dcs
            if c.change == "body_changed" and c.behavior_change_risk in ("high", "medium")
        )
        additions = sum(1 for c in dcs if c.change == "added")
        removals = sum(1 for c in dcs if c.change == "removed")
        domains.append(
            {
                "name": key,
                "root_path": key,
                "files": sorted(domain_files.get(key, set())),
                "change_count": len(dcs),
                "breaking_count": breaking,
                "high_risk_count": high_risk,
                "additions": additions,
                "removals": removals,
            }
        )
    # Assign review priority: breaking first, then high-risk, then by count
    domains.sort(key=lambda d: (-d["breaking_count"], -d["high_risk_count"], -d["change_count"]))
    for i, d in enumerate(domains, 1):
        d["review_priority"] = i
    return domains

def _detect_cross_domain_edges(
    changes: list[StructuralChange],
    domains: list[dict[str, Any]],
) -> list[dict[str, str]]:
    """Detect import-based edges between domains.
    Uses the ``impact.importing_files`` data already present on each
    StructuralChange to find cross-domain relationships without any
    additional DB queries.
    """
    # Build file → domain lookup
    file_to_domain: dict[str, str] = {}
    for d in domains:
        for f in d["files"]:
            file_to_domain[f] = d["name"]
    # Changed files set for filtering
    changed_files = {c.path for c in changes}
    edges: set[tuple[str, str]] = set()
    for c in changes:
        src_domain = file_to_domain.get(c.path)
        if not src_domain or not c.impact:
            continue
        importers = c.impact.importing_files or []
        for imp_file in importers:
            imp_domain = file_to_domain.get(imp_file)
            # Only report edges where the importer is also a changed file
            # in a different domain (otherwise it's just normal dependency)
            if imp_domain and imp_domain != src_domain and imp_file in changed_files:
                edges.add((src_domain, imp_domain))
    return [{"from_domain": a, "to_domain": b, "relationship": "import"} for a, b in sorted(edges)]

# Agentic Hint

def _build_agentic_hint(
    result: SemanticDiffResult,
    domains: list[dict[str, Any]] | None = None,
    cross_edges: list[dict[str, str]] | None = None,
) -> str:
    """Build action hint for the agent.
    When changes span multiple domains, emits a prioritized review plan.
    For single-domain changes, returns a compact counts summary.
    """
    if not result.structural_changes:
        return "No structural changes detected."
    # Count change types
    sig_changes = sum(1 for c in result.structural_changes if c.change == "signature_changed")
    removals = sum(1 for c in result.structural_changes if c.change == "removed")
    body_changes = [c for c in result.structural_changes if c.change == "body_changed"]
    high_risk = sum(1 for c in body_changes if c.behavior_change_risk in ("high", "medium"))
    additions = sum(1 for c in result.structural_changes if c.change == "added")
    # Count affected tests
    all_test_files: set[str] = set()
    for c in result.structural_changes:
        if c.impact and c.impact.affected_test_files:
            all_test_files.update(c.impact.affected_test_files)
    # Build compact counts line
    count_parts: list[str] = []
    if sig_changes:
        count_parts.append(f"{sig_changes} signature changes")
    if removals:
        count_parts.append(f"{removals} removals")
    if body_changes:
        risk_note = f" ({high_risk} high-risk)" if high_risk else ""
        count_parts.append(f"{len(body_changes)} body changes{risk_note}")
    if additions:
        count_parts.append(f"{additions} additions")
    if not count_parts:
        return "No actionable changes."
    counts_line = ", ".join(count_parts) + "."
    if all_test_files:
        counts_line += f" {len(all_test_files)} affected test files."
    # Single domain or no domain info → compact hint
    if not domains or len(domains) <= 1:
        return counts_line
    # Multi-domain → structured review plan
    lines: list[str] = []
    lines.append(f"REVIEW PLAN: {len(domains)} domains, {counts_line}")
    lines.append("")
    lines.append("Priority order (review breaking/high-risk first):")
    for d in domains:
        priority = d["review_priority"]
        risk_parts: list[str] = []
        if d["breaking_count"]:
            risk_parts.append(f"{d['breaking_count']} BREAKING")
        if d["high_risk_count"]:
            risk_parts.append(f"{d['high_risk_count']} high-risk")
        risk_str = f" ({', '.join(risk_parts)})" if risk_parts else ""
        files_str = ", ".join(f.split("/")[-1] for f in d["files"][:5])
        if len(d["files"]) > 5:
            files_str += f" +{len(d['files']) - 5} more"
        lines.append("")
        lines.append(f"{priority}. {d['name']} — {d['change_count']} changes{risk_str}")
        lines.append(f"   Files: {files_str}")
        lines.append(
            f'   → recon(task="review {d["name"]} changes", '
            f"read_only=True, pinned_paths={d['files'][:8]})"
        )
    if cross_edges:
        lines.append("")
        lines.append("CROSS-DOMAIN EDGES (verify interface compatibility):")
        for edge in cross_edges:
            lines.append(f"  {edge['from_domain']} → {edge['to_domain']}")
    lines.append("")
    lines.append(
        "WORKFLOW: For each domain above, call recon(read_only=True, "
        "pinned_paths=...) to get context, then read changed files via "
        "terminal (cat/head). Focus on breaking changes and high-risk body changes."
    )
    return "\n".join(lines)

def _result_to_text(result: SemanticDiffResult) -> dict[str, Any]:
    """Convert SemanticDiffResult to losslessly-compressed text format.
    Structural changes are rendered as flat text lines with three compression
    strategies applied:
    1. **Nested path dedup** — nested entries (methods inside a class) omit the
       file path and show only ``:start-end`` since the parent already carries it.
    2. **risk:unknown omitted** — the default risk level is dropped; a header
       comment documents the convention.
    3. **Test aliases** — test file paths that appear 3+ times are replaced with
       short aliases (``t1``, ``t2``, …) defined in a header line.
    Format per top-level change:
        {change} {kind} {name}  {path}:{start}-{end}  Δ{lines}  risk:{risk}  refs:{N}  tests:{list}
    Format per nested change:
        {change} {kind} {name}  :{start}-{end}  Δ{lines}  …
    """
    from collections import Counter
    from coderecon.mcp.tools.index import _change_to_text
    domains = _classify_domains(result.structural_changes, result.non_structural_changes)
    cross_edges = _detect_cross_domain_edges(result.structural_changes, domains)
    agentic_hint = _build_agentic_hint(result, domains, cross_edges)
    raw_lines: list[str] = []
    for c in result.structural_changes:
        raw_lines.extend(_change_to_text(c))
    test_counter: Counter[str] = Counter()
    for line in raw_lines:
        idx = line.find("tests:")
        if idx != -1:
            tests_str = line[idx + 6 :].split("  ")[0]  # stop at next double-space field
            for t in tests_str.split(","):
                if t:
                    test_counter[t] += 1
    aliases: dict[str, str] = {}
    alias_idx = 0
    for test_path, count in test_counter.most_common():
        if count >= 3 and (len(test_path) - 2) * count > 50:
            alias_idx += 1
            aliases[test_path] = f"t{alias_idx}"
    structural_lines: list[str] = []
    if raw_lines:
        # Header comments (lossless documentation)
        structural_lines.append("# entries without risk: have unknown risk")
        if aliases:
            alias_defs = ", ".join(f"{v}={k}" for k, v in aliases.items())
            structural_lines.append(f"# test aliases: {alias_defs}")
    for line in raw_lines:
        new_line = line
        # Nested path dedup: indented lines keep only :start-end
        if new_line.startswith("  "):
            m = re.search(r"  ([\w/.]+\.\w+):(\d+-\d+)", new_line)
            if m:
                # Replace "  path/to/file.py:10-20" with "  :10-20"
                new_line = new_line[: m.start()] + "  :" + m.group(2) + new_line[m.end() :]
        # Apply test aliases
        for test_path, alias in aliases.items():
            new_line = new_line.replace(test_path, alias)
        structural_lines.append(new_line)
    non_structural_lines: list[str] = []
    for f in result.non_structural_changes:
        parts = [f"{f.status} {f.path}  {f.category}"]
        if f.language:
            parts.append(f"  {f.language}")
        non_structural_lines.append("".join(parts))
    response: dict[str, Any] = {
        "summary": result.summary,
        "breaking_summary": result.breaking_summary,
        "files_analyzed": result.files_analyzed,
        "base": result.base_description,
        "target": result.target_description,
        "structural_changes": structural_lines,
        "non_structural_changes": non_structural_lines,
        "agentic_hint": agentic_hint,
    }
    if len(domains) > 1:
        response["domains"] = domains
        if cross_edges:
            response["cross_domain_edges"] = cross_edges
    if result.scope:
        scope_d = {k: v for k, v in asdict(result.scope).items() if v is not None}
        response["scope"] = scope_d
    return response

def _result_to_dict(
    result: SemanticDiffResult,
    *,
    verbosity: Literal["full", "standard", "minimal"] = "full",
) -> dict[str, Any]:
    """Convert SemanticDiffResult to a serializable dict.
    Verbosity levels:
    - full: Everything (default)
    - standard: Omit change_preview
    - minimal: Just path/kind/name/change (no impact, nested_changes, signatures, etc.)
    """
    def _change_to_dict(c: StructuralChange) -> dict[str, Any]:
        # Minimal: just essential fields
        if verbosity == "minimal":
            return {
                "path": c.path,
                "kind": c.kind,
                "name": c.name,
                "change": c.change,
            }
        d: dict[str, Any] = {
            "path": c.path,
            "kind": c.kind,
            "name": c.name,
            "change": c.change,
            "structural_severity": c.structural_severity,
            "behavior_change_risk": c.behavior_change_risk,
            "classification_confidence": c.classification_confidence,
        }
        # Schema invariant: risk_basis present when risk != low
        if c.risk_basis:
            d["risk_basis"] = c.risk_basis
        elif c.behavior_change_risk != "low":
            d["risk_basis"] = "unclassified_change"
        if c.qualified_name:
            d["qualified_name"] = c.qualified_name
        if c.entity_id:
            d["entity_id"] = c.entity_id
        # Rename-specific fields for correlation
        if c.change == "renamed":
            if c.old_name:
                d["old_name"] = c.old_name
            if c.previous_entity_id:
                d["previous_entity_id"] = c.previous_entity_id
        # Schema invariant: signature_changed requires both sigs
        if c.change == "signature_changed":
            d["old_signature"] = c.old_sig or ""
            d["new_signature"] = c.new_sig or ""
        else:
            if c.old_sig:
                d["old_signature"] = c.old_sig
            if c.new_sig:
                d["new_signature"] = c.new_sig
        if c.start_line:
            d["start_line"] = c.start_line
            if c.start_col:
                d["start_col"] = c.start_col
        if c.end_line:
            d["end_line"] = c.end_line
            if c.end_col:
                d["end_col"] = c.end_col
        # Schema invariant: body_changed requires lines_changed
        if c.change == "body_changed":
            d["lines_changed"] = c.lines_changed if c.lines_changed is not None else 0
        elif c.lines_changed is not None:
            d["lines_changed"] = c.lines_changed
        if c.delta_tags:
            d["delta_tags"] = c.delta_tags
        # Omit change_preview in standard mode (saves ~50% for body_changed)
        if c.change_preview and verbosity == "full":
            d["change_preview"] = c.change_preview
        if c.impact:
            impact_d: dict[str, Any] = {}
            for k, v in asdict(c.impact).items():
                if v is None:
                    continue
                if k == "ref_tiers" and v is not None:
                    # RefTierBreakdown is a dataclass; asdict already made it a dict
                    impact_d[k] = v
                else:
                    impact_d[k] = v
            d["impact"] = impact_d
        if c.nested_changes:
            d["nested_changes"] = [_change_to_dict(nc) for nc in c.nested_changes]
        return d
    agentic_hint = _build_agentic_hint(result)
    structural_items = [_change_to_dict(c) for c in result.structural_changes]
    non_structural_items = [asdict(f) for f in result.non_structural_changes]
    response: dict[str, Any] = {
        "summary": result.summary,
        "breaking_summary": result.breaking_summary,
        "files_analyzed": result.files_analyzed,
        "base": result.base_description,
        "target": result.target_description,
        "structural_changes": structural_items,
        "non_structural_changes": non_structural_items,
        **(
            {"scope": {k: v for k, v in asdict(result.scope).items() if v is not None}}
            if result.scope
            else {}
        ),
        "agentic_hint": agentic_hint,
    }
    return response
