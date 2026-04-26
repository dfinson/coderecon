"""Tests for GovernanceConfig in config models."""

from __future__ import annotations


class TestGovernanceConfig:
    def test_default_config(self) -> None:
        from coderecon.config.models import GovernanceConfig

        gov = GovernanceConfig()
        # All rules should have sensible defaults
        assert not gov.coverage_floor.enabled
        assert not gov.lint_clean.enabled
        assert not gov.no_new_cycles.enabled
        assert gov.test_debt.enabled  # test debt is on by default
        assert not gov.coverage_regression.enabled
        assert not gov.module_boundary.enabled
        assert not gov.centrality_impact.enabled

    def test_custom_config(self) -> None:
        from coderecon.config.models import GovernanceConfig, GovernancePolicyRule

        gov = GovernanceConfig(
            coverage_floor=GovernancePolicyRule(
                enabled=True, level="error", threshold=90.0
            ),
            lint_clean=GovernancePolicyRule(enabled=True, level="error"),
        )
        assert gov.coverage_floor.enabled
        assert gov.coverage_floor.threshold == 90.0
        assert gov.lint_clean.enabled
        assert gov.lint_clean.level == "error"

    def test_in_coderecon_config(self) -> None:
        from coderecon.config.models import CodeReconConfig

        config = CodeReconConfig()
        assert hasattr(config, "governance")
        assert config.governance.test_debt.enabled

    def test_policy_rule_defaults(self) -> None:
        from coderecon.config.models import GovernancePolicyRule

        rule = GovernancePolicyRule()
        assert rule.enabled
        assert rule.level == "warning"
        assert rule.threshold is None
        assert rule.message == ""
