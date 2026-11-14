"""Read-only circuit analysis and bottleneck detection."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from engine.candidate_validator import _directory_hash, _file_hash


@dataclass(frozen=True)
class CircuitAnalysis:
    benchmark_id: str
    artifact_path: str
    artifact_sha256: str
    observations: Mapping[str, str]
    bottlenecks: tuple[str, ...]
    evidence_status: str = "STRUCTURAL_ONLY"


class CircuitAnalyst:
    """Analyze artifact metadata without executing or modifying circuits."""

    def analyze(self, benchmark_id: str, artifact: Path) -> CircuitAnalysis:
        if not artifact.exists():
            raise FileNotFoundError(f"Circuit artifact does not exist: {artifact}")
        digest = _directory_hash(artifact) if artifact.is_dir() else _file_hash(artifact)
        observations = {
            "kind": "directory" if artifact.is_dir() else "file",
            "size_bytes": str(
                sum(item.stat().st_size for item in artifact.rglob("*") if item.is_file())
                if artifact.is_dir()
                else artifact.stat().st_size
            ),
        }
        return CircuitAnalysis(
            benchmark_id=benchmark_id.upper(),
            artifact_path=str(artifact),
            artifact_sha256=digest,
            observations=observations,
            bottlenecks=(),
        )
