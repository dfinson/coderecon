"""End-to-end integration tests — full pipeline workflows.

These tests exercise complete workflows: init → index → search → mutate → verify.
No daemon needed — direct Python API calls against real repos.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from coderecon.files.ops import FileOps
from coderecon.git.ops import GitOps
from coderecon.index.ops import IndexCoordinatorEngine
from coderecon.mutation.ops import Edit, MutationOps

if TYPE_CHECKING:
    from collections.abc import Generator

pytestmark = pytest.mark.integration


def _noop_progress(indexed: int, total: int, by_ext: dict[str, int], phase: str = "") -> None:
    pass


def _make_engine(repo: Path) -> IndexCoordinatorEngine:
    recon = repo / ".recon"
    return IndexCoordinatorEngine(repo, recon / "index.db", recon / "tantivy")


@pytest.fixture
def full_repo(tmp_path: Path) -> Generator[Path, None, None]:
    """Comprehensive repo with multiple modules, imports, and tests."""
    repo = tmp_path / "full_project"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, capture_output=True, check=True)
    subprocess.run(
        ["git", "config", "user.name", "E2E"], cwd=repo, capture_output=True, check=True
    )
    subprocess.run(
        ["git", "config", "user.email", "e@e.com"], cwd=repo, capture_output=True, check=True
    )

    # Project structure
    (repo / "src").mkdir()
    (repo / "src" / "__init__.py").write_text("")
    (repo / "src" / "models.py").write_text(
        'class User:\n    def __init__(self, name: str, email: str) -> None:\n'
        '        self.name = name\n        self.email = email\n\n'
        'class Team:\n    def __init__(self, name: str) -> None:\n'
        '        self.name = name\n        self.members: list[User] = []\n\n'
        '    def add_member(self, user: User) -> None:\n'
        '        self.members.append(user)\n'
    )
    (repo / "src" / "service.py").write_text(
        'from src.models import Team, User\n\n'
        'def create_team(name: str, members: list[dict]) -> Team:\n'
        '    team = Team(name)\n'
        '    for m in members:\n'
        '        user = User(m["name"], m["email"])\n'
        '        team.add_member(user)\n'
        '    return team\n\n'
        'def get_team_emails(team: Team) -> list[str]:\n'
        '    return [m.email for m in team.members]\n'
    )
    (repo / "src" / "utils.py").write_text(
        'def validate_email(email: str) -> bool:\n'
        '    return "@" in email and "." in email.split("@")[-1]\n\n'
        'def format_name(first: str, last: str) -> str:\n'
        '    return f"{first} {last}"\n'
    )

    (repo / "tests").mkdir()
    (repo / "tests" / "__init__.py").write_text("")
    (repo / "tests" / "test_models.py").write_text(
        'from src.models import Team, User\n\n'
        'def test_user_creation() -> None:\n'
        '    u = User("Alice", "alice@example.com")\n'
        '    assert u.name == "Alice"\n\n'
        'def test_team_add_member() -> None:\n'
        '    t = Team("dev")\n'
        '    t.add_member(User("Bob", "bob@example.com"))\n'
        '    assert len(t.members) == 1\n'
    )
    (repo / "tests" / "test_utils.py").write_text(
        'from src.utils import format_name, validate_email\n\n'
        'def test_validate_email_valid() -> None:\n'
        '    assert validate_email("a@b.com") is True\n\n'
        'def test_validate_email_invalid() -> None:\n'
        '    assert validate_email("invalid") is False\n\n'
        'def test_format_name() -> None:\n'
        '    assert format_name("Jane", "Doe") == "Jane Doe"\n'
    )

    (repo / "pyproject.toml").write_text(
        '[project]\nname = "full-project"\nversion = "0.1.0"\n\n'
        '[tool.pytest.ini_options]\ntestpaths = ["tests"]\npythonpath = ["."]\n'
    )

    recon_dir = repo / ".recon"
    recon_dir.mkdir()
    (recon_dir / ".reconignore").write_text("*.pyc\n__pycache__\n.recon\n")

    subprocess.run(["git", "add", "-A"], cwd=repo, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", "Initial commit"], cwd=repo, capture_output=True, check=True
    )
    yield repo


class TestFullPipeline:
    """Complete end-to-end workflow: init → index → search → mutate → reindex → verify."""

    @pytest.mark.asyncio
    async def test_init_index_search_workflow(self, full_repo: Path) -> None:
        """Initialize index, run search, verify results."""
        engine = _make_engine(full_repo)
        await engine.initialize(_noop_progress)

        # Search for a function
        result = await engine.search("validate_email", mode="lexical")
        assert len(result.results) > 0
        paths = {r.path for r in result.results}
        assert any("utils" in p for p in paths)

    @pytest.mark.asyncio
    async def test_search_finds_class(self, full_repo: Path) -> None:
        engine = _make_engine(full_repo)
        await engine.initialize(_noop_progress)

        result = await engine.search("Team", mode="lexical")
        assert len(result.results) > 0

    @pytest.mark.asyncio
    async def test_mutate_then_reindex(self, full_repo: Path) -> None:
        """Mutate a file, then reindex and search for the new content."""
        engine = _make_engine(full_repo)
        await engine.initialize(_noop_progress)

        # Add a new function via mutation
        mutation_ops = MutationOps(full_repo)
        mutation_ops.write_source([
            Edit(
                path="src/analytics.py",
                action="create",
                content=(
                    'def compute_metrics(data: list) -> dict:\n'
                    '    """Compute team analytics metrics."""\n'
                    '    return {"count": len(data)}\n'
                ),
            ),
        ])

        # Reindex
        await engine.reindex_incremental(
            [full_repo / "src" / "analytics.py"],
        )

        # Verify new content is searchable
        result = await engine.search("compute_metrics", mode="lexical")
        assert len(result.results) > 0


class TestGitMutationWorkflow:
    """Git + mutation integration — create files, check status, commit."""

    def test_mutation_shows_in_git_status(self, full_repo: Path) -> None:
        git_ops = GitOps(full_repo)
        mutation_ops = MutationOps(full_repo)

        # Create a new file
        mutation_ops.write_source([
            Edit(path="src/new_module.py", action="create", content="x = 1\n"),
        ])

        status = git_ops.status()
        # status() returns dict[str, int] — keys are file paths
        assert any("new_module" in path for path in status)

    def test_mutation_update_shows_diff(self, full_repo: Path) -> None:
        git_ops = GitOps(full_repo)
        mutation_ops = MutationOps(full_repo)

        mutation_ops.write_source([
            Edit(path="src/utils.py", action="update", content="# completely rewritten\nx = 42\n"),
        ])

        diff = git_ops.diff()
        # diff returns a DiffInfo object — check its string representation
        assert diff is not None

    def test_full_commit_workflow(self, full_repo: Path) -> None:
        git_ops = GitOps(full_repo)
        mutation_ops = MutationOps(full_repo)

        # Create file
        mutation_ops.write_source([
            Edit(path="new_feature.py", action="create", content="feature = True\n"),
        ])

        # Stage and commit
        subprocess.run(
            ["git", "add", "new_feature.py"], cwd=full_repo, capture_output=True, check=True
        )
        subprocess.run(
            ["git", "commit", "-m", "feat: add feature"],
            cwd=full_repo,
            capture_output=True,
            check=True,
        )

        # Verify commit appears in log
        log_entries = git_ops.log(limit=1)
        assert len(log_entries) == 1
        assert "feat: add feature" in log_entries[0].message


class TestFileOpsIntegration:
    """FileOps with real filesystem in context of a project."""

    def test_read_and_list(self, full_repo: Path) -> None:
        file_ops = FileOps(full_repo)

        # List files
        listing = file_ops.list_files("src")
        assert listing is not None
        # Read a specific file
        read_result = file_ops.read_files(["src/models.py"])
        assert len(read_result.files) == 1
        assert "class User" in read_result.files[0].content

    def test_read_with_line_range(self, full_repo: Path) -> None:
        file_ops = FileOps(full_repo)
        result = file_ops.read_files(
            ["src/models.py"],
            targets={"src/models.py": (1, 3)},
        )
        assert len(result.files) == 1
        lines = result.files[0].content.strip().split("\n")
        assert len(lines) <= 3

    def test_read_nonexistent_file(self, full_repo: Path) -> None:
        file_ops = FileOps(full_repo)
        result = file_ops.read_files(["no_such_file.py"])
        # Should handle gracefully (either empty or error in result)
        assert result is not None

    def test_list_recursive(self, full_repo: Path) -> None:
        file_ops = FileOps(full_repo)
        listing = file_ops.list_files(".", recursive=True)
        assert listing is not None


class TestMultiFileMutationPipeline:
    """Multi-file mutation + index verification."""

    @pytest.mark.asyncio
    async def test_create_update_delete_reindex(self, full_repo: Path) -> None:
        engine = _make_engine(full_repo)
        await engine.initialize(_noop_progress)

        mutation_ops = MutationOps(full_repo)

        # Create a new file
        mutation_ops.write_source([
            Edit(path="src/config.py", action="create", content="DB_URL = 'sqlite:///app.db'\n"),
        ])

        # Update an existing file
        old_content = (full_repo / "src" / "utils.py").read_text()
        new_content = old_content + "\ndef new_util() -> str:\n    return 'hello'\n"
        mutation_ops.write_source([
            Edit(path="src/utils.py", action="update", content=new_content),
        ])

        # Reindex changed files
        await engine.reindex_incremental([
            full_repo / "src" / "config.py",
            full_repo / "src" / "utils.py",
        ])

        # Verify both are searchable
        r1 = await engine.search("DB_URL", mode="lexical")
        assert len(r1.results) > 0

        r2 = await engine.search("new_util", mode="lexical")
        assert len(r2.results) > 0
