"""Tests for CLI global commands (catalog, register, unregister, worktrees, global-status)."""

from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from coderecon.cli.global_commands import (
    catalog_command,
    global_status_command,
    unregister_command,
    worktrees_command,
)

def test_catalog_command_no_repos():
    """catalog prints a hint when no repos are registered."""
    runner = CliRunner()
    mock_registry = MagicMock()
    mock_registry.list_repos.return_value = []
    with patch("coderecon.adapters.catalog.CatalogDB"), \
         patch("coderecon.adapters.catalog.CatalogRegistry", return_value=mock_registry):
        result = runner.invoke(catalog_command)
    assert result.exit_code == 0
    assert "No repositories registered" in result.output

def test_catalog_command_lists_repos():
    """catalog lists registered repos with their details."""
    runner = CliRunner()
    repo = MagicMock(id=1, name="my-repo", git_dir="/tmp/git", storage_dir="/tmp/store")
    wt = MagicMock(name="main", is_main=True)
    mock_registry = MagicMock()
    mock_registry.list_repos.return_value = [repo]
    mock_registry.list_worktrees.return_value = [wt]

    with patch("coderecon.adapters.catalog.CatalogDB"), \
         patch("coderecon.adapters.catalog.CatalogRegistry", return_value=mock_registry):
        result = runner.invoke(catalog_command)
    assert result.exit_code == 0
    assert "my-repo" in result.output

def test_unregister_command_daemon_not_running(tmp_path):
    """unregister falls back to direct catalog write when daemon not running."""
    runner = CliRunner()
    mock_registry = MagicMock()
    mock_registry.unregister.return_value = True
    with patch("coderecon.adapters.catalog.CatalogDB"), \
         patch("coderecon.adapters.catalog.CatalogRegistry", return_value=mock_registry), \
         patch("coderecon.cli.utils.find_repo_root", return_value=tmp_path), \
         patch("coderecon.daemon.global_lifecycle.is_global_server_running", return_value=False):
        result = runner.invoke(unregister_command)
    assert result.exit_code == 0
    assert "Unregistered" in result.output

def test_unregister_command_not_registered(tmp_path):
    """unregister prints 'Not registered' when repo isn't in catalog."""
    runner = CliRunner()
    mock_registry = MagicMock()
    mock_registry.unregister.return_value = False
    with patch("coderecon.adapters.catalog.CatalogDB"), \
         patch("coderecon.adapters.catalog.CatalogRegistry", return_value=mock_registry), \
         patch("coderecon.cli.utils.find_repo_root", return_value=tmp_path), \
         patch("coderecon.daemon.global_lifecycle.is_global_server_running", return_value=False):
        result = runner.invoke(unregister_command)
    assert result.exit_code == 0
    assert "Not registered" in result.output

def test_worktrees_command_repo_not_found(tmp_path):
    """worktrees prints message when repo not in catalog."""
    runner = CliRunner()
    mock_registry = MagicMock()
    mock_registry.lookup_by_path.return_value = None
    with patch("coderecon.adapters.catalog.CatalogDB"), \
         patch("coderecon.adapters.catalog.CatalogRegistry", return_value=mock_registry), \
         patch("coderecon.cli.utils.find_repo_root", return_value=tmp_path):
        result = runner.invoke(worktrees_command)
    assert result.exit_code == 0
    assert "not found" in result.output

def test_global_status_not_running():
    """global-status reports daemon not running."""
    runner = CliRunner()
    with patch("coderecon.daemon.global_lifecycle.is_global_server_running", return_value=False):
        result = runner.invoke(global_status_command)
    assert result.exit_code == 0
    assert "not running" in result.output

def test_global_status_running():
    """global-status shows PID and port when daemon is running."""
    runner = CliRunner()
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"active_repos": ["repo-a"]}
    with patch("coderecon.daemon.global_lifecycle.is_global_server_running", return_value=True), \
         patch("coderecon.daemon.global_lifecycle.read_global_server_info", return_value=(1234, 8080)), \
         patch.dict("sys.modules", {"httpx": MagicMock(get=MagicMock(return_value=mock_resp))}):
        result = runner.invoke(global_status_command)
    assert result.exit_code == 0
    assert "1234" in result.output
    assert "8080" in result.output
