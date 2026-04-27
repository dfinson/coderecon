"""Smoke tests for index enum definitions."""

from coderecon.index.models_enums import (
    BindReasonCode,
    BindTargetKind,
    Certainty,
    DynamicAccessPattern,
    ExportThunkMode,
    Freshness,
    ImportKind,
    LanguageFamily,
    MarkerTier,
    ProbeStatus,
    RefTier,
    ResolutionMethod,
    Role,
    ScopeKind,
)


def test_language_family_members():
    assert "python" in [m.value for m in LanguageFamily]
    assert "javascript" in [m.value for m in LanguageFamily]


def test_freshness_members():
    assert len(Freshness) > 0


def test_all_enums_are_str_enums():
    for enum_cls in (
        LanguageFamily,
        Freshness,
        Certainty,
        RefTier,
        Role,
        ScopeKind,
        BindTargetKind,
        BindReasonCode,
        ImportKind,
        ExportThunkMode,
        DynamicAccessPattern,
        ProbeStatus,
        MarkerTier,
        ResolutionMethod,
    ):
        for member in enum_cls:
            assert isinstance(member.value, str), f"{enum_cls.__name__}.{member.name} is not a str"
