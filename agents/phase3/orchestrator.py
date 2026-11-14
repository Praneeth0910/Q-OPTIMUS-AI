"""Phase 3 orchestration with autonomous execution disabled by default."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from agents.phase3.hypotheses import HypothesisGenerator, ResearchHypothesis
from engine.experiment_runner import ExperimentRunner


@dataclass(frozen=True)
class ResearchPlan:
    benchmark_id: str
    hypotheses: tuple[ResearchHypothesis, ...]
    execution_enabled: bool = False


class Phase3Orchestrator:
    """Coordinate planning only until the Phase 2 gate is explicitly enabled."""

    def __init__(
        self,
        experiment_runner: ExperimentRunner,
        hypothesis_generator: HypothesisGenerator | None = None,
        *,
        execution_enabled: bool = False,
    ):
        self.experiment_runner = experiment_runner
        self.hypothesis_generator = hypothesis_generator or HypothesisGenerator()
        self.execution_enabled = execution_enabled

    def plan(self, benchmark_id: str) -> ResearchPlan:
        return ResearchPlan(
            benchmark_id.upper(),
            self.hypothesis_generator.generate(benchmark_id),
            self.execution_enabled,
        )

    def execute_candidate(self, **kwargs):
        if not self.execution_enabled:
            raise PermissionError(
                "Phase 3 autonomous candidate execution is disabled until the "
                "Phase 2 acceptance gate passes"
            )
        return self.experiment_runner.evaluate(**kwargs)
