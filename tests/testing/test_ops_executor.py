"""Smoke test for ops_executor module import."""

from coderecon.testing.ops_executor import _execute_tests


def test_execute_tests_is_async():
    import asyncio

    assert asyncio.iscoroutinefunction(_execute_tests)
