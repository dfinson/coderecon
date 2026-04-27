"""Tests for index/_internal/discovery/scanner.py — ContextDiscovery."""

from coderecon.index._internal.discovery.scanner import (
    ContextDiscovery,
    DiscoveredMarker,
    DiscoveryResult,
)
from coderecon.index.models import LanguageFamily, MarkerTier

def test_discovery_result_defaults():
    """DiscoveryResult initializes with empty lists."""
    r = DiscoveryResult()
    assert r.candidates == []
    assert r.markers == []
    assert r.errors == []

def test_discovered_marker_construction():
    """DiscoveredMarker stores path, family, and tier."""
    m = DiscoveredMarker(path="Cargo.toml", family=LanguageFamily.RUST, tier=MarkerTier.PACKAGE)
    assert m.path == "Cargo.toml"
    assert m.family == LanguageFamily.RUST
    assert m.tier == MarkerTier.PACKAGE

def test_discover_all_empty_repo(tmp_path):
    """discover_all on an empty dir returns a root fallback candidate."""
    cd = ContextDiscovery(tmp_path)
    result = cd.discover_all()
    assert isinstance(result, DiscoveryResult)
    # Should always have at least the root fallback candidate
    fallback = [c for c in result.candidates if c.is_root_fallback]
    assert len(fallback) == 1
    assert fallback[0].language_family == LanguageFamily.UNKNOWN

def test_discover_all_detects_python(tmp_path):
    """discover_all detects a Python project from pyproject.toml."""
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'foo'\n")
    cd = ContextDiscovery(tmp_path)
    result = cd.discover_all()
    families = {c.language_family for c in result.candidates}
    assert LanguageFamily.PYTHON in families

def test_discover_all_detects_javascript(tmp_path):
    """discover_all detects a JS project from package.json."""
    (tmp_path / "package.json").write_text('{"name": "test"}')
    cd = ContextDiscovery(tmp_path)
    result = cd.discover_all()
    families = {c.language_family for c in result.candidates}
    assert LanguageFamily.JAVASCRIPT in families

def test_discover_family_python(tmp_path):
    """discover_family(PYTHON) finds pyproject.toml marker."""
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'foo'\n")
    cd = ContextDiscovery(tmp_path)
    result = cd.discover_family(LanguageFamily.PYTHON)
    assert len(result.markers) > 0
    assert all(m.family == LanguageFamily.PYTHON for m in result.markers)

def test_discover_family_no_markers(tmp_path):
    """discover_family returns empty markers when no matching files exist."""
    cd = ContextDiscovery(tmp_path)
    result = cd.discover_family(LanguageFamily.RUST)
    assert result.markers == []

def test_discover_all_prunes_node_modules(tmp_path):
    """discover_all skips node_modules directories."""
    nm = tmp_path / "node_modules" / "pkg"
    nm.mkdir(parents=True)
    (nm / "package.json").write_text('{"name": "dep"}')
    (tmp_path / "package.json").write_text('{"name": "root"}')
    cd = ContextDiscovery(tmp_path)
    result = cd.discover_all()
    marker_paths = [m.path for m in result.markers]
    assert not any("node_modules" in p for p in marker_paths)

def test_js_workspace_detection(tmp_path):
    """discover_all promotes package.json with workspaces to WORKSPACE tier."""
    (tmp_path / "package.json").write_text('{"name": "root", "workspaces": ["packages/*"]}')
    cd = ContextDiscovery(tmp_path)
    result = cd.discover_all()
    js_markers = [m for m in result.markers if m.family == LanguageFamily.JAVASCRIPT]
    workspace_markers = [m for m in js_markers if m.tier == MarkerTier.WORKSPACE]
    assert len(workspace_markers) >= 1
