"""AC-011: exponential backoff produces a monotonically increasing delay
sequence (bounded jitter) for attempts 1, 2, 3 — jitter-proof by
construction (spacing 2/4/8 exceeds jitter_max=0.5), so this is
deterministic with zero flake risk without needing to mock random.uniform.
"""

from __future__ import annotations

from unittest.mock import patch

from orchestrator.state_machine import compute_backoff


def test_backoff_monotonically_increasing_at_jitter_extremes():
    # Worst case for attempt N (max jitter) must still be less than best
    # case for attempt N+1 (zero jitter).
    with patch("random.uniform", return_value=0.5):
        max_1 = compute_backoff(1)
        max_2 = compute_backoff(2)
        max_3 = compute_backoff(3)

    with patch("random.uniform", return_value=0.0):
        min_1 = compute_backoff(1)
        min_2 = compute_backoff(2)
        min_3 = compute_backoff(3)

    assert min_1 == 2.0
    assert max_1 == 2.5
    assert min_2 == 4.0
    assert max_2 == 4.5
    assert min_3 == 8.0
    assert max_3 == 8.5

    assert max_1 < min_2
    assert max_2 < min_3


def test_backoff_real_jitter_stays_within_bounds():
    for attempt, base in [(1, 2.0), (2, 4.0), (3, 8.0)]:
        delay = compute_backoff(attempt)
        assert base <= delay <= base + 0.5
