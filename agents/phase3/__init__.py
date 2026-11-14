"""Non-autonomous Phase 3 research and orchestration interfaces."""

from .hypotheses import ResearchHypothesis, HypothesisGenerator
from .circuit_analyst import CircuitAnalysis, CircuitAnalyst
from .candidate_generator import CandidateArtifact, CandidateGenerator
from .orchestrator import Phase3Orchestrator
from .specialists import (
    AlgorithmDesigner,
    CircuitEngineer,
    ResearchAgent,
    ResourceAnalyst,
    VerificationAgent,
)

__all__ = [
    "ResearchHypothesis",
    "HypothesisGenerator",
    "CircuitAnalysis",
    "CircuitAnalyst",
    "CandidateArtifact",
    "CandidateGenerator",
    "Phase3Orchestrator",
    "AlgorithmDesigner",
    "CircuitEngineer",
    "ResearchAgent",
    "ResourceAnalyst",
    "VerificationAgent",
]
