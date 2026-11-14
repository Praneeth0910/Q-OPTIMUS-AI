"""Verified-only candidate archive with metric-aware Pareto filtering."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from benchmarks.registry import get_baseline
from evaluation.verifier_adapter import VerificationResult, VerificationStatus


@dataclass(frozen=True)
class ArchivedCandidate:
    candidate_id: str
    benchmark_id: str
    score: float
    costs: Mapping[str, float]
    result: VerificationResult


class ParetoArchive:
    """Keep verified candidates and retain incomparable trade-offs."""

    def __init__(self, reference_scores: Mapping[str, float] | None = None) -> None:
        self._items: dict[str, ArchivedCandidate] = {}
        self._reference_scores = dict(reference_scores or {})

    def promote(
        self,
        result: VerificationResult,
        costs: Mapping[str, float],
    ) -> bool:
        if result.status is not VerificationStatus.PASS or not result.official_run:
            return False
        if result.official_score is None:
            return False
        if result.official_score <= 0 or not costs:
            return False
        if any(value <= 0 for value in costs.values()):
            return False
        reference_score = self._reference_scores.get(result.benchmark_id)
        if reference_score is None:
            reference_score = get_baseline(result.benchmark_id).official_score
        if reference_score is None or result.official_score >= reference_score:
            return False
        candidate = ArchivedCandidate(
            result.candidate_id, result.benchmark_id, result.official_score,
            dict(costs), result,
        )
        existing = [
            item for item in self._items.values()
            if item.benchmark_id == candidate.benchmark_id
        ]
        if any(_dominates(item.costs, candidate.costs) for item in existing):
            return False
        for item in existing:
            if _dominates(candidate.costs, item.costs):
                self._items.pop(item.candidate_id, None)
        self._items[candidate.candidate_id] = candidate
        return True

    def candidates(self, benchmark_id: str | None = None) -> tuple[ArchivedCandidate, ...]:
        values = tuple(self._items.values())
        if benchmark_id is None:
            return values
        return tuple(item for item in values if item.benchmark_id == benchmark_id)


def _dominates(left: Mapping[str, float], right: Mapping[str, float]) -> bool:
    keys = set(left) | set(right)
    if any(key not in left or key not in right for key in keys):
        return False
    no_worse = all(left[key] <= right[key] for key in keys)
    strictly_better = any(left[key] < right[key] for key in keys)
    return no_worse and strictly_better
