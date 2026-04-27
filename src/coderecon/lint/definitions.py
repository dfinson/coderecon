"""Tool definitions - register all supported lint tools."""

from __future__ import annotations

from coderecon.lint import parsers
from coderecon.lint.models import ToolCategory
from coderecon.lint.tools import LintTool, registry

# Python Tools

registry.register(
    LintTool(
        tool_id="python.ruff",
        name="Ruff",
        languages=frozenset({"python"}),
        category=ToolCategory.LINT,
        executable="ruff",
        config_files=["pyproject.toml:tool.ruff", "ruff.toml", ".ruff.toml"],
        check_args=["check", "--output-format=json"],
        fix_args=["check", "--fix", "--output-format=json"],
        dry_run_args=["check", "--diff", "--output-format=json"],
        output_format="json",
        force_exclude_flag="--force-exclude",
    ),
    parser=parsers.parse_ruff,
)

registry.register(
    LintTool(
        tool_id="python.ruff-format",
        name="Ruff Format",
        languages=frozenset({"python"}),
        category=ToolCategory.FORMAT,
        executable="ruff",
        config_files=["pyproject.toml:tool.ruff", "ruff.toml", ".ruff.toml"],
        check_args=["format", "--check", "--diff"],
        fix_args=["format"],
        dry_run_args=["format", "--check", "--diff"],
        output_format="custom",
        force_exclude_flag="--force-exclude",
    ),
    parser=parsers.parse_ruff_format,
)
registry.register(
    LintTool(
        tool_id="python.mypy",
        name="mypy",
        languages=frozenset({"python"}),
        category=ToolCategory.TYPE_CHECK,
        executable="mypy",
        config_files=["pyproject.toml:tool.mypy", "mypy.ini", ".mypy.ini", "setup.cfg"],
        check_args=["--output=json"],
        fix_args=["--output=json"],  # mypy doesn't fix
        dry_run_args=["--output=json"],
        output_format="json",
    ),
    parser=parsers.parse_mypy,
)

registry.register(
    LintTool(
        tool_id="python.pyright",
        name="Pyright",
        languages=frozenset({"python"}),
        category=ToolCategory.TYPE_CHECK,
        executable="pyright",
        config_files=["pyrightconfig.json", "pyproject.toml:tool.pyright"],
        check_args=["--outputjson"],
        fix_args=["--outputjson"],  # pyright doesn't fix
        dry_run_args=["--outputjson"],
        output_format="json",
    ),
    parser=parsers.parse_pyright,
)

registry.register(
    LintTool(
        tool_id="python.bandit",
        name="Bandit",
        languages=frozenset({"python"}),
        category=ToolCategory.SECURITY,
        executable="bandit",
        config_files=["pyproject.toml:tool.bandit", ".bandit", "bandit.yaml"],
        check_args=["-f", "json", "-r"],
        fix_args=["-f", "json", "-r"],  # bandit doesn't fix
        dry_run_args=["-f", "json", "-r"],
        output_format="json",
    ),
    parser=parsers.parse_bandit,
)

registry.register(
    LintTool(
        tool_id="python.black",
        name="Black",
        languages=frozenset({"python"}),
        category=ToolCategory.FORMAT,
        executable="black",
        config_files=["pyproject.toml:tool.black"],
        check_args=["--check", "--diff"],
        fix_args=[],
        dry_run_args=["--check", "--diff"],
        output_format="custom",
        stderr_has_output=True,
    ),
    parser=parsers.parse_black_check,
)

registry.register(
    LintTool(
        tool_id="python.isort",
        name="isort",
        languages=frozenset({"python"}),
        category=ToolCategory.FORMAT,
        executable="isort",
        config_files=["pyproject.toml:tool.isort", ".isort.cfg", "setup.cfg"],
        check_args=["--check", "--diff"],
        fix_args=[],
        dry_run_args=["--check", "--diff"],
        output_format="custom",
    ),
    parser=parsers.parse_gofmt,  # Lists files
)


# JavaScript/TypeScript Tools

registry.register(
    LintTool(
        tool_id="js.eslint",
        name="ESLint",
        languages=frozenset({"javascript", "typescript"}),
        category=ToolCategory.LINT,
        executable="eslint",
        config_files=[
            ".eslintrc.js",
            ".eslintrc.cjs",
            ".eslintrc.json",
            ".eslintrc.yml",
            ".eslintrc.yaml",
            "eslint.config.js",
            "eslint.config.mjs",
        ],
        check_args=["--format=json"],
        fix_args=["--fix", "--format=json"],
        dry_run_args=["--format=json"],
        output_format="json",
    ),
    parser=parsers.parse_eslint,
)

registry.register(
    LintTool(
        tool_id="js.tsc",
        name="TypeScript Compiler",
        languages=frozenset({"typescript"}),
        category=ToolCategory.TYPE_CHECK,
        executable="tsc",
        config_files=["tsconfig.json"],
        check_args=["--noEmit"],
        fix_args=["--noEmit"],  # tsc doesn't fix
        dry_run_args=["--noEmit"],
        output_format="custom",
        paths_position="none",  # tsc uses tsconfig
    ),
    parser=parsers.parse_tsc,
)

registry.register(
    LintTool(
        tool_id="js.prettier",
        name="Prettier",
        languages=frozenset(
            {"javascript", "typescript", "json", "css", "html", "markdown", "yaml"}
        ),
        category=ToolCategory.FORMAT,
        executable="prettier",
        config_files=[
            ".prettierrc",
            ".prettierrc.js",
            ".prettierrc.json",
            ".prettierrc.yml",
            ".prettierrc.yaml",
            "prettier.config.js",
            "prettier.config.mjs",
        ],
        check_args=["--check"],
        fix_args=["--write"],
        dry_run_args=["--check"],
        output_format="custom",
    ),
    parser=parsers.parse_prettier_check,
)

registry.register(
    LintTool(
        tool_id="js.biome",
        name="Biome",
        languages=frozenset({"javascript", "typescript", "json"}),
        category=ToolCategory.LINT,
        executable="biome",
        config_files=["biome.json", "biome.jsonc"],
        check_args=["lint", "--reporter=json"],
        fix_args=["lint", "--write", "--reporter=json"],
        dry_run_args=["lint", "--reporter=json"],
        output_format="json",
    ),
    parser=parsers.parse_biome,
)

import coderecon.lint.definitions_ext  # noqa: F401
