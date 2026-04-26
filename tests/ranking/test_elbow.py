"""Tests for coderecon.ranking.elbow."""

from __future__ import annotations

import pytest

from coderecon.ranking.elbow import elbow_cut


class TestElbowCut:
    """Tests for elbow_cut."""

    def test_empty_list(self) -> None:
        assert elbow_cut([]) == 0

    def test_fewer_than_min_n(self) -> None:
        assert elbow_cut([5.0, 4.0], min_n=3) == 2

    def test_exactly_min_n(self) -> None:
        assert elbow_cut([5.0, 4.0, 3.0], min_n=3) == 3

    def test_clear_elbow(self) -> None:
        # Big gap between item 2 and 3 → elbow at 3
        scores = [10.0, 9.5, 9.0, 2.0, 1.5, 1.0]
        result = elbow_cut(scores, min_n=1, max_n=10)
        assert result == 3

    def test_uniform_scores_returns_upper(self) -> None:
        scores = [5.0, 5.0, 5.0, 5.0]
        result = elbow_cut(scores, min_n=1, max_n=10)
        assert result == 4  # All equal → upper bound

    def test_max_n_caps_result(self) -> None:
        scores = [10.0, 9.0, 8.0, 7.0, 1.0, 0.5, 0.1]
        result = elbow_cut(scores, min_n=1, max_n=3)
        assert result <= 3

    def test_min_n_floor(self) -> None:
        # Elbow at position 1 (gap between first two items)
        scores = [10.0, 1.0, 0.9, 0.8, 0.7]
        result = elbow_cut(scores, min_n=3, max_n=10)
        assert result >= 3

    def test_single_item(self) -> None:
        assert elbow_cut([5.0]) == 1

    def test_descending_equal_gaps(self) -> None:
        # Equal gaps → first gap wins (position 1)
        scores = [4.0, 3.0, 2.0, 1.0]
        result = elbow_cut(scores, min_n=1, max_n=10)
        assert result == 1

    def test_gap_at_end(self) -> None:
        scores = [10.0, 9.9, 9.8, 9.7, 1.0]
        result = elbow_cut(scores, min_n=1, max_n=10)
        assert result == 4
