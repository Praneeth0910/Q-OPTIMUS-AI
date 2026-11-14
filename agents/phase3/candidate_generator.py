"""Safe candidate-generation boundary; no implementation claims."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Protocol


@dataclass(frozen=True)
class CandidateArtifact:
    candidate_id: str
    benchmark_id: str
    artifact_path: Path
    source_revision: str | None
    parameters: Mapping[str, object]
    generated: bool = True


class CandidateGenerator(Protocol):
    """Implementations must write only to an authorized candidate directory."""

    def generate(
        self,
        benchmark_id: str,
        hypothesis: str,
        output_dir: Path,
        parameters: Mapping[str, object],
    ) -> CandidateArtifact:
        """Generate a candidate artifact; verification is a separate step."""


class UnimplementedCandidateGenerator:
    """Explicit placeholder that cannot be mistaken for a generator."""

    def generate(self, *args, **kwargs) -> CandidateArtifact:
        raise NotImplementedError(
            "Phase 3 candidate synthesis is not implemented; no candidate generated"
        )
