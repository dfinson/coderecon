"""Integration tests for configuration edge cases."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml

from coderecon.config import load_config
from coderecon.config.user_config import (
    DEFAULT_LOG_LEVEL,
    DEFAULT_MAX_FILE_SIZE_MB,
    DEFAULT_PORT,
    UserConfig,
    write_user_config,
)
from coderecon._core.errors import ConfigError

pytestmark = pytest.mark.integration


class TestUserConfig:
    def test_defaults(self) -> None:
        cfg = UserConfig()
        assert cfg.port == DEFAULT_PORT
        assert cfg.max_file_size_mb == DEFAULT_MAX_FILE_SIZE_MB
        assert cfg.log_level == DEFAULT_LOG_LEVEL

    def test_custom_values(self) -> None:
        cfg = UserConfig(port=8080, log_level="DEBUG", max_file_size_mb=50)
        assert cfg.port == 8080
        assert cfg.log_level == "DEBUG"
        assert cfg.max_file_size_mb == 50

    def test_invalid_log_level_raises(self) -> None:
        with pytest.raises(Exception):
            UserConfig(log_level="INVALID")  # type: ignore[arg-type]


class TestWriteUserConfig:
    def test_write_default(self, tmp_path: Path) -> None:
        path = tmp_path / "config.yaml"
        write_user_config(path)
        assert path.exists()
        content = path.read_text()
        assert "port" in content

    def test_write_custom(self, tmp_path: Path) -> None:
        path = tmp_path / "config.yaml"
        cfg = UserConfig(port=9999, log_level="WARNING")
        write_user_config(path, cfg)
        content = path.read_text()
        assert "9999" in content
        assert "WARNING" in content

    def test_roundtrip(self, tmp_path: Path) -> None:
        """Write then load config — values should match."""
        path = tmp_path / "config.yaml"
        cfg = UserConfig(port=5555, log_level="ERROR", max_file_size_mb=100)
        write_user_config(path, cfg)

        # Load it back via yaml
        with path.open() as f:
            data = yaml.safe_load(f)
        assert data is not None
        assert data.get("port") == 5555 or data.get("server", {}).get("port") == 5555


class TestLoadConfig:
    def test_load_without_repo(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Load config with no repo root — should use defaults."""
        # Point global config to a non-existent path
        monkeypatch.setattr(
            "coderecon.config.loader.GLOBAL_CONFIG_PATH",
            tmp_path / "nonexistent" / "config.yaml",
        )
        # Clean env
        for k in list(os.environ.keys()):
            if k.startswith("CODERECON__"):
                monkeypatch.delenv(k)

        config = load_config()
        assert config is not None
        assert config.server.port == DEFAULT_PORT
        assert config.logging.level == DEFAULT_LOG_LEVEL

    def test_env_override(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Environment variables override file config."""
        monkeypatch.setattr(
            "coderecon.config.loader.GLOBAL_CONFIG_PATH",
            tmp_path / "nonexistent" / "config.yaml",
        )
        monkeypatch.setenv("CODERECON__LOGGING__LEVEL", "ERROR")
        config = load_config()
        assert config.logging.level == "ERROR"

    def test_load_with_repo_config(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Repo config takes precedence over global."""
        monkeypatch.setattr(
            "coderecon.config.loader.GLOBAL_CONFIG_PATH",
            tmp_path / "nonexistent" / "config.yaml",
        )
        # Clean env
        for k in list(os.environ.keys()):
            if k.startswith("CODERECON__"):
                monkeypatch.delenv(k)

        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / ".git").mkdir()
        recon_dir = repo / ".recon"
        recon_dir.mkdir()
        write_user_config(recon_dir / "config.yaml", UserConfig(log_level="WARNING"))

        config = load_config(repo_root=repo)
        assert config.logging.level == "WARNING"

    def test_invalid_yaml_in_global(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Invalid YAML in global config should raise ConfigError."""
        global_dir = tmp_path / "global"
        global_dir.mkdir(parents=True)
        config_path = global_dir / "config.yaml"
        config_path.write_text("{{invalid yaml: [broken")
        monkeypatch.setattr(
            "coderecon.config.loader.GLOBAL_CONFIG_PATH",
            config_path,
        )
        # Clean env
        for k in list(os.environ.keys()):
            if k.startswith("CODERECON__"):
                monkeypatch.delenv(k)

        with pytest.raises(ConfigError):
            load_config()
