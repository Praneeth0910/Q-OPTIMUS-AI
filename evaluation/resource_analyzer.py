"""Benchmark-independent resource normalization and comparisons."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


RESOURCE_KEYS = (
    "qubits",
    "classical_bits",
    "toffoli_count",
    "toffoli_equivalent_count",
    "t_count",
    "rotation_count",
    "reaction_depth",
    "instruction_count",
    "runtime_seconds",
    "peak_memory_kb",
    "verified_shots",
)


@dataclass(frozen=True)
class ResourceMetrics:
    """Normalized metrics; ``None`` means unavailable, never zero."""

    values: Mapping[str, float | int | None]

    def get(self, name: str) -> float | int | None:
        return self.values.get(name)


@dataclass(frozen=True)
class ResourceComparison:
    absolute_reduction: Mapping[str, float | int | None]
    percentage_reduction: Mapping[str, float | None]
    relative_score_improvement: float | None
    runtime_overhead_seconds: float | None
    memory_overhead_kb: int | None


def normalize_metrics(
    raw: Mapping[str, Any],
    *,
    runtime_seconds: float | None = None,
    peak_memory_kb: int | None = None,
    verified_shots: int | None = None,
) -> ResourceMetrics:
    values = {key: raw.get(key) for key in RESOURCE_KEYS}
    if runtime_seconds is not None:
        values["runtime_seconds"] = runtime_seconds
    if peak_memory_kb is not None:
        values["peak_memory_kb"] = peak_memory_kb
    if verified_shots is not None:
        values["verified_shots"] = verified_shots
    return ResourceMetrics(values)


def compare_resources(
    reference: ResourceMetrics,
    candidate: ResourceMetrics,
    *,
    reference_score: float | None = None,
    candidate_score: float | None = None,
) -> ResourceComparison:
    absolute: dict[str, float | int | None] = {}
    percentage: dict[str, float | None] = {}
    for key in RESOURCE_KEYS:
        old, new = reference.get(key), candidate.get(key)
        if not isinstance(old, (int, float)) or not isinstance(new, (int, float)):
            absolute[key] = None
            percentage[key] = None
            continue
        absolute[key] = old - new
        percentage[key] = None if old == 0 else (old - new) / old * 100

    score_improvement = None
    if (
        isinstance(reference_score, (int, float))
        and isinstance(candidate_score, (int, float))
        and candidate_score > 0
    ):
        score_improvement = reference_score / candidate_score - 1
    runtime_overhead = None
    if isinstance(reference.get("runtime_seconds"), (int, float)) and isinstance(
        candidate.get("runtime_seconds"), (int, float)
    ):
        runtime_overhead = candidate.get("runtime_seconds") - reference.get(
            "runtime_seconds"
        )
    memory_overhead = None
    if isinstance(reference.get("peak_memory_kb"), int) and isinstance(
        candidate.get("peak_memory_kb"), int
    ):
        memory_overhead = candidate.get("peak_memory_kb") - reference.get(
            "peak_memory_kb"
        )
    return ResourceComparison(
        absolute_reduction=absolute,
        percentage_reduction=percentage,
        relative_score_improvement=score_improvement,
        runtime_overhead_seconds=runtime_overhead,
        memory_overhead_kb=memory_overhead,
    )
