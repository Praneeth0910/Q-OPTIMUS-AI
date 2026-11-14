from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import time
from unittest.mock import Mock

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
        for extra_args in (["--max-workers", "4"], ["--max-workers=4"]):
            runner._validate_request(
                {
                    "benchmark_id": "DQI",
                    "candidate_artifact": str(candidate),
                    "authorized": True,
                    "extra_args": extra_args,
                }
            )


def test_execute_command_timeout_is_explicit(monkeypatch):
    result = runner.execute_command(
        (sys.executable, "-c", "import time; time.sleep(2)"),
        timeout_seconds=0.01,
        pid_file=None,
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


def test_runtime_and_wsl_interpreter_resolution(tmp_path, monkeypatch):
    from evaluation.verifier_adapter import VerifierAdapter

    adapter = VerifierAdapter()
    monkeypatch.setenv("Q_OPTIMUS_RUNTIME_DIR", str(tmp_path / "runtime"))
    monkeypatch.setenv(
        "Q_OPTIMUS_WSL_PYTHON",
        "/opt/q-optimus/.venv-verifier/bin/python",
    )
    first = adapter._pid_file()
    second = adapter._pid_file()
    assert first.parent == (tmp_path / "runtime").resolve()
    assert first != second
    assert adapter._wsl_python() == "/opt/q-optimus/.venv-verifier/bin/python"


@pytest.mark.parametrize(
    "value",
    ["python", "/usr/bin/python", "/opt/python"],
)
def test_invalid_wsl_interpreter_is_rejected(monkeypatch, value):
    from evaluation.verifier_adapter import VerifierAdapter

    monkeypatch.setenv("Q_OPTIMUS_WSL_PYTHON", value)
    with pytest.raises(RuntimeError, match="approved WSL interpreter"):
        VerifierAdapter()._wsl_python()


def test_missing_interpreter_config_uses_approved_fallback(monkeypatch):
    import evaluation.verifier_adapter as adapter_module

    monkeypatch.delenv("Q_OPTIMUS_WSL_PYTHON", raising=False)
    monkeypatch.setattr(
        adapter_module, "VERIFIER_ENVIRONMENT_FILE", Path("missing-environment.txt")
    )
    assert adapter_module.VerifierAdapter()._wsl_python().endswith(
        "/.venv-kickmix/bin/python"
    )


@pytest.mark.skipif(sys.platform == "win32", reason="fcntl is Linux-only")
def test_lock_timeout_and_release_after_exception(tmp_path, monkeypatch):
    lock_path = tmp_path / "dqi.lock"
    monkeypatch.setattr(runner, "DQI_LOCK_PATH", lock_path)
    first = runner._lock_file(0.1)
    try:
        with pytest.raises(TimeoutError):
            runner._lock_file(0.01)
    finally:
        import fcntl

        fcntl.flock(first.fileno(), fcntl.LOCK_UN)
        first.close()
    second = runner._lock_file(0.1)
    second.close()


@pytest.mark.skipif(sys.platform == "win32", reason="fcntl is Linux-only")
def test_lock_releases_when_command_spawn_fails(tmp_path, monkeypatch):
    lock_path = tmp_path / "dqi.lock"
    monkeypatch.setattr(runner, "DQI_LOCK_PATH", lock_path)
    original = runner.subprocess.Popen

    def fail_spawn(*args, **kwargs):
        raise OSError("spawn failed")

    monkeypatch.setattr(runner.subprocess, "Popen", fail_spawn)
    with pytest.raises(OSError):
        runner.execute_command(("unused",), timeout_seconds=1, lock_dqi=True, pid_file=None)
    monkeypatch.setattr(runner.subprocess, "Popen", original)
    handle = runner._lock_file(0.1)
    handle.close()


def test_status_mapping_preserves_all_outcomes():
    from evaluation.verifier_adapter import (
        VerificationStatus,
        _status_from_runner,
    )

    for name in ("PASS", "FAIL", "TIMEOUT", "ERROR", "NOT_RUN"):
        expected = VerificationStatus[name]
        assert _status_from_runner({"status": name}, 0) is expected
    assert _status_from_runner({"status": "NOT_RUN"}, 2) is VerificationStatus.NOT_RUN


def test_runner_main_exit_codes_for_input_and_verifier_outcomes(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["wsl_verifier_runner"])
    monkeypatch.setattr(sys, "stdin", type("Input", (), {"read": lambda self: "{}"})())
    assert runner.main() == 2
    assert json.loads(capsys.readouterr().out)["status"] == "NOT_RUN"


def test_runner_main_malformed_json_is_structured_error(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["wsl_verifier_runner"])
    monkeypatch.setattr(sys, "stdin", type("Input", (), {"read": lambda self: "{"})())
    assert runner.main() == 1
    response = json.loads(capsys.readouterr().out)
    assert response["status"] == "ERROR"


def test_runner_main_unexpected_error_is_structured(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["wsl_verifier_runner"])
    monkeypatch.setattr(runner, "run_request", Mock(side_effect=RuntimeError("boom")))
    monkeypatch.setattr(
        sys, "stdin", type("Input", (), {"read": lambda self: "{}"})()
    )
    assert runner.main() == 1
    response = json.loads(capsys.readouterr().out)
    assert response["status"] == "ERROR"
    assert "RuntimeError" in response["error"]


def test_adapter_timeout_requests_linux_group_cleanup(tmp_path, monkeypatch):
    from evaluation.verifier_adapter import VerificationRequest, VerifierAdapter

    candidate = tmp_path / "candidate.kmb"
    candidate.write_bytes(b"candidate")
    pid_file = tmp_path / "pid.json"
    pid_file.write_text(
        json.dumps(
            {
                "runner_pid": 101,
                "verifier_pid": 202,
                "verifier_pgid": 202,
                "runner_starttime": 1,
                "verifier_starttime": 2,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("Q_OPTIMUS_WSL_PID_FILE", str(pid_file))
    monkeypatch.setattr(VerifierAdapter, "_pid_file", lambda self: pid_file)
    fake_process = Mock()
    fake_process.returncode = -9
    fake_process.communicate.side_effect = [
        runner.subprocess.TimeoutExpired("wsl.exe", 1),
        ("", ""),
    ]
    def fake_popen(*args, **kwargs):
        pid_file.write_text(
            json.dumps(
                {
                    "runner_pid": 101,
                    "verifier_pid": 202,
                    "verifier_pgid": 202,
                    "runner_starttime": 1,
                    "verifier_starttime": 2,
                }
            ),
            encoding="utf-8",
        )
        return fake_process

    monkeypatch.setattr(runner.subprocess, "Popen", fake_popen)
    kill_calls = []

    def fake_run(command, **kwargs):
        kill_calls.append(command)
        return Mock(returncode=0 if len(kill_calls) == 1 else 1)

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    result = VerifierAdapter().run(
        VerificationRequest(
            "INVSQRT",
            "candidate",
            candidate,
            tmp_path,
            timeout_seconds=1,
            authorized=True,
            use_wsl_runner=True,
        )
    )
    assert result.status.value == "TIMEOUT"
    assert kill_calls
    assert any("python" in part for call in kill_calls for part in call)
    fake_process.kill.assert_called_once()
    assert not pid_file.exists()


def test_pid_file_writer_is_atomic_and_valid(tmp_path, monkeypatch):
    class Process:
        pid = 22

    monkeypatch.setattr(runner.os, "getpid", lambda: 11)
    monkeypatch.setattr(runner.os, "getpgid", lambda _pid: 22, raising=False)
    monkeypatch.setattr(runner, "_proc_starttime", lambda pid: pid + 100)
    path = tmp_path / "nested" / "wsl-verifier.pid"
    runner._write_pid_file(path, Process())
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "runner_pid": 11,
        "verifier_pid": 22,
        "verifier_pgid": 22,
        "runner_starttime": 111,
        "verifier_starttime": 122,
    }
    assert not list(path.parent.glob("*.tmp"))


@pytest.mark.parametrize(
    "record",
    [
        "not-json",
        {},
        {"runner_pid": 11, "verifier_pid": 22},
        {"runner_pid": 11, "verifier_pid": 11, "verifier_pgid": 22},
    ],
)
def test_cleanup_rejects_malformed_or_unsafe_pid_records(tmp_path, record):
    from evaluation.verifier_adapter import _terminate_wsl_process_tree

    path = tmp_path / "pid.json"
    path.write_text(
        record if isinstance(record, str) else json.dumps(record),
        encoding="utf-8",
    )
    assert _terminate_wsl_process_tree(path) is False
    assert path.exists()


def test_cleanup_failure_is_reported_and_record_retained(tmp_path, monkeypatch):
    from evaluation.verifier_adapter import _terminate_wsl_process_tree

    path = tmp_path / "pid.json"
    path.write_text(
        json.dumps(
            {
                "runner_pid": 11,
                "verifier_pid": 22,
                "verifier_pgid": 22,
                "runner_starttime": 1,
                "verifier_starttime": 2,
            }
        ),
        encoding="utf-8",
    )

    def fail_cleanup(*args, **kwargs):
        raise OSError("wsl unavailable")

    monkeypatch.setattr(runner.subprocess, "run", fail_cleanup)
    assert _terminate_wsl_process_tree(path) is False
    assert path.exists()


def test_stale_pid_identity_is_rejected_without_follow_up_kills(tmp_path, monkeypatch):
    from evaluation.verifier_adapter import _terminate_wsl_process_tree

    path = tmp_path / "stale.pid"
    path.write_text(
        json.dumps(
            {
                "runner_pid": 11,
                "verifier_pid": 22,
                "verifier_pgid": 22,
                "runner_starttime": 101,
                "verifier_starttime": 202,
            }
        ),
        encoding="utf-8",
    )
    calls = []

    def stale_identity(command, **kwargs):
        calls.append((command, kwargs))
        return Mock(returncode=3)

    monkeypatch.setattr(runner.subprocess, "run", stale_identity)
    assert _terminate_wsl_process_tree(path) is False
    assert len(calls) == 1
    assert path.exists()


@pytest.mark.skipif(
    sys.platform == "win32" and not Path(r"C:\Windows\System32\wsl.exe").exists(),
    reason="WSL is unavailable",
)
def test_real_windows_to_wsl_timeout_lifecycle(tmp_path, monkeypatch):
    from dataclasses import replace

    from evaluation.verifier_adapter import (
        VerificationRequest,
        VerificationStatus,
        VerifierAdapter,
        _to_wsl_path,
    )

    candidate = tmp_path / "candidate.kmb"
    candidate.write_bytes(b"harmless integration candidate")
    pid_file = tmp_path / "integration.pid"
    monkeypatch.setenv("Q_OPTIMUS_WSL_PID_FILE", str(pid_file))
    helper = Path(__file__).with_name("wsl_harmless_runner.py")

    class IntegrationAdapter(VerifierAdapter):
        def _pid_file(self) -> Path:
            return pid_file

        def _wsl_command(self, pid_path: Path) -> tuple[str, ...]:
            return (
                "wsl.exe",
                "-e",
                "env",
                "PYTHONPATH=/mnt/d/Projects/Q-OPTIMUS-AI",
                f"Q_OPTIMUS_WSL_PID_FILE={_to_wsl_path(pid_path)}",
                self._wsl_python(),
                _to_wsl_path(helper),
                "--request",
                "-",
            )

    request = VerificationRequest(
        "INVSQRT",
        "harmless-integration",
        candidate,
        tmp_path,
        timeout_seconds=5,
        authorized=True,
        use_wsl_runner=True,
    )
    results: list[tuple[object, float]] = []
    unrelated_process = subprocess.Popen(
        (
            "wsl.exe",
            "-e",
            "sh",
            "-c",
            "setsid sleep 120 >/dev/null 2>&1 & echo $!; wait",
        ),
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    assert unrelated_process.stdout is not None
    unrelated_pid = int(unrelated_process.stdout.readline().strip())
    try:
        for index in range(2):
            started = time.monotonic()
            result = IntegrationAdapter().run(
                replace(request, candidate_id=f"run-{index}")
            )
            duration = time.monotonic() - started
            results.append((result, duration))
            assert result.status is VerificationStatus.TIMEOUT
            assert result.status is not VerificationStatus.PASS
            assert result.verifier_exit_code == -9
            assert pid_file.exists() is False
            child_pid = int((tmp_path / "harmless-child.pid").read_text())
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                probe = subprocess.run(
                    ("wsl.exe", "-e", "kill", "-0", str(child_pid)),
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                if probe.returncode != 0:
                    break
                time.sleep(0.05)
            assert subprocess.run(
                ("wsl.exe", "-e", "kill", "-0", str(child_pid)),
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            ).returncode != 0
            assert subprocess.run(
                ("wsl.exe", "-e", "kill", "-0", str(unrelated_pid)),
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            ).returncode == 0
            assert duration >= 5
    finally:
        pid_file.unlink(missing_ok=True)
        subprocess.run(
            ("wsl.exe", "-e", "kill", "-KILL", str(unrelated_pid)),
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        unrelated_process.terminate()
        unrelated_process.communicate(timeout=5)
    assert len(results) == 2


@pytest.mark.skipif(sys.platform == "win32", reason="Linux process-group behavior")
def test_timeout_kills_child_process_group(tmp_path):
    pid_file = tmp_path / "pid.json"
    child_pid_file = tmp_path / "child.pid"
    code = (
        "import subprocess,sys,time; "
        f"p=subprocess.Popen([sys.executable,'-c','import time,os; "
        f"open(r\"{child_pid_file}\",\"w\").write(str(os.getpid())); time.sleep(30)']); "
        "time.sleep(30)"
    )
    result = runner.execute_command(
        (sys.executable, "-c", code),
        timeout_seconds=0.1,
        pid_file=pid_file,
    )
    assert result["timed_out"] is True
    child_pid = int(child_pid_file.read_text())
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        if not runner.psutil.pid_exists(child_pid):
            break
        time.sleep(0.05)
    assert not runner.psutil.pid_exists(child_pid)
