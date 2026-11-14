"""Specialist interfaces used by the Phase 3 orchestrator."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Protocol


@dataclass(frozen=True)
class SpecialistFinding:
    specialist: str
    benchmark_id: str
    statement: str
    evidence: tuple[str, ...]
    confidence: str


class AlgorithmDesigner(Protocol):
    def design(self, benchmark_id: str, hypothesis: str) -> SpecialistFinding:
        ...


class VerificationAgent(Protocol):
    def assess(self, benchmark_id: str, candidate_id: str) -> SpecialistFinding:
        ...


class ResourceAnalyst(Protocol):
    def analyze(self, benchmark_id: str, metrics: Mapping[str, object]) -> SpecialistFinding:
        ...


class ResearchAgent(Protocol):
    def investigate(self, hypothesis: str) -> SpecialistFinding:
        ...


class CircuitEngineer(Protocol):
    def synthesize(self, benchmark_id: str, hypothesis: str) -> SpecialistFinding:
        ...
