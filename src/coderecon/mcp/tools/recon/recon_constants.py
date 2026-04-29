"""Recon constants, stop-words, file classifiers, and intent extraction.

Pure data + pure functions.  No I/O, no database access, no async.
Extracted from models.py to keep that module focused on dataclasses.
"""

from __future__ import annotations

import re
from enum import StrEnum
from pathlib import PurePosixPath

_INTERNAL_DEPTH = 2  # Graph expansion depth (backend-decided, not agent-facing)

_BARREL_FILENAMES = frozenset(
    {
        "__init__.py",
        "index.js",
        "index.ts",
        "index.tsx",
        "index.jsx",
        "index.mjs",
        "mod.rs",
    }
)

_STOP_WORDS = frozenset(
    {
        "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
        "have", "has", "had", "do", "does", "did", "will", "would", "could",
        "should", "may", "might", "shall", "can", "need", "must",
        "in", "on", "at", "to", "for", "of", "with", "by", "from", "as",
        "into", "through", "during", "before", "after", "above", "below",
        "between", "under", "over",
        "and", "or", "but", "not", "no", "nor", "so", "yet", "both", "either",
        "if", "then", "else", "when", "where", "how", "what", "which", "who",
        "that", "this", "these", "those", "it", "its", "i", "we", "you",
        "they", "me", "my", "our", "your", "his", "her",
        "all", "each", "every", "any", "some", "such", "only", "also", "very",
        "just", "more",
        "add", "fix", "implement", "change", "update", "modify", "create",
        "make", "use", "get", "set", "new", "code", "file", "method",
        "function", "class", "module", "test", "check", "ensure", "want",
        "like", "about", "etc", "using", "way", "thing", "tool", "run",
    }
)

_PATH_EXTENSIONS = frozenset(
    {
        ".py", ".js", ".ts", ".jsx", ".tsx", ".java", ".go", ".rs",
        ".c", ".cpp", ".h", ".hpp", ".rb", ".php", ".cs", ".swift",
        ".kt", ".scala", ".lua", ".r", ".m", ".mm",
        ".sh", ".bash", ".zsh",
        ".yaml", ".yml", ".json", ".toml", ".cfg", ".ini", ".xml",
    }
)

_CONFIG_EXTENSIONS = frozenset(
    {".yaml", ".yml", ".json", ".toml", ".cfg", ".ini", ".xml", ".env", ".properties"}
)

_DOC_EXTENSIONS = frozenset({".md", ".rst", ".txt", ".adoc"})

_BUILD_FILES = frozenset(
    {
        "Makefile", "CMakeLists.txt", "Dockerfile",
        "docker-compose.yml", "docker-compose.yaml",
        "Jenkinsfile", "Taskfile.yml",
    }
)

_PATH_STOP_TOKENS = frozenset(
    {
        "src", "test", "tests", "config", "models", "utils", "core", "cli",
        "docs", "init", "main", "base", "common", "tools", "commands",
        "templates", "integration", "lib", "internal", "helpers", "types",
        "api", "app", "pkg",
    }
)

class ArtifactKind(StrEnum):
    """Classification of what kind of artifact a definition belongs to."""
    code = "code"
    test = "test"
    config = "config"
    doc = "doc"
    build = "build"

class TaskIntent(StrEnum):
    """High-level classification of what the user wants to do."""
    debug = "debug"
    implement = "implement"
    refactor = "refactor"
    understand = "understand"
    test = "test"
    unknown = "unknown"

_INTENT_KEYWORDS: dict[TaskIntent, frozenset[str]] = {
    TaskIntent.debug: frozenset(
        {"bug", "fix", "error", "crash", "broken", "fail", "failing", "wrong",
         "issue", "debug", "trace", "traceback", "exception", "stacktrace",
         "investigate", "diagnose"}
    ),
    TaskIntent.implement: frozenset(
        {"add", "implement", "create", "build", "introduce", "support",
         "feature", "extend", "enable", "integrate", "wire"}
    ),
    TaskIntent.refactor: frozenset(
        {"refactor", "rename", "move", "extract", "split", "merge",
         "consolidate", "simplify", "clean", "reorganize", "restructure",
         "decouple", "inline"}
    ),
    TaskIntent.understand: frozenset(
        {"understand", "explain", "how", "what", "where", "why", "find",
         "locate", "show", "describe", "document", "reads", "overview",
         "architecture"}
    ),
    TaskIntent.test: frozenset(
        {"test", "tests", "testing", "coverage", "spec", "assertion", "mock",
         "fixture", "pytest", "unittest"}
    ),
}

def _is_test_file(path: str) -> bool:
    """Check if a file path points to a test file."""
    parts = path.split("/")
    basename = parts[-1] if parts else ""
    return (
        any(p in ("tests", "test") for p in parts[:-1])
        or basename.startswith("test_")
        or basename.endswith("_test.py")
    )

def _is_barrel_file(path: str) -> bool:
    """Check if a file is a barrel/index re-export file."""
    name = PurePosixPath(path).name
    return name in _BARREL_FILENAMES

def _classify_artifact(path: str) -> ArtifactKind:
    """Classify a file path into an ArtifactKind."""
    name = PurePosixPath(path).name
    suffix = PurePosixPath(path).suffix.lower()
    if _is_test_file(path):
        return ArtifactKind.test
    if name in _BUILD_FILES or name == "pyproject.toml":
        return ArtifactKind.build
    if suffix in _CONFIG_EXTENSIONS:
        return ArtifactKind.config
    if suffix in _DOC_EXTENSIONS:
        return ArtifactKind.doc
    return ArtifactKind.code

def _extract_intent(task: str) -> TaskIntent:
    """Extract the most likely intent from a task description."""
    words = set(re.split(r"[^a-zA-Z]+", task.lower()))
    best_intent = TaskIntent.unknown
    best_count = 0
    for intent, keywords in _INTENT_KEYWORDS.items():
        count = len(words & keywords)
        if count > best_count:
            best_count = count
            best_intent = intent
    return best_intent
