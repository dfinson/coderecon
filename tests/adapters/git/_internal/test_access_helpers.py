"""Tests for git/_internal/access_helpers.py — parsing & helper classes.

Covers:
- _ParseMixin._parse_commit() — mocked git output
- _ParseMixin._parse_log_output() — pure string parsing
- _ParseMixin._parse_blame_output() — pure string parsing
- _ReferenceHelper — GitRunner-dependent ops
- _BranchHelper / _BranchCategory — GitRunner-dependent ops
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from coderecon.adapters.git._internal.access_helpers import (
    _BranchCategory,
    _BranchHelper,
    _ParseMixin,
    _ReferenceHelper,
)
from coderecon.adapters.git._internal.access_models import GitBranchData, GitReference
from coderecon.adapters.git.errors import GitError


# ---------------------------------------------------------------------------
# Minimal stub to test _ParseMixin methods (they don't use self._git)
# ---------------------------------------------------------------------------

class _Parseable(_ParseMixin):
    """Minimal stub — parse methods only read `output`, not self._git."""

    def __init__(self) -> None:
        self._git = None  # not used by parsing methods


# ===========================================================================
# _parse_log_output tests
# ===========================================================================

class TestParseLogOutput:
    """Tests for _ParseMixin._parse_log_output."""

    def _make_entry(
        self,
        sha: str = "a" * 40,
        tree: str = "b" * 40,
        parents: str = "",
        author_name: str = "Alice",
        author_email: str = "alice@example.com",
        author_time: int = 1700000000,
        committer_name: str = "Bob",
        committer_email: str = "bob@example.com",
        committer_time: int = 1700000001,
        message: str = "test commit",
    ) -> str:
        return "\n".join([
            sha, tree, parents,
            author_name, author_email, str(author_time),
            committer_name, committer_email, str(committer_time),
            message,
        ])

    def test_single_commit(self) -> None:
        p = _Parseable()
        output = self._make_entry()
        commits = p._parse_log_output(output)
        assert len(commits) == 1
        c = commits[0]
        assert c.sha == "a" * 40
        assert c.tree_sha == "b" * 40
        assert c.parent_shas == ()
        assert c.author.name == "Alice"
        assert c.author.email == "alice@example.com"
        assert c.committer.name == "Bob"
        assert c.message == "test commit"

    def test_multiple_commits_nul_separated(self) -> None:
        p = _Parseable()
        e1 = self._make_entry(sha="1" * 40, message="first")
        e2 = self._make_entry(sha="2" * 40, message="second")
        output = e1 + "\x00" + e2
        commits = p._parse_log_output(output)
        assert len(commits) == 2
        assert commits[0].sha == "1" * 40
        assert commits[1].sha == "2" * 40

    def test_commit_with_parents(self) -> None:
        p = _Parseable()
        parent1 = "c" * 40
        parent2 = "d" * 40
        output = self._make_entry(parents=f"{parent1} {parent2}")
        commits = p._parse_log_output(output)
        assert commits[0].parent_shas == (parent1, parent2)

    def test_empty_output(self) -> None:
        p = _Parseable()
        assert p._parse_log_output("") == []

    def test_malformed_entry_skipped(self) -> None:
        p = _Parseable()
        output = "short\nentry\ntoo\nfew\nlines"
        assert p._parse_log_output(output) == []

    def test_trailing_nul_ignored(self) -> None:
        p = _Parseable()
        output = self._make_entry() + "\x00"
        commits = p._parse_log_output(output)
        assert len(commits) == 1

    def test_multiline_message(self) -> None:
        p = _Parseable()
        output = self._make_entry(message="line1\nline2\nline3")
        commits = p._parse_log_output(output)
        assert commits[0].message == "line1\nline2\nline3"


# ===========================================================================
# _parse_blame_output tests
# ===========================================================================

class TestParseBlameOutput:
    """Tests for _ParseMixin._parse_blame_output."""

    def test_single_hunk(self) -> None:
        p = _Parseable()
        sha = "a" * 40
        output = "\n".join([
            f"{sha} 1 1 3",
            "author Alice",
            "author-mail <alice@example.com>",
            "author-time 1700000000",
            "summary init",
            "\tline content 1",
        ])
        hunks = p._parse_blame_output(output)
        assert len(hunks) == 1
        h = hunks[0]
        assert h["sha"] == sha
        assert h["final_line"] == 1
        assert h["num_lines"] == 3
        assert h["author_name"] == "Alice"
        assert h["author_email"] == "alice@example.com"
        assert h["author_time"] == 1700000000

    def test_multiple_hunks_different_shas(self) -> None:
        p = _Parseable()
        sha1 = "a" * 40
        sha2 = "b" * 40
        output = "\n".join([
            f"{sha1} 1 1 2",
            "author Alice",
            "author-mail <alice@example.com>",
            "author-time 1700000000",
            "\tline 1",
            f"{sha2} 3 3 1",
            "author Bob",
            "author-mail <bob@example.com>",
            "author-time 1700000001",
            "\tline 3",
        ])
        hunks = p._parse_blame_output(output)
        assert len(hunks) == 2
        assert hunks[0]["sha"] == sha1
        assert hunks[1]["sha"] == sha2

    def test_contiguous_same_sha_merged(self) -> None:
        p = _Parseable()
        sha = "a" * 40
        output = "\n".join([
            f"{sha} 1 1 1",
            "author Alice",
            "author-mail <alice@example.com>",
            "author-time 1700000000",
            "\tline 1",
            f"{sha} 2 2 1",
            "\tline 2",
        ])
        hunks = p._parse_blame_output(output)
        assert len(hunks) == 1
        assert hunks[0]["num_lines"] == 2

    def test_empty_output(self) -> None:
        p = _Parseable()
        assert p._parse_blame_output("") == []

    def test_email_angle_brackets_stripped(self) -> None:
        p = _Parseable()
        sha = "f" * 40
        output = "\n".join([
            f"{sha} 1 1 1",
            "author-mail <user@host.com>",
        ])
        hunks = p._parse_blame_output(output)
        assert hunks[0]["author_email"] == "user@host.com"


# ===========================================================================
# _ReferenceHelper tests
# ===========================================================================

class TestReferenceHelper:
    """Tests for _ReferenceHelper with mocked GitRunner."""

    def _make_git(self) -> MagicMock:
        return MagicMock()

    def test_contains_true(self) -> None:
        git = self._make_git()
        git.run_raw.return_value = (0, "abc123\n", "")
        refs = _ReferenceHelper(git)
        assert "refs/heads/main" in refs
        git.run_raw.assert_called_once_with("rev-parse", "--verify", "refs/heads/main")

    def test_contains_false(self) -> None:
        git = self._make_git()
        git.run_raw.return_value = (128, "", "fatal: not a ref")
        refs = _ReferenceHelper(git)
        assert "refs/heads/nope" not in refs

    def test_iter(self) -> None:
        git = self._make_git()
        git.run.return_value = SimpleNamespace(stdout="refs/heads/main\nrefs/tags/v1\n")
        refs = _ReferenceHelper(git)
        assert list(refs) == ["refs/heads/main", "refs/tags/v1"]

    def test_getitem_existing(self) -> None:
        git = self._make_git()
        sha = "a" * 40
        git.run_raw.return_value = (0, sha + "\n", "")
        refs = _ReferenceHelper(git)
        ref = refs["refs/heads/main"]
        assert isinstance(ref, GitReference)
        assert ref.target == sha
        assert ref.shorthand == "main"

    def test_getitem_tag(self) -> None:
        git = self._make_git()
        sha = "b" * 40
        git.run_raw.return_value = (0, sha + "\n", "")
        refs = _ReferenceHelper(git)
        ref = refs["refs/tags/v1.0"]
        assert ref.shorthand == "v1.0"

    def test_getitem_missing_raises_keyerror(self) -> None:
        git = self._make_git()
        git.run_raw.return_value = (128, "", "fatal")
        refs = _ReferenceHelper(git)
        with pytest.raises(KeyError, match="refs/heads/missing"):
            refs["refs/heads/missing"]

    def test_create(self) -> None:
        git = self._make_git()
        refs = _ReferenceHelper(git)
        refs.create("refs/heads/new", "abc123")
        git.run.assert_called_once_with("update-ref", "refs/heads/new", "abc123")

    def test_delete(self) -> None:
        git = self._make_git()
        refs = _ReferenceHelper(git)
        refs.delete("refs/heads/old")
        git.run.assert_called_once_with("update-ref", "-d", "refs/heads/old")


# ===========================================================================
# _BranchCategory tests
# ===========================================================================

class TestBranchCategory:
    """Tests for _BranchCategory with mocked GitRunner."""

    def test_local_iter(self) -> None:
        git = MagicMock()
        git.run.return_value = SimpleNamespace(stdout="main\nfeature/foo\n")
        cat = _BranchCategory(git, remote=False)
        assert list(cat) == ["main", "feature/foo"]
        git.run.assert_called_once_with("branch", "--list", "--format=%(refname:short)")

    def test_remote_iter(self) -> None:
        git = MagicMock()
        git.run.return_value = SimpleNamespace(stdout="origin/main\norigin/dev\n")
        cat = _BranchCategory(git, remote=True)
        assert list(cat) == ["origin/main", "origin/dev"]
        git.run.assert_called_once_with("branch", "-r", "--format=%(refname:short)")

    def test_contains_true(self) -> None:
        git = MagicMock()
        git.run_raw.return_value = (0, "abc\n", "")
        cat = _BranchCategory(git, remote=False)
        assert "main" in cat
        git.run_raw.assert_called_once_with("rev-parse", "--verify", "refs/heads/main")

    def test_contains_remote(self) -> None:
        git = MagicMock()
        git.run_raw.return_value = (0, "abc\n", "")
        cat = _BranchCategory(git, remote=True)
        assert "origin/main" in cat
        git.run_raw.assert_called_once_with("rev-parse", "--verify", "refs/remotes/origin/main")

    def test_getitem_local(self) -> None:
        git = MagicMock()
        sha = "c" * 40
        git.run_raw.return_value = (0, sha + "\n", "")
        cat = _BranchCategory(git, remote=False)
        branch = cat["main"]
        assert isinstance(branch, GitBranchData)
        assert branch.shorthand == "main"
        assert branch.target == sha

    def test_getitem_missing_raises_keyerror(self) -> None:
        git = MagicMock()
        git.run_raw.return_value = (128, "", "fatal")
        cat = _BranchCategory(git, remote=False)
        with pytest.raises(KeyError, match="nope"):
            cat["nope"]

    def test_empty_branches(self) -> None:
        git = MagicMock()
        git.run.return_value = SimpleNamespace(stdout="")
        cat = _BranchCategory(git, remote=False)
        assert list(cat) == []

    def test_contains_false(self) -> None:
        git = MagicMock()
        git.run_raw.return_value = (128, "", "fatal")
        cat = _BranchCategory(git, remote=False)
        assert "nope" not in cat

    def test_getitem_remote(self) -> None:
        git = MagicMock()
        sha = "d" * 40
        git.run_raw.return_value = (0, sha + "\n", "")
        cat = _BranchCategory(git, remote=True)
        branch = cat["origin/main"]
        assert isinstance(branch, GitBranchData)
        assert branch.shorthand == "origin/main"
        assert branch.target == sha
        assert branch.name == "refs/remotes/origin/main"


# ===========================================================================
# _ParseMixin._parse_commit tests
# ===========================================================================

class TestParseCommit:
    """Tests for _ParseMixin._parse_commit with mocked GitRunner."""

    _FMT_LINES = [
        "a" * 40,          # sha
        "b" * 40,          # tree
        "c" * 40,          # parent
        "Alice",           # author name
        "alice@example.com",
        "1700000000",      # author time
        "Bob",             # committer name
        "bob@example.com",
        "1700000001",      # committer time
        "fix: the thing",  # message
    ]

    def _make_parser(self, stdout: str) -> _ParseMixin:
        p = _ParseMixin.__new__(_ParseMixin)
        p._git = MagicMock()
        p._git.run.return_value = SimpleNamespace(stdout=stdout)
        return p

    def test_success_with_parent(self) -> None:
        p = self._make_parser("\n".join(self._FMT_LINES))
        commit = p._parse_commit("HEAD")
        assert commit.sha == "a" * 40
        assert commit.tree_sha == "b" * 40
        assert commit.parent_shas == ("c" * 40,)
        assert commit.author.name == "Alice"
        assert commit.committer.name == "Bob"
        assert commit.message == "fix: the thing"

    def test_success_no_parents(self) -> None:
        lines = list(self._FMT_LINES)
        lines[2] = ""  # empty parent line
        p = self._make_parser("\n".join(lines))
        commit = p._parse_commit("HEAD")
        assert commit.parent_shas == ()

    def test_multiple_parents(self) -> None:
        lines = list(self._FMT_LINES)
        lines[2] = f"{'c' * 40} {'d' * 40}"
        p = self._make_parser("\n".join(lines))
        commit = p._parse_commit("HEAD")
        assert commit.parent_shas == ("c" * 40, "d" * 40)

    def test_multiline_message(self) -> None:
        lines = list(self._FMT_LINES)
        lines[9] = "Subject line\n\nBody paragraph."
        p = self._make_parser("\n".join(lines))
        commit = p._parse_commit("HEAD")
        assert commit.message == "Subject line\n\nBody paragraph."

    def test_too_few_lines_raises_git_error(self) -> None:
        p = self._make_parser("short\noutput\nonly")
        with pytest.raises(GitError):
            p._parse_commit("HEAD")


# ===========================================================================
# _BranchHelper tests
# ===========================================================================

class TestBranchHelper:
    """Tests for _BranchHelper.local and .remote properties."""

    def test_local_returns_branch_category(self) -> None:
        git = MagicMock()
        helper = _BranchHelper(git)
        cat = helper.local
        assert isinstance(cat, _BranchCategory)

    def test_remote_returns_branch_category(self) -> None:
        git = MagicMock()
        helper = _BranchHelper(git)
        cat = helper.remote
        assert isinstance(cat, _BranchCategory)


# ===========================================================================
# _ReferenceHelper — additional edge cases
# ===========================================================================

class TestReferenceHelperEdgeCases:
    """Additional edge cases for _ReferenceHelper."""

    def test_getitem_generic_refname_shorthand_equals_name(self) -> None:
        """A ref not under refs/heads/ or refs/tags/ keeps its full name as shorthand."""
        git = MagicMock()
        sha = "e" * 40
        git.run_raw.return_value = (0, sha + "\n", "")
        refs = _ReferenceHelper(git)
        ref = refs["refs/stash"]
        assert ref.shorthand == "refs/stash"
        assert ref.target == sha

    def test_iter_empty(self) -> None:
        git = MagicMock()
        git.run.return_value = SimpleNamespace(stdout="")
        refs = _ReferenceHelper(git)
        assert list(refs) == []
