from __future__ import annotations

from pathlib import Path

from benchmarks.registry import get_baseline, list_benchmarks
from engine.candidate_validator import CandidateStatus, validate_candidate
from evaluation.pareto_archive import ParetoArchive
from evaluation.verifier_adapter import (
    VerificationRequest,
    VerificationStatus,
    VerifierAdapter,
)
from agents.strategy.strategy_engine import ResearchOpportunity, StrategyEngine
from storage.experiment_tracker import Experiment, ExperimentTracker


def test_registry_contains_prompt_supplied_baselines():
    assert {item.benchmark_id for item in list_benchmarks()} == {
        "INVSQRT", "DQI", "DFTHCSOS"
    }
    assert get_baseline("INVSQRT").official_score == 2076774.233715282
    assert get_baseline("DQI").verification_parameters["max_workers"] == 1


def test_candidate_validator_rejects_missing_artifact(tmp_path):
    result = validate_candidate("INVSQRT", "candidate-1", tmp_path / "missing.kmb")
    assert result.status is CandidateStatus.ERROR
    assert result.artifact_sha256 is None


def test_candidate_validator_checks_dfthcsos_structure(tmp_path):
    for index in range(10):
        (tmp_path / f"angle_k_{index}.kmb").write_bytes(b"candidate")
    (tmp_path / "submission.npz").write_bytes(b"candidate")
    result = validate_candidate("DFTHCSOS", "candidate-1", tmp_path)
    assert result.status is CandidateStatus.STRUCTURALLY_VALID
    assert result.artifact_sha256


def test_tracker_round_trip_and_duplicate_protection(tmp_path):
    tracker = ExperimentTracker(tmp_path / "qoptimus.db")
    experiment = Experiment(
        benchmark_id="INVSQRT",
        candidate_id="candidate-1",
        circuit_artifact_path="candidate.kmb",
        strategy="test",
        parameters={"alpha": 1},
    )
    tracker.create(experiment)
    assert tracker.get(experiment.experiment_id).parameters == {"alpha": 1}
    try:
        tracker.create(experiment)
    except Exception as exc:
        assert "UNIQUE" in str(exc)
    else:
        raise AssertionError("duplicate candidates must not overwrite history")


def test_verifier_requires_official_status_and_parses_json(tmp_path, monkeypatch):
    verifier = tmp_path / "verifier.py"
    verifier.write_text(
        "import json\nprint(json.dumps({'status': 'PASS', 'score': 3.5}))\n",
        encoding="utf-8",
    )
    candidate = tmp_path / "candidate.kmb"
    candidate.write_bytes(b"candidate")
    monkeypatch.setattr(
        "evaluation.verifier_adapter.get_benchmark",
        lambda _: type(
            "Benchmark",
            (),
            {"benchmark_id": "INVSQRT", "verifier_path": "verifier.py"},
        )(),
    )
    result = VerifierAdapter().run(
        VerificationRequest(
            benchmark_id="INVSQRT",
            candidate_id="candidate-1",
            candidate_artifact=candidate,
            verifier_root=tmp_path,
            official=True,
            authorized=True,
        )
    )
    assert result.status is VerificationStatus.PASS
    assert result.official_score == 3.5


def test_strategy_ranking_is_explainable():
    opportunity = ResearchOpportunity(
        benchmark_id="INVSQRT",
        hypothesis="reduce arithmetic ancillas",
        expected_cost_reduction=0.1,
        research_hours=2,
        implementation_probability=0.8,
        verification_probability=0.9,
        verifier_hours=1,
        memory_risk=0.1,
        uncertainty=0.2,
        evidence="source-derived baseline resource profile",
        success_criteria="official PASS with lower cost",
        abandonment_criteria="no structural improvement after two trials",
    )
    decision = StrategyEngine().evaluate(opportunity)
    assert decision.expected_log_score_per_hour > 0
    assert "Evidence:" in decision.rationale


def test_archive_rejects_unverified_results():
    archive = ParetoArchive()
    result = type(
        "Result",
        (),
        {
            "status": VerificationStatus.FAIL,
            "official_run": True,
            "official_score": 1.0,
            "candidate_id": "candidate-1",
            "benchmark_id": "INVSQRT",
        },
    )()
    assert archive.promote(result, {"cost": 1}) is False
