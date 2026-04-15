"""Shared fixtures for index tests."""

from __future__ import annotations

import tempfile
from collections.abc import Generator
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from coderecon.index._internal.db import Database


@pytest.fixture
def temp_dir() -> Generator[Path, None, None]:
    """Create a temporary directory for tests."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def temp_db(temp_dir: Path) -> Generator[Database, None, None]:
    """Create a temporary database with schema."""
    from coderecon.index._internal.db import Database, create_additional_indexes

    db_path = temp_dir / "test.db"
    db = Database(db_path)
    db.create_all()
    create_additional_indexes(db.engine)
    yield db


@pytest.fixture
def temp_repo(temp_dir: Path) -> Generator[Path, None, None]:
    """Create a temporary git repository."""
    import subprocess

    repo_path = temp_dir / "repo"
    repo_path.mkdir()
    subprocess.run(["git", "init"], cwd=repo_path, capture_output=True, check=True)

    # Configure git user for commits
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo_path, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_path, capture_output=True, check=True)

    # Create initial commit
    (repo_path / "README.md").write_text("# Test Repo\n")
    subprocess.run(["git", "add", "README.md"], cwd=repo_path, capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=repo_path, capture_output=True, check=True)

    yield repo_path


@pytest.fixture
def temp_repo_with_db(
    temp_repo: Path, temp_dir: Path
) -> Generator[tuple[Path, Database], None, None]:
    """Create a temporary repo with an associated database."""
    from coderecon.index._internal.db import Database, create_additional_indexes

    db_path = temp_dir / "index.db"
    db = Database(db_path)
    db.create_all()
    create_additional_indexes(db.engine)
    yield temp_repo, db


@pytest.fixture
def sample_python_content() -> str:
    """Sample Python content for parsing tests."""
    return '''"""Sample module."""

def hello(name: str) -> str:
    """Say hello."""
    return f"Hello, {name}!"


class Greeter:
    """A greeter class."""

    def __init__(self, prefix: str = "Hello"):
        self.prefix = prefix

    def greet(self, name: str) -> str:
        """Greet someone."""
        return f"{self.prefix}, {name}!"


CONSTANT = 42
'''


@pytest.fixture
def sample_javascript_content() -> str:
    """Sample JavaScript content for parsing tests."""
    return """// Sample module

function hello(name) {
    return `Hello, ${name}!`;
}

class Greeter {
    constructor(prefix = "Hello") {
        this.prefix = prefix;
    }

    greet(name) {
        return `${this.prefix}, ${name}!`;
    }
}

const CONSTANT = 42;

export { hello, Greeter, CONSTANT };
"""


@pytest.fixture
def sample_go_content() -> str:
    """Sample Go content for parsing tests."""
    return """package main

import "fmt"

func Hello(name string) string {
    return fmt.Sprintf("Hello, %s!", name)
}

type Greeter struct {
    Prefix string
}

func (g *Greeter) Greet(name string) string {
    return fmt.Sprintf("%s, %s!", g.Prefix, name)
}

const Constant = 42
"""
