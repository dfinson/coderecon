"""Tests for coderecon.git._internal.access."""

import dataclasses
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from coderecon.git._internal.access import (
    GitCommitData,
    GitReference,
    GitSignature,
    GitTagData,
    RepoAccess,
)
from coderecon.git._internal.constants import (
    GIT_REPOSITORY_STATE_APPLY_MAILBOX,
    GIT_REPOSITORY_STATE_APPLY_MAILBOX_OR_REBASE,
    GIT_REPOSITORY_STATE_BISECT,
    GIT_REPOSITORY_STATE_CHERRYPICK,
    GIT_REPOSITORY_STATE_CHERRYPICK_SEQUENCE,
    GIT_REPOSITORY_STATE_MERGE,
    GIT_REPOSITORY_STATE_NONE,
    GIT_REPOSITORY_STATE_REBASE,
    GIT_REPOSITORY_STATE_REBASE_INTERACTIVE,
    GIT_REPOSITORY_STATE_REBASE_MERGE,
    GIT_REPOSITORY_STATE_REVERT,
    GIT_REPOSITORY_STATE_REVERT_SEQUENCE,
    STATUS_INDEX_DELETED,
    STATUS_INDEX_MODIFIED,
    STATUS_INDEX_NEW,
    STATUS_WT_MODIFIED,
    STATUS_WT_NEW,
)
from coderecon.git.errors import GitError

# ---------------------------------------------------------------------------
# Helper to build a RepoAccess with mocked internals
# ---------------------------------------------------------------------------

def _make_repo_access(tmp_path: Path) -> RepoAccess:
    """Construct a RepoAccess bypassing __init__ validation."""
    git_dir = tmp_path / ".git"
    git_dir.mkdir(exist_ok=True)
    ra = object.__new__(RepoAccess)
    ra._path = tmp_path
    ra._git = MagicMock()
    ra._git_dir = git_dir
    ra._index = MagicMock()
    return ra

# ===========================================================================
# Dataclass tests
# ===========================================================================

class TestDataclassImmutability:
    """Verify frozen dataclasses reject mutation."""

    def test_git_signature_frozen(self):
        sig = GitSignature(name="a", email="b", time=0, offset=0)
        with pytest.raises(dataclasses.FrozenInstanceError):
            sig.name = "x"  # type: ignore[misc]

    def test_git_commit_data_frozen(self):
        sig = GitSignature(name="a", email="b", time=0, offset=0)
        commit = GitCommitData(
            sha="abc", tree_sha="def", parent_shas=(), author=sig,
            committer=sig, message="m",
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            commit.sha = "zzz"  # type: ignore[misc]

    def test_git_reference_frozen(self):
        ref = GitReference(name="refs/heads/main", target="abc123", shorthand="main")
        with pytest.raises(dataclasses.FrozenInstanceError):
            ref.name = "other"  # type: ignore[misc]

    def test_git_tag_data_defaults(self):
        tag = GitTagData(name="v1", target_sha="abc", is_annotated=False)
        assert tag.message is None
        assert tag.tagger is None

# ===========================================================================
# _parse_commit tests
# ===========================================================================

class TestParseCommit:
    """Tests for RepoAccess._parse_commit."""

    def test_basic_commit(self, tmp_path: Path):
        ra = _make_repo_access(tmp_path)
        stdout = "\n".join([
            "abc123def456abc123def456abc123def456abc1",  # sha
            "tree_sha_000",                              # tree
            "parent_sha_111",                            # parents
            "Alice",                                     # author name
            "alice@example.com",                         # author email
            "1700000000",                                # author time
            "Bob",                                       # committer name
            "bob@example.com",                           # committer email
            "1700000001",                                # committer time
            "Initial commit",                            # message
        ])
        ra._git.run.return_value = SimpleNamespace(stdout=stdout)

        commit = ra._parse_commit("HEAD")

        assert commit.sha == "abc123def456abc123def456abc123def456abc1"
        assert commit.tree_sha == "tree_sha_000"
        assert commit.parent_shas == ("parent_sha_111",)
        assert commit.author.name == "Alice"
        assert commit.author.email == "alice@example.com"
        assert commit.author.time == 1700000000
        assert commit.committer.name == "Bob"
        assert commit.committer.email == "bob@example.com"
        assert commit.message == "Initial commit"

    def test_merge_commit_multiple_parents(self, tmp_path: Path):
        ra = _make_repo_access(tmp_path)
        stdout = "\n".join([
            "sha1",
            "tree1",
            "parent_a parent_b parent_c",
            "Alice", "a@b.c", "100",
            "Alice", "a@b.c", "100",
            "Merge branch 'feature'",
        ])
        ra._git.run.return_value = SimpleNamespace(stdout=stdout)

        commit = ra._parse_commit("HEAD")
        assert commit.parent_shas == ("parent_a", "parent_b", "parent_c")

    def test_initial_commit_no_parents(self, tmp_path: Path):
        ra = _make_repo_access(tmp_path)
        stdout = "\n".join([
            "sha_root",
            "tree_root",
            "",  # no parents
            "Dev", "dev@co.io", "50",
            "Dev", "dev@co.io", "50",
            "root commit",
        ])
        ra._git.run.return_value = SimpleNamespace(stdout=stdout)

        commit = ra._parse_commit("HEAD")
        assert commit.parent_shas == ()

    def test_multiline_message(self, tmp_path: Path):
        ra = _make_repo_access(tmp_path)
        stdout = "\n".join([
            "sha1", "tree1", "p1",
            "A", "a@b", "1",
            "A", "a@b", "1",
            "Subject line",
            "",
            "Body paragraph.",
        ])
        ra._git.run.return_value = SimpleNamespace(stdout=stdout)

        commit = ra._parse_commit("HEAD")
        assert commit.message == "Subject line\n\nBody paragraph."

    def test_too_few_lines_raises(self, tmp_path: Path):
        ra = _make_repo_access(tmp_path)
        ra._git.run.return_value = SimpleNamespace(stdout="only\nfew\nlines")

        with pytest.raises(GitError, match="Failed to parse commit"):
            ra._parse_commit("HEAD")

# ===========================================================================
# _parse_log_output tests
# ===========================================================================

class TestParseLogOutput:
    """Tests for RepoAccess._parse_log_output."""

    def test_single_entry(self, tmp_path: Path):
        ra = _make_repo_access(tmp_path)
        entry = "\n".join([
            "sha1", "tree1", "p1",
            "A", "a@b", "1",
            "C", "c@d", "2",
            "msg",
        ])
        commits = ra._parse_log_output(entry)
        assert len(commits) == 1
        assert commits[0].sha == "sha1"

    def test_multiple_entries(self, tmp_path: Path):
        ra = _make_repo_access(tmp_path)
        entry1 = "\n".join(["sha1", "t1", "p1", "A", "a@b", "1", "C", "c@d", "2", "first"])
        entry2 = "\n".join(["sha2", "t2", "", "B", "b@c", "3", "D", "d@e", "4", "second"])
        output = entry1 + "\x00" + entry2
        commits = ra._parse_log_output(output)
        assert len(commits) == 2
        assert commits[0].sha == "sha1"
        assert commits[1].sha == "sha2"
        assert commits[1].parent_shas == ()

    def test_empty_output(self, tmp_path: Path):
        ra = _make_repo_access(tmp_path)
        assert ra._parse_log_output("") == []
        assert ra._parse_log_output("\x00") == []

    def test_skips_short_entries(self, tmp_path: Path):
        ra = _make_repo_access(tmp_path)
        good = "\n".join(["sha1", "t1", "p1", "A", "a@b", "1", "C", "c@d", "2", "ok"])
        bad = "too\nshort"
        commits = ra._parse_log_output(good + "\x00" + bad)
        assert len(commits) == 1

# ===========================================================================
# _parse_blame_output tests
# ===========================================================================

class TestParseBlameOutput:
    """Tests for RepoAccess._parse_blame_output."""

    def test_single_hunk(self, tmp_path: Path):
        ra = _make_repo_access(tmp_path)
        sha = "a" * 40
        output = "\n".join([
            f"{sha} 1 1 3",
            "author Alice",
            "author-mail <alice@example.com>",
            "author-time 1700000000",
            "summary some line",
            "\tcode line 1",
        ])
        hunks = ra._parse_blame_output(output)
        assert len(hunks) == 1
        assert hunks[0]["sha"] == sha
        assert hunks[0]["final_line"] == 1
        assert hunks[0]["num_lines"] == 3
        assert hunks[0]["author_name"] == "Alice"
        assert hunks[0]["author_email"] == "alice@example.com"
        assert hunks[0]["author_time"] == 1700000000

    def test_two_different_hunks(self, tmp_path: Path):
        ra = _make_repo_access(tmp_path)
        sha_a = "a" * 40
        sha_b = "b" * 40
        output = "\n".join([
            f"{sha_a} 1 1 2",
            "author A",
            "author-mail <a@b>",
            "author-time 100",
            "\tline1",
            f"{sha_b} 1 3 1",
            "author B",
            "author-mail <b@c>",
            "author-time 200",
            "\tline3",
        ])
        hunks = ra._parse_blame_output(output)
        assert len(hunks) == 2
        assert hunks[0]["sha"] == sha_a
        assert hunks[1]["sha"] == sha_b

    def test_empty_output(self, tmp_path: Path):
        ra = _make_repo_access(tmp_path)
        assert ra._parse_blame_output("") == []

# ===========================================================================
# status() tests
# ===========================================================================

class TestStatus:
    """Tests for RepoAccess.status."""

    def _status_with(self, tmp_path: Path, porcelain_stdout: str) -> dict[str, int]:
        ra = _make_repo_access(tmp_path)
        ra._git.run.return_value = SimpleNamespace(stdout=porcelain_stdout)
        return ra.status()

    def test_modified_in_worktree(self, tmp_path: Path):
        result = self._status_with(tmp_path, " M file.py\n")
        assert result == {"file.py": STATUS_WT_MODIFIED}

    def test_added_to_index(self, tmp_path: Path):
        result = self._status_with(tmp_path, "A  new_file.py\n")
        assert result == {"new_file.py": STATUS_INDEX_NEW}

    def test_deleted_from_index(self, tmp_path: Path):
        result = self._status_with(tmp_path, "D  gone.py\n")
        assert result == {"gone.py": STATUS_INDEX_DELETED}

    def test_untracked_file(self, tmp_path: Path):
        result = self._status_with(tmp_path, "?? untracked.txt\n")
        assert result == {"untracked.txt": STATUS_WT_NEW}

    def test_renamed_uses_new_path(self, tmp_path: Path):
        result = self._status_with(tmp_path, "R  old.py -> new.py\n")
        assert "new.py" in result
        assert "old.py" not in result
        assert result["new.py"] & STATUS_INDEX_MODIFIED

    def test_modified_in_both_index_and_worktree(self, tmp_path: Path):
        result = self._status_with(tmp_path, "MM both.py\n")
        assert result["both.py"] == STATUS_INDEX_MODIFIED | STATUS_WT_MODIFIED

    def test_multiple_files(self, tmp_path: Path):
        porcelain = "A  added.py\n M modified.py\n?? new.txt\n"
        result = self._status_with(tmp_path, porcelain)
        assert len(result) == 3
        assert result["added.py"] == STATUS_INDEX_NEW
        assert result["modified.py"] == STATUS_WT_MODIFIED
        assert result["new.txt"] == STATUS_WT_NEW

    def test_empty_status(self, tmp_path: Path):
        result = self._status_with(tmp_path, "")
        assert result == {}

    def test_short_lines_skipped(self, tmp_path: Path):
        result = self._status_with(tmp_path, "x\nA  ok.py\n")
        assert result == {"ok.py": STATUS_INDEX_NEW}

# ===========================================================================
# state() tests
# ===========================================================================

class TestState:
    """Tests for RepoAccess.state using tmp_path for .git dir files."""

    def _make_state_repo(self, tmp_path: Path) -> RepoAccess:
        return _make_repo_access(tmp_path)

    def test_clean_repo(self, tmp_path: Path):
        ra = self._make_state_repo(tmp_path)
        assert ra.state() == GIT_REPOSITORY_STATE_NONE

    def test_merge_state(self, tmp_path: Path):
        ra = self._make_state_repo(tmp_path)
        (tmp_path / ".git" / "MERGE_HEAD").touch()
        assert ra.state() == GIT_REPOSITORY_STATE_MERGE

    def test_rebase_interactive(self, tmp_path: Path):
        ra = self._make_state_repo(tmp_path)
        (tmp_path / ".git" / "rebase-merge" / "interactive").mkdir(parents=True)
        assert ra.state() == GIT_REPOSITORY_STATE_REBASE_INTERACTIVE

    def test_rebase_merge(self, tmp_path: Path):
        ra = self._make_state_repo(tmp_path)
        (tmp_path / ".git" / "rebase-merge").mkdir(parents=True)
        assert ra.state() == GIT_REPOSITORY_STATE_REBASE_MERGE

    def test_rebase_apply_rebasing(self, tmp_path: Path):
        ra = self._make_state_repo(tmp_path)
        (tmp_path / ".git" / "rebase-apply").mkdir(parents=True)
        (tmp_path / ".git" / "rebase-apply" / "rebasing").touch()
        assert ra.state() == GIT_REPOSITORY_STATE_REBASE

    def test_apply_mailbox(self, tmp_path: Path):
        ra = self._make_state_repo(tmp_path)
        (tmp_path / ".git" / "rebase-apply").mkdir(parents=True)
        (tmp_path / ".git" / "rebase-apply" / "applying").touch()
        assert ra.state() == GIT_REPOSITORY_STATE_APPLY_MAILBOX

    def test_apply_mailbox_or_rebase(self, tmp_path: Path):
        ra = self._make_state_repo(tmp_path)
        (tmp_path / ".git" / "rebase-apply").mkdir(parents=True)
        assert ra.state() == GIT_REPOSITORY_STATE_APPLY_MAILBOX_OR_REBASE

    def test_revert(self, tmp_path: Path):
        ra = self._make_state_repo(tmp_path)
        (tmp_path / ".git" / "REVERT_HEAD").touch()
        assert ra.state() == GIT_REPOSITORY_STATE_REVERT

    def test_revert_sequence(self, tmp_path: Path):
        ra = self._make_state_repo(tmp_path)
        (tmp_path / ".git" / "REVERT_HEAD").touch()
        (tmp_path / ".git" / "sequencer").mkdir()
        assert ra.state() == GIT_REPOSITORY_STATE_REVERT_SEQUENCE

    def test_cherrypick(self, tmp_path: Path):
        ra = self._make_state_repo(tmp_path)
        (tmp_path / ".git" / "CHERRY_PICK_HEAD").touch()
        assert ra.state() == GIT_REPOSITORY_STATE_CHERRYPICK

    def test_cherrypick_sequence(self, tmp_path: Path):
        ra = self._make_state_repo(tmp_path)
        (tmp_path / ".git" / "CHERRY_PICK_HEAD").touch()
        (tmp_path / ".git" / "sequencer").mkdir()
        assert ra.state() == GIT_REPOSITORY_STATE_CHERRYPICK_SEQUENCE

    def test_bisect(self, tmp_path: Path):
        ra = self._make_state_repo(tmp_path)
        (tmp_path / ".git" / "BISECT_LOG").touch()
        assert ra.state() == GIT_REPOSITORY_STATE_BISECT

# ===========================================================================
# normalize_path tests
# ===========================================================================

class TestNormalizePath:
    """Tests for RepoAccess.normalize_path."""

    def test_relative_path_unchanged(self, tmp_path: Path):
        ra = _make_repo_access(tmp_path)
        assert ra.normalize_path("src/foo.py") == "src/foo.py"

    def test_absolute_path_made_relative(self, tmp_path: Path):
        ra = _make_repo_access(tmp_path)
        abs_path = tmp_path / "src" / "bar.py"
        assert ra.normalize_path(abs_path) == "src/bar.py"

    def test_absolute_outside_repo_unchanged(self, tmp_path: Path):
        ra = _make_repo_access(tmp_path)
        result = ra.normalize_path("/completely/other/path.py")
        assert result == "/completely/other/path.py"
