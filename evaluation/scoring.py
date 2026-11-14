"""Composition helpers for official benchmark costs."""

from __future__ import annotations

import math
from typing import Mapping


def geometric_mean_score(costs: Mapping[str, float], references: Mapping[str, float]) -> float:
    """Return the competition-relative geometric mean improvement."""

    ratios = []
    for benchmark_id, reference in references.items():
        candidate = costs.get(benchmark_id)
        if candidate is None or candidate <= 0 or reference <= 0:
            raise ValueError(f"Missing or invalid cost for {benchmark_id}")
        ratios.append(math.log(reference / candidate))
    return math.exp(sum(ratios) / len(ratios))


def expected_log_score_gain(cost_reduction: float) -> float:
    """Return log(reference/candidate) for a fractional cost reduction."""

    if not 0 <= cost_reduction < 1:
        raise ValueError("cost_reduction must be in [0, 1)")
    return -math.log1p(-cost_reduction)
