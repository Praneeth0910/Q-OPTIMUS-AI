"""Immutable registry for the three competition benchmark references."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping
from types import MappingProxyType

import yaml


_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
_REGISTRY_PATH = _REPOSITORY_ROOT / "config" / "benchmarks.yaml"


@dataclass(frozen=True)
class BaselineRecord:
    """Historical reference measurement with explicit provenance."""

    reference_artifact: str
    reference_sha256: str
    verification_status: str
    official_score: float | None
    resource_metrics: Mapping[str, Any]
    verification_parameters: Mapping[str, Any]
    runtime_seconds: float | None
    peak_memory_kb: int | None
    recorded_at: str | None
    source: str
    values_verified: bool


@dataclass(frozen=True)
class BenchmarkDefinition:
    """Stable benchmark identity and its immutable baseline."""

    benchmark_id: str
    name: str
    objective: str
    verifier_path: str
    candidate_structure: str
    baseline: BaselineRecord


def _load_registry() -> dict[str, BenchmarkDefinition]:
    with _REGISTRY_PATH.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}

    definitions: dict[str, BenchmarkDefinition] = {}
    for benchmark_id, value in raw.get("benchmarks", {}).items():
        baseline = value["baseline"]
        record = BaselineRecord(
            reference_artifact=baseline["reference_artifact"],
            reference_sha256=baseline["reference_sha256"],
            verification_status=baseline["verification_status"],
            official_score=baseline.get("official_score"),
            resource_metrics=MappingProxyType(
                dict(baseline.get("resource_metrics", {}))
            ),
            verification_parameters=MappingProxyType(
                dict(baseline.get("verification_parameters", {}))
            ),
            runtime_seconds=baseline.get("runtime_seconds"),
            peak_memory_kb=baseline.get("peak_memory_kb"),
            recorded_at=baseline.get("recorded_at"),
            source=baseline["source"],
            values_verified=bool(baseline["values_verified"]),
        )
        definitions[benchmark_id] = BenchmarkDefinition(
            benchmark_id=benchmark_id,
            name=value["name"],
            objective=value["objective"],
            verifier_path=value["verifier_path"],
            candidate_structure=value["candidate_structure"],
            baseline=record,
        )
    return definitions


def list_benchmarks() -> tuple[BenchmarkDefinition, ...]:
    """Return all registered benchmarks in configuration order."""

    return tuple(_load_registry().values())


def get_benchmark(benchmark_id: str) -> BenchmarkDefinition:
    """Return a benchmark or raise a clear error for an unknown ID."""

    try:
        return _load_registry()[benchmark_id.upper()]
    except KeyError as exc:
        raise KeyError(f"Unknown benchmark ID: {benchmark_id}") from exc


def get_baseline(benchmark_id: str) -> BaselineRecord:
    """Return the immutable baseline for ``benchmark_id``."""

    return get_benchmark(benchmark_id).baseline


def validate_reference_artifact(
    benchmark_id: str,
    repository_root: Path | None = None,
) -> str:
    """Validate a configured reference path and its SHA-256.

    Returns the computed hash. Missing artifacts and hash mismatches are
    explicit errors; a historical PASS does not make an absent local file valid.
    """

    benchmark = get_benchmark(benchmark_id)
    root = repository_root or _REPOSITORY_ROOT
    path = root / benchmark.baseline.reference_artifact
    if not path.exists():
        raise FileNotFoundError(
            f"Reference artifact for {benchmark_id} does not exist: {path}"
        )
    if path.is_dir():
        if benchmark.baseline.reference_sha256 == "not-applicable-directory-reference":
            return benchmark.baseline.reference_sha256
        raise IsADirectoryError(f"Reference artifact is a directory: {path}")

    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != benchmark.baseline.reference_sha256:
        raise ValueError(
            f"Reference hash mismatch for {benchmark_id}: "
            f"expected {benchmark.baseline.reference_sha256}, got {digest}"
        )
    return digest
