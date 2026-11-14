"""Fail-closed structural candidate validation."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from benchmarks.registry import get_benchmark


class CandidateStatus(StrEnum):
    GENERATED = "GENERATED"
    STRUCTURALLY_VALID = "STRUCTURALLY_VALID"
    VERIFICATION_PENDING = "VERIFICATION_PENDING"
    VERIFIED_PASS = "VERIFIED_PASS"
    VERIFIED_FAIL = "VERIFIED_FAIL"
    ERROR = "ERROR"
    ARCHIVED = "ARCHIVED"


@dataclass(frozen=True)
class CandidateValidation:
    benchmark_id: str
    candidate_id: str
    status: CandidateStatus
    artifact_sha256: str | None
    errors: tuple[str, ...]


def validate_candidate(
    benchmark_id: str,
    candidate_id: str,
    artifact: Path,
) -> CandidateValidation:
    benchmark = get_benchmark(benchmark_id)
    errors: list[str] = []
    if not artifact.exists():
        errors.append(f"Candidate artifact does not exist: {artifact}")
        return CandidateValidation(
            benchmark.benchmark_id, candidate_id, CandidateStatus.ERROR, None, tuple(errors)
        )
    if benchmark.benchmark_id == "DFTHCSOS":
        required = [
            artifact / f"angle_k_{index}.kmb" for index in range(10)
        ] + [artifact / "submission.npz"]
        errors.extend(f"Missing required file: {path.name}" for path in required if not path.is_file())
        digest = _directory_hash(artifact) if artifact.is_dir() else None
    else:
        if not artifact.is_file() or artifact.suffix.lower() != ".kmb":
            errors.append("Candidate must be a .kmb file")
        digest = _file_hash(artifact) if artifact.is_file() else None
    status = CandidateStatus.STRUCTURALLY_VALID if not errors else CandidateStatus.ERROR
    return CandidateValidation(
        benchmark.benchmark_id, candidate_id, status, digest, tuple(errors)
    )


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _directory_hash(path: Path) -> str:
    digest = hashlib.sha256()
    for child in sorted(item for item in path.rglob("*") if item.is_file()):
        digest.update(child.relative_to(path).as_posix().encode())
        digest.update(child.read_bytes())
    return digest.hexdigest()
