"""Lint module - static analysis, type checking, and formatting."""

# Import definitions to register all tools
from coderecon.lint import definitions as _definitions  # noqa: F401
from coderecon.lint.models import Diagnostic, LintResult, Severity, ToolCategory, ToolResult
from coderecon.lint.ops import LintOps
from coderecon.lint.tools import LintTool, registry

__all__ = [
    "Diagnostic",
    "LintResult",
    "LintOps",
    "LintTool",
    "Severity",
    "ToolCategory",
    "ToolResult",
    "registry",
]
