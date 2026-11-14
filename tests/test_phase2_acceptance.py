from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from benchmarks.registry import get_baseline
from evaluation import verifier_adapter as adapter_module
from evaluation.pareto_archive import ParetoArchive
from evaluation.scoring import geometric_mean_score
from evaluation.verifier_adapter import (
    VerificationRequest,
    VerificationStatus,
    VerifierAdapter,
    _parse_json_output,
)
from storage.experiment_tracker import Experiment, ExperimentTracker


def _request(root: Path, benchmark_id: str, *, authorized: bool = True):
    benchmark = adapter_module.get_benchmark(benchmark_id)
    verifier = root / benchmark.verifier_path
    verifier.parent.mkdir(parents=True, exist_ok=True)
    verifier.write_text("print('{}')\n", encoding="utf-8")
    if benchmark_id == "DFTHCSOS":
        artifact = root / "candidate"
        artifact.mkdir()
        (artifact / "submission.npz").write_bytes(b"npz")
    else:
        artifact = root / "candidate.kmb"
        artifact.write_bytes(b"kmb")
    return VerificationRequest(
        benchmark_id=benchmark_id,
        candidate_id=f"{benchmark_id}-candidate",
        candidate_artifact=artifact,
        verifier_root=root,
        authorized=authorized,
    )


def test_json_parser_handles_pretty_printed_and_multiple_objects():
    output = 'log\n{"status": "FAIL", "error": "old"}\n' + (
        '{\n  "status": "PASS",\n  "score": 2.5\n}\n'
    )
    assert _parse_json_output(output) == {"status": "PASS", "score": 2.5}


def test_command_construction_covers_all_benchmarks(tmp_path):
    verifier = VerifierAdapter()
    invsqrt = verifier.build_command(_request(tmp_path, "INVSQRT"))
    dqi = verifier.build_command(_request(tmp_path, "DQI"))
    dfthcsos = verifier.build_command(_request(tmp_path, "DFTHCSOS"))
    assert invsqrt[-1].endswith("candidate.kmb")
    assert "--max-workers" in dqi and dqi[dqi.index("--max-workers") + 1] == "1"
    assert str(tmp_path / "candidate" / "submission.npz") in dfthcsos
    assert str(tmp_path / "candidate") in dfthcsos


def test_authorization_denial_does_not_spawn(tmp_path, monkeypatch):
    called = False

    def fail_spawn(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("subprocess must not start")

    monkeypatch.setattr(adapter_module.subprocess, "Popen", fail_spawn)
    result = VerifierAdapter().run(_request(tmp_path, "INVSQRT", authorized=False))
    assert result.status is VerificationStatus.NOT_RUN
    assert called is False


def test_dqi_lock_serializes_mocked_processes(tmp_path, monkeypatch):
    active = 0
    maximum = 0
    state_lock = threading.Lock()

    class FakeProcess:
        pid = 1
        returncode = 0

        def communicate(self, timeout=None):
            nonlocal active, maximum
            with state_lock:
                active += 1
                maximum = max(maximum, active)
            time.sleep(0.05)
            with state_lock:
                active -= 1
            return '{"status": "FAIL"}', ""

        def kill(self):
            pass

    monkeypatch.setattr(adapter_module.subprocess, "Popen", lambda *a, **k: FakeProcess())
    request = _request(tmp_path, "DQI")
    threads = [threading.Thread(target=VerifierAdapter().run, args=(request,)) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert maximum == 1


def test_timeout_is_not_pass(tmp_path):
    verifier = (
        tmp_path
        / "official"
        / "verifiers"
        / "invsqrt"
        / "verifiers"
        / "invsqrt"
        / "verify_submission_invsqrt.py"
    )
    verifier.parent.mkdir(parents=True)
    verifier.write_text("import time; time.sleep(1)\n", encoding="utf-8")
    candidate = tmp_path / "candidate.kmb"
    candidate.write_bytes(b"kmb")
    request = VerificationRequest(
        "INVSQRT", "timeout", candidate, tmp_path,
        timeout_seconds=0.01, authorized=True,
    )
    result = VerifierAdapter().run(request)
    assert result.status is VerificationStatus.TIMEOUT


def test_tracker_rolls_back_and_recovers(tmp_path):
    tracker = ExperimentTracker(tmp_path / "experiments.sqlite")
    first = Experiment("INVSQRT", "first", "first.kmb", "test")
    tracker.create(first)
    with pytest.raises(Exception):
        tracker.create(first)
    second = Experiment("INVSQRT", "second", "second.kmb", "test")
    tracker.create(second)
    assert {item.candidate_id for item in tracker.list()} == {"first", "second"}


def test_baseline_mapping_is_immutable_and_separate_from_candidates():
    baseline = get_baseline("INVSQRT")
    with pytest.raises(TypeError):
        baseline.resource_metrics["qubits"] = 1
    assert baseline.resource_metrics["qubits"] == 260


def test_geometric_mean_matches_hand_calculation():
    assert geometric_mean_score(
        {"A": 90, "B": 80}, {"A": 100, "B": 100}
    ) == pytest.approx((100 / 90 * 100 / 80) ** 0.5)


def test_archive_rejects_unverified_and_non_improving_candidates():
    archive = ParetoArchive()
    result = type(
        "Result",
        (),
        {
            "status": VerificationStatus.FAIL,
            "official_run": True,
            "official_score": 1.0,
            "candidate_id": "candidate",
            "benchmark_id": "INVSQRT",
        },
    )()
    assert archive.promote(result, {"cost": 1}) is False
    result.status = VerificationStatus.PASS
    result.official_score = get_baseline("INVSQRT").official_score
    assert archive.promote(result, {"cost": 1}) is False
