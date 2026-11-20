"""Tests for Phase B Task B-1: D1.5 WSL Hardening.

Covers:
- Dynamic derivation of runner path (removing hardcoded Windows path).
- Path conversion with spaces and arbitrary drive letters.
- Dynamic WSL application root resolution and fail-closed validation of overrides.
- Official verification routing to WSL by default on Windows.
- Explicit local execution option for unit tests.
- Graceful error reporting when WSL resolution or invocation fails (no silent local fallback).
- Propagation of verifier selection through ExperimentRunner.
"""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
from unittest.mock import Mock, patch

import pytest

from evaluation.verifier_adapter import (
    VerificationRequest,
    VerificationResult,
    VerificationStatus,
    VerifierAdapter,
    _to_wsl_path,
    _wsl_runner_path,
)
from evaluation import wsl_verifier_runner as runner
from engine.experiment_runner import ExperimentRunner
from storage.experiment_tracker import ExperimentTracker


def test_runner_path_derived_dynamically():
    """Requirement 1: Runner path is derived from the actual location, not hardcoded."""
    derived = _wsl_runner_path()
    assert derived.is_file(), f"Derived runner path does not exist: {derived}"
    assert derived.name == "wsl_verifier_runner.py"
    assert derived.parent.name == "evaluation"

    # Command construction uses dynamic path rather than hardcoded /mnt/d/...
    adapter = VerifierAdapter()
    pid_file = Path("D:/test/pid.json")
    cmd = adapter._wsl_command(pid_file)
    expected_runner_wsl = _to_wsl_path(derived)
    assert expected_runner_wsl in cmd


def test_wsl_path_conversion_handles_spaces_and_various_drives():
    """Requirement 2: Different drive letters, relocation, and spaces in paths are handled."""
    # Drive D with spaces
    p_d_spaces = Path("D:/Projects/Q OPTIMUS AI/evaluation/wsl_verifier_runner.py")
    assert _to_wsl_path(p_d_spaces) == "/mnt/d/Projects/Q OPTIMUS AI/evaluation/wsl_verifier_runner.py"

    # Drive C
    p_c = Path("C:/Users/prane/workspace/candidate.kmb")
    assert _to_wsl_path(p_c) == "/mnt/c/Users/prane/workspace/candidate.kmb"

    # Drive E with spaces
    p_e = Path("E:/Quantum Comp/submission test/circuit.kmb")
    assert _to_wsl_path(p_e) == "/mnt/e/Quantum Comp/submission test/circuit.kmb"

    # Linux-native / POSIX path (drive letter empty)
    p_posix = Path("/home/user/projects/test.pid")
    assert _to_wsl_path(p_posix).endswith("/home/user/projects/test.pid")


def test_wsl_application_root_derived_from_location(tmp_path, monkeypatch):
    """Requirement 3: WSL application root derived from runner's location."""
    monkeypatch.delenv("Q_OPTIMUS_APP_ROOT", raising=False)
    derived = runner._resolve_application_root()
    assert derived.is_dir()
    assert (derived / "evaluation").is_dir()
    assert (derived / "benchmarks").is_dir()


def test_wsl_application_root_override_valid(tmp_path, monkeypatch):
    """Requirement 3: Valid Q_OPTIMUS_APP_ROOT override is accepted when valid."""
    app_dir = tmp_path / "custom_app"
    (app_dir / "benchmarks").mkdir(parents=True)
    (app_dir / "evaluation").mkdir(parents=True)

    monkeypatch.setenv("Q_OPTIMUS_APP_ROOT", str(app_dir))
    resolved = runner._resolve_application_root()
    assert resolved == app_dir.resolve()


def test_wsl_application_root_invalid_overrides_fail_closed(tmp_path, monkeypatch):
    """Requirement 3: Invalid Q_OPTIMUS_APP_ROOT overrides fail closed."""
    # Non-existent directory
    monkeypatch.setenv("Q_OPTIMUS_APP_ROOT", str(tmp_path / "nonexistent_dir"))
    with pytest.raises(runner.RunnerInputError, match="not an existing directory"):
        runner._resolve_application_root()

    # Directory missing expected project structure
    empty_dir = tmp_path / "empty_dir"
    empty_dir.mkdir()
    monkeypatch.setenv("Q_OPTIMUS_APP_ROOT", str(empty_dir))
    with pytest.raises(runner.RunnerInputError, match="missing expected project structure"):
        runner._resolve_application_root()


def test_wsl_command_propagates_q_optimus_app_root(tmp_path, monkeypatch):
    """Requirement 3: Q_OPTIMUS_APP_ROOT is explicitly propagated to WSL in _wsl_command."""
    app_dir = Path("D:/Custom/App Root")
    monkeypatch.setenv("Q_OPTIMUS_APP_ROOT", str(app_dir))
    adapter = VerifierAdapter()
    cmd = adapter._wsl_command(tmp_path / "test.pid")
    expected_env = f"Q_OPTIMUS_APP_ROOT={_to_wsl_path(app_dir)}"
    assert expected_env in cmd


def test_official_verification_routes_through_wsl_by_default_on_windows(tmp_path, monkeypatch):
    """Requirement 4: Unspecified official verification on Windows routes to WSL."""
    candidate = tmp_path / "candidate.kmb"
    candidate.write_bytes(b"candidate-bytes")

    request = VerificationRequest(
        benchmark_id="INVSQRT",
        candidate_id="cand-1",
        candidate_artifact=candidate,
        verifier_root=tmp_path,
        official=True,
        authorized=True,
        use_wsl_runner=None,  # Unspecified
    )

    adapter = VerifierAdapter()
    monkeypatch.setattr(sys, "platform", "win32")
    assert adapter._should_use_wsl(request) is True

    # Mock _run_wsl to verify dispatch
    wsl_called = False

    def mock_run_wsl(req, b_id):
        nonlocal wsl_called
        wsl_called = True
        return VerificationResult(
            benchmark_id=b_id,
            candidate_id=req.candidate_id,
            status=VerificationStatus.PASS,
            official_score=100.0,
            verified_shots=10,
            resource_metrics={},
            circuit_sha256="abc",
            verifier_exit_code=0,
            verification_parameters={},
            runtime_seconds=1.0,
            peak_memory_kb=1000,
            stdout="",
            stderr="",
            error=None,
            official_run=True,
        )

    monkeypatch.setattr(adapter, "_run_wsl", mock_run_wsl)
    result = adapter.run(request)
    assert wsl_called is True
    assert result.status is VerificationStatus.PASS


def test_explicit_local_test_mode_behaves_as_documented(tmp_path, monkeypatch):
    """Requirement 4: Explicit use_wsl_runner=False bypasses WSL on Windows."""
    candidate = tmp_path / "candidate.kmb"
    candidate.write_bytes(b"candidate-bytes")

    request = VerificationRequest(
        benchmark_id="INVSQRT",
        candidate_id="cand-local",
        candidate_artifact=candidate,
        verifier_root=tmp_path,
        official=True,
        authorized=True,
        use_wsl_runner=False,  # Explicitly local
    )

    adapter = VerifierAdapter()
    monkeypatch.setattr(sys, "platform", "win32")
    assert adapter._should_use_wsl(request) is False

    # Calling run with use_wsl_runner=False must NOT call _run_wsl
    def fail_if_wsl_called(*args, **kwargs):
        raise AssertionError("_run_wsl must not be called when use_wsl_runner=False")

    monkeypatch.setattr(adapter, "_run_wsl", fail_if_wsl_called)

    # Mock build_command and subprocess to verify local execution path
    mock_proc = Mock()
    mock_proc.communicate.return_value = ('{"status": "PASS", "score": 42.0}', "")
    mock_proc.returncode = 0
    mock_proc.pid = 99999
    monkeypatch.setattr(adapter, "build_command", lambda req: ("python", "verifier.py"))
    monkeypatch.setattr("evaluation.verifier_adapter.subprocess.Popen", lambda *a, **k: mock_proc)

    result = adapter.run(request)
    assert result.status is VerificationStatus.PASS
    assert result.official_score == 42.0


def test_failure_to_resolve_wsl_environment_is_reported_clearly(tmp_path, monkeypatch):
    """Requirement 4 & 6: Missing/invalid WSL environment reports ERROR and never falls back."""
    candidate = tmp_path / "candidate.kmb"
    candidate.write_bytes(b"candidate-bytes")

    request = VerificationRequest(
        benchmark_id="INVSQRT",
        candidate_id="cand-err",
        candidate_artifact=candidate,
        verifier_root=tmp_path,
        official=True,
        authorized=True,
        use_wsl_runner=True,
    )

    adapter = VerifierAdapter()

    # Case A: Interpreter resolution fails
    monkeypatch.setattr(
        adapter,
        "_wsl_python",
        Mock(side_effect=RuntimeError("Q_OPTIMUS_WSL_PYTHON must be an absolute approved WSL interpreter path")),
    )
    result = adapter.run(request)
    assert result.status is VerificationStatus.ERROR
    assert "WSL environment resolution failed" in (result.error or "")

    # Case B: wsl.exe process invocation fails (e.g. WSL not installed)
    monkeypatch.undo()
    monkeypatch.setattr(
        "evaluation.verifier_adapter.subprocess.Popen",
        Mock(side_effect=FileNotFoundError("wsl.exe not found")),
    )
    result_proc = adapter.run(request)
    assert result_proc.status is VerificationStatus.ERROR
    assert "Unable to execute WSL runner" in (result_proc.error or "")


def test_experiment_runner_propagates_use_wsl_runner(tmp_path, monkeypatch):
    """Requirement 4: ExperimentRunner correctly propagates use_wsl_runner to VerificationRequest."""
    tracker = ExperimentTracker(tmp_path / "test_experiments.sqlite")
    candidate = tmp_path / "cand.kmb"
    candidate.write_bytes(b"data")

    # Mock candidate validator
    from engine.candidate_validator import CandidateStatus, CandidateValidation
    monkeypatch.setattr(
        "engine.experiment_runner.validate_candidate",
        lambda *args: CandidateValidation(
            benchmark_id="INVSQRT",
            candidate_id="c1",
            status=CandidateStatus.STRUCTURALLY_VALID,
            artifact_sha256="abc",
            errors=(),
        ),
    )

    captured_requests = []
    mock_verifier = Mock()
    mock_verifier.run.side_effect = lambda req: captured_requests.append(req) or VerificationResult(
        benchmark_id=req.benchmark_id,
        candidate_id=req.candidate_id,
        status=VerificationStatus.PASS,
        official_score=10.0,
        verified_shots=10,
        resource_metrics={},
        circuit_sha256="abc",
        verifier_exit_code=0,
        verification_parameters={},
        runtime_seconds=1.0,
        peak_memory_kb=100,
        stdout="",
        stderr="",
        error=None,
        official_run=True,
    )

    runner_instance = ExperimentRunner(tracker=tracker, verifier=mock_verifier)

    # 1. Unspecified use_wsl_runner defaults to None
    runner_instance.evaluate(
        benchmark_id="INVSQRT",
        candidate_id="c1",
        artifact=candidate,
        verifier_root=tmp_path,
        strategy="test",
        authorized=True,
    )
    assert len(captured_requests) == 1
    assert captured_requests[0].use_wsl_runner is None

    # 2. Explicit use_wsl_runner=False is propagated
    runner_instance.evaluate(
        benchmark_id="INVSQRT",
        candidate_id="c2",
        artifact=candidate,
        verifier_root=tmp_path,
        strategy="test",
        authorized=True,
        use_wsl_runner=False,
    )
    assert len(captured_requests) == 2
    assert captured_requests[1].use_wsl_runner is False

    # 3. Explicit use_wsl_runner=True is propagated
    runner_instance.evaluate(
        benchmark_id="INVSQRT",
        candidate_id="c3",
        artifact=candidate,
        verifier_root=tmp_path,
        strategy="test",
        authorized=True,
        use_wsl_runner=True,
    )
    assert len(captured_requests) == 3
    assert captured_requests[2].use_wsl_runner is True
