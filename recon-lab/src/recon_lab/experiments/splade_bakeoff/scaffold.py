"""Anglicized scaffold builder for SPLADE sparse retrieval.

Restored from commit b4cd167^ (def_embedding.py / file_embedding.py),
simplified: no truncation budget, no greedy priority fill.

Each DefFact produces one untruncated scaffold. Field order follows
measured marginal recall contribution from the original bge-small
ablation (calls +4.8%, strings +1.5%, sig +0.7%, doc +0.2%).
Decorators excluded (measured -0.4%).
"""

from __future__ import annotations

import re
from typing import Any

# ── Identifier splitting ─────────────────────────────────────────

_CAMEL_SPLIT = re.compile(r"[A-Z]?[a-z]+|[A-Z]+(?=[A-Z][a-z]|\d|\b)|[0-9]+")


def word_split(name: str) -> list[str]:
    """Split an identifier into lowercase natural words.

    Handles camelCase, PascalCase, snake_case, and mixed styles.
    ``getUserById`` → ``["get", "user", "by", "id"]``
    """
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


def path_to_phrase(file_path: str) -> str:
    """Convert a file path into a natural-language phrase.

    ``src/auth/middleware/rate_limiter.py`` → ``"auth middleware rate limiter"``
    """
    p = file_path.replace("\\", "/")
    for prefix in ("src/", "lib/", "app/", "pkg/", "internal/"):
        if p.startswith(prefix):
            p = p[len(prefix):]
            break
    dot = p.rfind(".")
    if dot > 0:
        p = p[:dot]
    parts: list[str] = []
    for segment in p.split("/"):
        parts.extend(word_split(segment))
    return " ".join(parts)


def compact_sig(name: str, sig: str) -> str:
    """Build a compact anglicized signature.

    Strips ``self`` and returns e.g. ``"check rate(request, limit)"``.
    """
    words = " ".join(word_split(name))
    if sig:
        compact = sig.replace("self, ", "").replace("self,", "").replace("self", "")
        if compact and compact != "()":
            return f"{words}{compact}"
    return words


# ── Code-def kinds ───────────────────────────────────────────────

_CODE_KINDS = frozenset({
    "function", "method", "class", "struct", "interface",
    "trait", "enum", "property", "constant", "variable", "module",
})

# ── Config-file scaffold lines ───────────────────────────────────

_CONFIG_SKIP_NAMES = frozenset({
    ".PHONY", ".DEFAULT_GOAL", ".SUFFIXES", ".PRECIOUS",
    ".INTERMEDIATE", ".SECONDARY", ".DELETE_ON_ERROR",
})


def _build_config_defines(defs: list[dict[str, Any]]) -> list[str]:
    """Build scaffold lines for config-file def kinds."""
    targets: list[str] = []
    sections: list[str] = []
    config_keys: list[str] = []
    headings: list[str] = []
    variables: list[str] = []
    seen: set[str] = set()

    for d in defs:
        kind = d.get("kind", "")
        name = d.get("name", "")
        if not name or name in _CONFIG_SKIP_NAMES:
            continue
        key = f"{kind}:{name}"
        if key in seen:
            continue
        seen.add(key)
        words = " ".join(word_split(name))
        if not words:
            continue
        if kind == "target":
            targets.append(words)
        elif kind == "table":
            words = " ".join(word_split(name.replace(".", "_")))
            sections.append(words)
        elif kind in ("pair", "key"):
            config_keys.append(words)
        elif kind == "heading":
            clean = re.sub(r"^\d+\.\s*", "", name).strip()
            if clean:
                headings.append(" ".join(word_split(clean)))
        elif kind == "variable":
            variables.append(words)

    lines: list[str] = []
    if targets:
        lines.append(f"targets {', '.join(targets)}")
    if sections:
        lines.append(f"sections {', '.join(sections)}")
    if config_keys:
        lines.append(f"configures {', '.join(config_keys)}")
    if headings:
        lines.append(f"topics {', '.join(headings)}")
    if variables:
        lines.append(f"variables {', '.join(variables)}")
    return lines


# ── Per-def scaffold (untruncated) ───────────────────────────────


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
    string_literals: list[str] | None = None,
) -> str:
    """Build an anglicized scaffold for a single DefFact. No truncation.

    Fields are present unconditionally. Order follows measured marginal
    recall contribution from bge-small ablation:
        name (baseline) > calls (+4.8%) > uses (new) > mentions (+1.5%)
        > sig (+0.7%) > doc (+0.2%).
    Decorators excluded (-0.4%).
    """
    if not name:
        return ""

    lines: list[str] = []

    # Module context
    path_phrase = path_to_phrase(file_path)
    if path_phrase:
        lines.append(f"module {path_phrase}")

    # Kind + signature (or just kind + name)
    sig = signature_text or ""
    if sig:
        lines.append(f"{kind} {compact_sig(name, sig)}")
    else:
        lines.append(f"{kind} {' '.join(word_split(name))}")

    # Parent class context
    qualified = qualified_name or lexical_path or ""
    if qualified and "." in qualified:
        parent = qualified.rsplit(".", 1)[0]
        parent_words = " ".join(word_split(parent))
        if parent_words:
            lines.append(f"in {parent_words}")

    # Callees (measured +4.8%)
    if callee_names:
        sorted_calls = sorted({c for c in callee_names if c and len(c) >= 2})
        if sorted_calls:
            lines.append(f"calls {', '.join(sorted_calls)}")

    # Type references (new, unmeasured — ablation target)
    if type_ref_names:
        # Deduplicate against callee_names
        callee_set = set(callee_names or [])
        unique_refs = sorted({r for r in type_ref_names if r and r not in callee_set})
        if unique_refs:
            lines.append(f"uses {', '.join(unique_refs)}")

    # String literals (measured +1.5%)
    if string_literals:
        clean = [
            lit.strip()
            for lit in string_literals
            if lit.strip().lower() not in ("true", "false", "none", "", "0", "1")
            and len(lit.strip()) >= 3
        ]
        if clean:
            lines.append(f"mentions {', '.join(clean)}")

    # Docstring first sentence (measured +0.2%)
    doc = (docstring or "").strip()
    if doc and len(doc) > 15:
        first = doc.split(".")[0].strip() if "." in doc else doc
        if first:
            lines.append(f"describes {first}")

    # Config defs for non-code kinds
    if kind not in _CODE_KINDS:
        config_lines = _build_config_defines([{
            "kind": kind, "name": name,
        }])
        lines.extend(config_lines)

    return "\n".join(lines) if lines else ""


def build_file_header_scaffold(
    file_path: str,
    import_sources: list[str],
    module_docstring: str | None = None,
) -> str:
    """Build a synthetic scaffold for file-level signals.

    Captures module-level information that no individual def owns:
    imports, module docstring.
    """
    lines: list[str] = []

    path_phrase = path_to_phrase(file_path)
    if path_phrase:
        lines.append(f"module {path_phrase}")

    if import_sources:
        seen: set[str] = set()
        unique: list[str] = []
        for src in import_sources:
            words = " ".join(word_split(src.split(".")[-1]))
            if words and words not in seen:
                seen.add(words)
                unique.append(words)
        if unique:
            lines.append(f"imports {', '.join(unique)}")

    doc = (module_docstring or "").strip()
    if doc and len(doc) > 15:
        first = doc.split(".")[0].strip() if "." in doc else doc
        if first:
            lines.append(f"describes {first}")

    return "\n".join(lines) if lines else ""
