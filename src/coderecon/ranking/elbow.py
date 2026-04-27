"""Elbow-based cutoff — model-free N prediction.

Finds the largest score gap between consecutive ranked items and uses
that position as the cutoff.  Used as a fallback when the LightGBM
cutoff model is unavailable.
"""

from __future__ import annotations

def elbow_cut(scores: list[float], *, min_n: int = 3, max_n: int = 30) -> int:
    """Return the elbow position in a descending score list.

    The elbow is the index of the largest gap between consecutive scores.
    Result is clamped to [*min_n*, min(*max_n*, len(scores))].

    Parameters
    ----------
    scores
        Scores in descending order (e.g. RRF scores after sorting).
    min_n, max_n
        Hard bounds on the returned cutoff.
    """
    n = len(scores)
    if n <= min_n:
        return n

    upper = min(max_n, n)

    gaps = [scores[i] - scores[i + 1] for i in range(n - 1)]
    if not gaps or max(gaps) == 0:
        return upper

    elbow = gaps.index(max(gaps)) + 1  # position *after* the gap
    return max(min_n, min(elbow, upper))
