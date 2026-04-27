"""Tests for IgnoreChecker - shared path exclusion logic."""

from pathlib import Path

from coderecon.index._internal.ignore import IgnoreChecker

class TestIgnoreChecker:
    """Tests for IgnoreChecker."""

    def test_init_without_reconignore(self, tmp_path: Path) -> None:
        """IgnoreChecker works when .reconignore doesn't exist."""
        checker = IgnoreChecker(tmp_path)
        # Should not ignore anything by default
        assert not checker.should_ignore(tmp_path / "file.py")

    def test_init_with_extra_patterns(self, tmp_path: Path) -> None:
        """IgnoreChecker accepts extra patterns."""
        checker = IgnoreChecker(tmp_path, extra_patterns=["*.log", "temp/**"])
        assert checker.should_ignore(tmp_path / "debug.log")
        assert checker.should_ignore(tmp_path / "temp" / "file.txt")
        assert not checker.should_ignore(tmp_path / "main.py")

    def test_loads_reconignore_patterns(self, tmp_path: Path) -> None:
        """IgnoreChecker loads patterns from .reconignore file."""
        reconignore = tmp_path / ".recon" / ".reconignore"
        reconignore.parent.mkdir(parents=True)
        reconignore.write_text("*.pyc\n__pycache__/\n# comment\n\n")

        checker = IgnoreChecker(tmp_path)
        assert checker.should_ignore(tmp_path / "module.pyc")
        assert checker.should_ignore(tmp_path / "__pycache__" / "file.pyc")

    def test_directory_patterns_match_contents(self, tmp_path: Path) -> None:
        """Directory patterns ending in / match contents."""
        reconignore = tmp_path / ".recon" / ".reconignore"
        reconignore.parent.mkdir(parents=True)
        reconignore.write_text("build/\n")

        checker = IgnoreChecker(tmp_path)
        assert checker.should_ignore(tmp_path / "build" / "output.js")
        assert checker.should_ignore(tmp_path / "build" / "nested" / "file.txt")

    def test_parent_directory_matching(self, tmp_path: Path) -> None:
        """Patterns match parent directories."""
        checker = IgnoreChecker(tmp_path, extra_patterns=["node_modules"])
        # File inside node_modules should be ignored
        assert checker.should_ignore(tmp_path / "node_modules" / "pkg" / "index.js")

    def test_path_outside_root_is_ignored(self, tmp_path: Path) -> None:
        """Paths outside root are always ignored."""
        checker = IgnoreChecker(tmp_path)
        other_path = tmp_path.parent / "other" / "file.py"
        assert checker.should_ignore(other_path)

    def test_is_excluded_rel_basic(self, tmp_path: Path) -> None:
        """is_excluded_rel works with relative path strings."""
        checker = IgnoreChecker(tmp_path, extra_patterns=["*.log", "dist/**"])
        assert checker.is_excluded_rel("debug.log")
        assert checker.is_excluded_rel("dist/bundle.js")
        assert not checker.is_excluded_rel("src/main.py")

    def test_is_excluded_rel_parent_matching(self, tmp_path: Path) -> None:
        """is_excluded_rel matches parent directories."""
        checker = IgnoreChecker(tmp_path, extra_patterns=["__pycache__"])
        assert checker.is_excluded_rel("__pycache__/module.cpython-312.pyc")
        # Note: pattern "__pycache__" doesn't match nested paths without **
        # This tests actual fnmatch behavior

    def test_is_excluded_rel_negation(self, tmp_path: Path) -> None:
        """is_excluded_rel handles negation patterns (last match wins)."""
        reconignore = tmp_path / ".recon" / ".reconignore"
        reconignore.parent.mkdir(parents=True)
        # In gitignore, last matching pattern wins.
        # Negation AFTER the include pattern opts the file back in.
        reconignore.write_text("*.txt\n!important.txt\n")

        checker = IgnoreChecker(tmp_path)
        assert checker.is_excluded_rel("notes.txt")
        # Negation after *.txt re-includes important.txt
        assert not checker.is_excluded_rel("important.txt")

    def test_reconignore_read_error_handled(self, tmp_path: Path) -> None:
        """OSError reading .reconignore is handled gracefully."""
        reconignore = tmp_path / ".recon" / ".reconignore"
        reconignore.parent.mkdir(parents=True)
        reconignore.mkdir()  # Make it a directory to cause OSError

        # Should not raise, just skip loading
        checker = IgnoreChecker(tmp_path)
        assert not checker.should_ignore(tmp_path / "file.py")

    def test_comment_and_empty_lines_skipped(self, tmp_path: Path) -> None:
        """Comments and empty lines in .reconignore are skipped."""
        reconignore = tmp_path / ".recon" / ".reconignore"
        reconignore.parent.mkdir(parents=True)
        reconignore.write_text("# This is a comment\n\n  \n*.log\n")

        checker = IgnoreChecker(tmp_path)
        # Only *.log should be active
        assert checker.should_ignore(tmp_path / "debug.log")
        # Comments/empty aren't patterns
        assert not checker.should_ignore(tmp_path / "# This is a comment")

class TestHierarchicalCplignore:
    """Tests for hierarchical .reconignore support (files anywhere in repo)."""

    def test_loads_root_reconignore(self, tmp_path: Path) -> None:
        """IgnoreChecker loads .reconignore from repo root."""
        root_reconignore = tmp_path / ".reconignore"
        root_reconignore.write_text("*.log\n")

        checker = IgnoreChecker(tmp_path)
        assert checker.should_ignore(tmp_path / "debug.log")
        assert root_reconignore in checker.reconignore_paths

    def test_loads_nested_reconignore(self, tmp_path: Path) -> None:
        """IgnoreChecker loads .reconignore from subdirectories."""
        # Create nested .reconignore in subdir
        subdir = tmp_path / "src" / "lib"
        subdir.mkdir(parents=True)
        nested_reconignore = subdir / ".reconignore"
        nested_reconignore.write_text("*.tmp\n")

        checker = IgnoreChecker(tmp_path)

        # Nested pattern is prefixed with its directory
        # "src/lib/*.tmp" should match
        assert checker.is_excluded_rel("src/lib/cache.tmp")
        # But not files outside that directory
        assert not checker.is_excluded_rel("other.tmp")
        assert nested_reconignore in checker.reconignore_paths

    def test_loads_both_legacy_and_root_reconignore(self, tmp_path: Path) -> None:
        """IgnoreChecker loads both .recon/.reconignore and root .reconignore."""
        # Legacy location
        legacy = tmp_path / ".recon" / ".reconignore"
        legacy.parent.mkdir(parents=True)
        legacy.write_text("*.pyc\n")

        # Root location
        root = tmp_path / ".reconignore"
        root.write_text("*.log\n")

        checker = IgnoreChecker(tmp_path)

        # Both patterns should be active
        assert checker.is_excluded_rel("module.pyc")
        assert checker.is_excluded_rel("debug.log")
        assert legacy in checker.reconignore_paths
        assert root in checker.reconignore_paths

    def test_reconignore_paths_property(self, tmp_path: Path) -> None:
        """reconignore_paths returns all loaded .reconignore files."""
        # Create multiple .reconignore files
        (tmp_path / ".recon").mkdir()
        (tmp_path / ".recon" / ".reconignore").write_text("*.a\n")
        (tmp_path / ".reconignore").write_text("*.b\n")
        (tmp_path / "sub").mkdir()
        (tmp_path / "sub" / ".reconignore").write_text("*.c\n")

        checker = IgnoreChecker(tmp_path)

        # All three should be tracked
        assert len(checker.reconignore_paths) == 3
        assert tmp_path / ".recon" / ".reconignore" in checker.reconignore_paths
        assert tmp_path / ".reconignore" in checker.reconignore_paths
        assert tmp_path / "sub" / ".reconignore" in checker.reconignore_paths

    def test_compute_combined_hash(self, tmp_path: Path) -> None:
        """compute_combined_hash returns hash of all .reconignore contents."""
        (tmp_path / ".reconignore").write_text("*.log\n")

        checker = IgnoreChecker(tmp_path)
        hash1 = checker.compute_combined_hash()

        assert hash1 is not None
        assert len(hash1) == 64  # SHA-256 hex digest

        # Changing content changes hash
        (tmp_path / ".reconignore").write_text("*.log\n*.tmp\n")
        checker2 = IgnoreChecker(tmp_path)
        hash2 = checker2.compute_combined_hash()

        assert hash2 != hash1

    def test_compute_combined_hash_none_when_no_files(self, tmp_path: Path) -> None:
        """compute_combined_hash returns None when no .reconignore files exist."""
        checker = IgnoreChecker(tmp_path)
        assert checker.compute_combined_hash() is None

    def test_compute_combined_hash_includes_multiple_files(self, tmp_path: Path) -> None:
        """compute_combined_hash includes all .reconignore files."""
        (tmp_path / ".reconignore").write_text("*.log\n")
        (tmp_path / "sub").mkdir()
        (tmp_path / "sub" / ".reconignore").write_text("*.tmp\n")

        checker = IgnoreChecker(tmp_path)
        hash1 = checker.compute_combined_hash()

        # Changing nested file changes overall hash
        (tmp_path / "sub" / ".reconignore").write_text("*.bak\n")
        checker2 = IgnoreChecker(tmp_path)
        hash2 = checker2.compute_combined_hash()

        assert hash2 != hash1

    def test_nested_pattern_prefix_is_correct(self, tmp_path: Path) -> None:
        """Nested .reconignore patterns are prefixed with relative directory."""
        # Create deeply nested .reconignore
        deep = tmp_path / "a" / "b" / "c"
        deep.mkdir(parents=True)
        (deep / ".reconignore").write_text("secret.txt\n")

        checker = IgnoreChecker(tmp_path)

        # Pattern "a/b/c/secret.txt" should match
        assert checker.is_excluded_rel("a/b/c/secret.txt")
        # But not same filename elsewhere
        assert not checker.is_excluded_rel("secret.txt")
        assert not checker.is_excluded_rel("a/secret.txt")
