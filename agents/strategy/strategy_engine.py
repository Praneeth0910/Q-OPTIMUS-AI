"""Deterministic strategy ranking based on expected log-score improvement."""

from __future__ import annotations

import math
from dataclasses import dataclass

from evaluation.scoring import expected_log_score_gain


@dataclass(frozen=True)
class ResearchOpportunity:
    benchmark_id: str
    hypothesis: str
    expected_cost_reduction: float
    research_hours: float
    implementation_probability: float
    verification_probability: float
    verifier_hours: float
    memory_risk: float
    uncertainty: float
    evidence: str
    success_criteria: str
    abandonment_criteria: str
    competition_weight: float = 1 / 3


@dataclass(frozen=True)
class StrategyDecision:
    opportunity: ResearchOpportunity
    expected_log_score_per_hour: float
    rationale: str


class StrategyEngine:
    """Rank opportunities without comparing raw benchmark scores."""

    def rank(self, opportunities: list[ResearchOpportunity]) -> list[StrategyDecision]:
        decisions = [self.evaluate(item) for item in opportunities]
        return sorted(
            decisions,
            key=lambda item: item.expected_log_score_per_hour,
            reverse=True,
        )

    def evaluate(self, opportunity: ResearchOpportunity) -> StrategyDecision:
        _probability_range(opportunity.implementation_probability, "implementation_probability")
        _probability_range(opportunity.verification_probability, "verification_probability")
        _probability_range(opportunity.memory_risk, "memory_risk")
        _probability_range(opportunity.uncertainty, "uncertainty")
        _probability_range(opportunity.competition_weight, "competition_weight")
        if not 0 <= opportunity.expected_cost_reduction < 1:
            raise ValueError("expected_cost_reduction must be in [0, 1)")
        if opportunity.research_hours <= 0:
            raise ValueError("research_hours must be positive")
        if opportunity.verifier_hours < 0:
            raise ValueError("verifier_hours cannot be negative")

        log_gain = expected_log_score_gain(opportunity.expected_cost_reduction)
        expected_gain = (
            log_gain
            * opportunity.competition_weight
            * opportunity.implementation_probability
            * opportunity.verification_probability
            * (1 - opportunity.uncertainty)
            * (1 - opportunity.memory_risk)
        )
        total_hours = opportunity.research_hours + opportunity.verifier_hours
        value = expected_gain / total_hours
        rationale = (
            f"{opportunity.benchmark_id}: expected log-cost gain {expected_gain:.6g}, "
            f"weighted by implementation/verification probability, uncertainty, "
            f"memory risk, and {total_hours:.2f} total hours. Evidence: "
            f"{opportunity.evidence}. Success: {opportunity.success_criteria}. "
            f"Abandon: {opportunity.abandonment_criteria}."
        )
        return StrategyDecision(opportunity, value, rationale)


def _probability_range(value: float, name: str) -> None:
    if not 0 <= value <= 1:
        raise ValueError(f"{name} must be between 0 and 1")
