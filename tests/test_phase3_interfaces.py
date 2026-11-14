from __future__ import annotations

from pathlib import Path

import pytest

from agents.phase3.candidate_generator import UnimplementedCandidateGenerator
from agents.phase3.circuit_analyst import CircuitAnalyst
from agents.phase3.hypotheses import HypothesisGenerator
from agents.phase3.orchestrator import Phase3Orchestrator


def test_hypotheses_cover_each_benchmark():
    generator = HypothesisGenerator()
    assert len(generator.generate("INVSQRT")) == 4
    assert len(generator.generate("DQI")) == 4
    assert len(generator.generate("DFTHCSOS")) == 4


def test_circuit_analysis_is_read_only_and_hashed(tmp_path: Path):
    artifact = tmp_path / "candidate.kmb"
    artifact.write_bytes(b"candidate")
    analysis = CircuitAnalyst().analyze("INVSQRT", artifact)
    assert analysis.artifact_sha256
    assert analysis.evidence_status == "STRUCTURAL_ONLY"


def test_unimplemented_generator_fails_closed(tmp_path: Path):
    with pytest.raises(NotImplementedError):
        UnimplementedCandidateGenerator().generate(
            "INVSQRT", "hypothesis", tmp_path, {}
        )


def test_orchestrator_keeps_execution_disabled():
    class Runner:
        def evaluate(self, **kwargs):
            raise AssertionError("must not execute")

    orchestrator = Phase3Orchestrator(Runner())
    plan = orchestrator.plan("INVSQRT")
    assert plan.execution_enabled is False
    with pytest.raises(PermissionError):
        orchestrator.execute_candidate()
