"""Smoke test for ops_move module."""

from coderecon.refactor.ops_move import _MoveMixin


def test_move_mixin_has_move_method():
    assert hasattr(_MoveMixin, "move")
