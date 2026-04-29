"""Integration tests for mutation operations — multi-file, edge cases, rollback."""

from __future__ import annotations

from pathlib import Path

import pytest

from coderecon.adapters.mutation.ops import Edit, MutationOps

pytestmark = pytest.mark.integration


@pytest.fixture
def mutation_repo(tmp_path: Path) -> Path:
    """A minimal directory for mutation tests (no git needed)."""
    repo = tmp_path / "mutation_repo"
    repo.mkdir()
    (repo / "existing.py").write_text("x = 1\n")
    (repo / "other.py").write_text("y = 2\nz = 3\n")
    return repo


class TestCreateAction:
    def test_create_new_file(self, mutation_repo: Path) -> None:
        ops = MutationOps(mutation_repo)
        result = ops.write_source([Edit(path="new.py", action="create", content="a = 1\n")])
        assert result.applied is True
        assert result.dry_run is False
        assert (mutation_repo / "new.py").read_text() == "a = 1\n"
        assert result.delta.files_changed == 1
        assert result.delta.files[0].action == "created"

    def test_create_nested_dirs(self, mutation_repo: Path) -> None:
        ops = MutationOps(mutation_repo)
        result = ops.write_source(
            [Edit(path="a/b/c/deep.py", action="create", content="deep = True\n")]
        )
        assert result.applied is True
        assert (mutation_repo / "a" / "b" / "c" / "deep.py").read_text() == "deep = True\n"

    def test_create_existing_raises(self, mutation_repo: Path) -> None:
        ops = MutationOps(mutation_repo)
        with pytest.raises(FileExistsError, match="existing.py"):
            ops.write_source([Edit(path="existing.py", action="create", content="")])

    def test_create_empty_content(self, mutation_repo: Path) -> None:
        ops = MutationOps(mutation_repo)
        result = ops.write_source([Edit(path="empty.py", action="create", content="")])
        assert (mutation_repo / "empty.py").read_text() == ""
        assert result.delta.files[0].new_hash is not None

    def test_create_none_content_treated_as_empty(self, mutation_repo: Path) -> None:
        ops = MutationOps(mutation_repo)
        result = ops.write_source([Edit(path="nil.py", action="create")])
        assert (mutation_repo / "nil.py").read_text() == ""
        assert result.delta.files[0].insertions == 1  # empty string -> 1 "line"


class TestUpdateAction:
    def test_update_existing_file(self, mutation_repo: Path) -> None:
        ops = MutationOps(mutation_repo)
        result = ops.write_source(
            [Edit(path="existing.py", action="update", content="x = 99\ny = 100\n")]
        )
        assert result.applied is True
        assert (mutation_repo / "existing.py").read_text() == "x = 99\ny = 100\n"
        delta = result.delta.files[0]
        assert delta.action == "updated"
        assert delta.old_hash is not None
        assert delta.new_hash is not None
        assert delta.old_hash != delta.new_hash

    def test_update_nonexistent_raises(self, mutation_repo: Path) -> None:
        ops = MutationOps(mutation_repo)
        with pytest.raises(FileNotFoundError, match="ghost.py"):
            ops.write_source([Edit(path="ghost.py", action="update", content="")])

    def test_update_with_same_content(self, mutation_repo: Path) -> None:
        """Update with identical content still succeeds (idempotent)."""
        ops = MutationOps(mutation_repo)
        result = ops.write_source(
            [Edit(path="existing.py", action="update", content="x = 1\n")]
        )
        assert result.applied is True
        delta = result.delta.files[0]
        assert delta.old_hash == delta.new_hash

    def test_update_tracks_insertions_deletions(self, mutation_repo: Path) -> None:
        ops = MutationOps(mutation_repo)
        # Expand from 1 line to 3 lines
        result = ops.write_source(
            [Edit(path="existing.py", action="update", content="a = 1\nb = 2\nc = 3\n")]
        )
        delta = result.delta.files[0]
        assert delta.insertions >= 2


class TestDeleteAction:
    def test_delete_file(self, mutation_repo: Path) -> None:
        ops = MutationOps(mutation_repo)
        result = ops.write_source([Edit(path="existing.py", action="delete")])
        assert result.applied is True
        assert not (mutation_repo / "existing.py").exists()
        delta = result.delta.files[0]
        assert delta.action == "deleted"
        assert delta.old_hash is not None
        assert delta.new_hash is None

    def test_delete_nonexistent_raises(self, mutation_repo: Path) -> None:
        ops = MutationOps(mutation_repo)
        with pytest.raises(FileNotFoundError, match="nope.py"):
            ops.write_source([Edit(path="nope.py", action="delete")])


class TestDryRun:
    def test_dry_run_does_not_modify(self, mutation_repo: Path) -> None:
        ops = MutationOps(mutation_repo)
        result = ops.write_source(
            [Edit(path="existing.py", action="update", content="changed\n")],
            dry_run=True,
        )
        assert result.applied is False
        assert result.dry_run is True
        # File should be unchanged
        assert (mutation_repo / "existing.py").read_text() == "x = 1\n"

    def test_dry_run_create_does_not_create(self, mutation_repo: Path) -> None:
        ops = MutationOps(mutation_repo)
        ops.write_source(
            [Edit(path="phantom.py", action="create", content="phantom\n")],
            dry_run=True,
        )
        assert not (mutation_repo / "phantom.py").exists()

    def test_dry_run_delete_does_not_delete(self, mutation_repo: Path) -> None:
        ops = MutationOps(mutation_repo)
        ops.write_source(
            [Edit(path="existing.py", action="delete")],
            dry_run=True,
        )
        assert (mutation_repo / "existing.py").exists()

    def test_dry_run_returns_delta(self, mutation_repo: Path) -> None:
        ops = MutationOps(mutation_repo)
        result = ops.write_source(
            [Edit(path="existing.py", action="update", content="new\n")],
            dry_run=True,
        )
        assert result.delta.files_changed == 1
        assert result.changed_paths == []  # dry_run → no changed_paths


class TestMultiFileEdits:
    def test_multiple_creates(self, mutation_repo: Path) -> None:
        ops = MutationOps(mutation_repo)
        result = ops.write_source([
            Edit(path="a.py", action="create", content="a\n"),
            Edit(path="b.py", action="create", content="b\n"),
            Edit(path="c.py", action="create", content="c\n"),
        ])
        assert result.delta.files_changed == 3
        assert (mutation_repo / "a.py").read_text() == "a\n"
        assert (mutation_repo / "b.py").read_text() == "b\n"
        assert (mutation_repo / "c.py").read_text() == "c\n"

    def test_mixed_actions(self, mutation_repo: Path) -> None:
        ops = MutationOps(mutation_repo)
        result = ops.write_source([
            Edit(path="new_file.py", action="create", content="new\n"),
            Edit(path="existing.py", action="update", content="updated\n"),
            Edit(path="other.py", action="delete"),
        ])
        assert result.delta.files_changed == 3
        assert (mutation_repo / "new_file.py").exists()
        assert (mutation_repo / "existing.py").read_text() == "updated\n"
        assert not (mutation_repo / "other.py").exists()

    def test_atomic_validation_rollback(self, mutation_repo: Path) -> None:
        """If any edit in the batch is invalid, no edits should apply."""
        ops = MutationOps(mutation_repo)
        with pytest.raises(FileExistsError):
            ops.write_source([
                Edit(path="valid_new.py", action="create", content="ok\n"),
                Edit(path="existing.py", action="create", content="conflict\n"),  # already exists
            ])
        # The valid_new.py should NOT have been created because validation
        # happens before any writes
        assert not (mutation_repo / "valid_new.py").exists()


class TestDelta:
    def test_mutation_id_is_unique(self, mutation_repo: Path) -> None:
        ops = MutationOps(mutation_repo)
        r1 = ops.write_source([Edit(path="a.py", action="create", content="a\n")])
        r2 = ops.write_source([Edit(path="b.py", action="create", content="b\n")])
        assert r1.delta.mutation_id != r2.delta.mutation_id

    def test_changed_paths_populated(self, mutation_repo: Path) -> None:
        ops = MutationOps(mutation_repo)
        result = ops.write_source(
            [Edit(path="existing.py", action="update", content="x = 2\n")]
        )
        assert len(result.changed_paths) == 1
        assert result.changed_paths[0] == mutation_repo / "existing.py"

    def test_total_insertions_deletions(self, mutation_repo: Path) -> None:
        ops = MutationOps(mutation_repo)
        result = ops.write_source([
            Edit(path="a.py", action="create", content="1\n2\n3\n"),
            Edit(path="other.py", action="delete"),
        ])
        assert result.delta.insertions > 0
        assert result.delta.deletions > 0


class TestEmptyEdits:
    def test_empty_edit_list(self, mutation_repo: Path) -> None:
        ops = MutationOps(mutation_repo)
        result = ops.write_source([])
        assert result.delta.files_changed == 0
        assert result.applied is True
