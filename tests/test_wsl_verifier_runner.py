from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest

from evaluation import wsl_verifier_runner as runner


def _sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    official = tmp_path / "official"
    application = tmp_path / "application"
    verifier = (
        official
        / "verifiers"
        / "invsqrt"
        / "verifiers"
        / "invsqrt"
        / "verify_submission_invsqrt.py"
    )
    verifier.parent.mkdir(parents=True)
    verifier.write_text("print('{}')\n", encoding="utf-8")
    candidate = application / "candidate.kmb"
    candidate.parent.mkdir()
    candidate.write_bytes(b"candidate")
    monkeypatch.setattr(runner, "OFFICIAL_ROOT", official)
    monkeypatch.setattr(runner, "APPLICATION_ROOT", application)
    return official, candidate


def test_request_requires_authorization_and_approved_path(
    tmp_path, monkeypatch
):
    _, candidate = _sandbox(tmp_path, monkeypatch)
    request = {
        "benchmark_id": "INVSQRT",
        "candidate_artifact": str(candidate),
        "authorized": False,
    }
    with pytest.raises(runner.RunnerInputError, match="authorization"):
        runner._validate_request(request)

    request["authorized"] = True
    assert runner._validate_request(request)[0] == "INVSQRT"

    request["candidate_artifact"] = str(tmp_path / "outside.kmb")
    with pytest.raises(runner.RunnerInputError, match="approved roots"):
        runner._validate_request(request)


def test_runner_json_parser_selects_last_complete_object():
    assert runner._parse_json('log {"status":"FAIL"} {"status":"PASS"}') == {
        "status": "PASS"
    }


def test_dqi_command_forces_single_worker(tmp_path, monkeypatch):
    official, candidate = _sandbox(tmp_path, monkeypatch)
    dqi_verifier = official / "verifiers" / "dqi" / "verify_submission_dqi.py"
    dqi_verifier.parent.mkdir(parents=True)
    dqi_verifier.write_text("print('{}')\n", encoding="utf-8")
    command = runner._command("DQI", candidate, ())
    assert "--max-workers" in command
    assert command[command.index("--max-workers") + 1] == "1"


def test_dqi_rejects_worker_override(tmp_path, monkeypatch):
    official, candidate = _sandbox(tmp_path, monkeypatch)
    verifier = official / "verifiers" / "dqi" / "verify_submission_dqi.py"
    verifier.parent.mkdir(parents=True)
    verifier.write_text("print('{}')\n", encoding="utf-8")
    with pytest.raises(runner.RunnerInputError, match="worker count"):
        runner._validate_request(
            {
                "benchmark_id": "DQI",
                "candidate_artifact": str(candidate),
                "authorized": True,
                "extra_args": ["--max-workers", "4"],
            }
        )


def test_execute_command_timeout_is_explicit(monkeypatch):
    result = runner.execute_command(
        (sys.executable, "-c", "import time; time.sleep(2)"),
        timeout_seconds=0.01,
    )
    assert result["timed_out"] is True
    assert result["exit_code"] is not None
    assert isinstance(result["peak_memory_available"], bool)


def test_adapter_wsl_request_preserves_authorization_gate(tmp_path):
    from evaluation.verifier_adapter import VerificationRequest, VerifierAdapter

    request = VerificationRequest(
        "INVSQRT",
        "candidate",
        tmp_path / "candidate.kmb",
        tmp_path,
        authorized=False,
        use_wsl_runner=True,
    )
    result = VerifierAdapter().run(request)
    assert result.status.value == "NOT_RUN"
