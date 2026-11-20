"""Controlled execution interface for official benchmark verifiers."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import threading
import time
from uuid import uuid4
from threading import Event, Thread
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Sequence

from benchmarks.registry import get_benchmark

try:
    import psutil
except ImportError:
    psutil = None


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ROOT = REPOSITORY_ROOT / ".runtime"
VERIFIER_ENVIRONMENT_FILE = REPOSITORY_ROOT / "config" / "verifier-environment.txt"
APPROVED_WSL_PYTHON_FALLBACK = (
    "/home/praneeth_0910/projects/q-optimus-quantum/.venv-kickmix/bin/python"
)


def _resolve_wsl_python() -> str:
    configured = os.environ.get("Q_OPTIMUS_WSL_PYTHON")
    if configured is None and VERIFIER_ENVIRONMENT_FILE.is_file():
        for line in VERIFIER_ENVIRONMENT_FILE.read_text(
            encoding="utf-8"
        ).splitlines():
            if line.startswith("wsl_python="):
                configured = line.partition("=")[2].strip()
                break
    interpreter = configured or APPROVED_WSL_PYTHON_FALLBACK
    if (
        not interpreter.startswith("/")
        or "/.venv" not in interpreter
        or not interpreter.endswith("/bin/python")
    ):
        raise RuntimeError(
            "Q_OPTIMUS_WSL_PYTHON must be an absolute approved WSL interpreter path"
        )
    return interpreter


class VerificationStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    TIMEOUT = "TIMEOUT"
    ERROR = "ERROR"
    NOT_RUN = "NOT_RUN"


@dataclass(frozen=True)
class VerificationRequest:
    """A single candidate-verification request."""

    benchmark_id: str
    candidate_id: str
    candidate_artifact: Path
    verifier_root: Path
    timeout_seconds: float = 3600.0
    official: bool = True
    authorized: bool = False
    extra_args: tuple[str, ...] = ()
    use_wsl_runner: bool | None = None


@dataclass(frozen=True)
class VerificationResult:
    """Normalized result retaining the official raw output for audit."""

    benchmark_id: str
    candidate_id: str
    status: VerificationStatus
    official_score: float | None
    verified_shots: int | None
    resource_metrics: dict[str, Any]
    circuit_sha256: str | None
    verifier_exit_code: int | None
    verification_parameters: dict[str, Any]
    runtime_seconds: float | None
    peak_memory_kb: int | None
    stdout: str
    stderr: str
    error: str | None
    official_run: bool
    command: tuple[str, ...] = field(default_factory=tuple)


class VerifierAdapter:
    """Execute an official verifier without modifying its source or assets."""

    _dqi_lock = threading.Lock()

    def _pid_file(self) -> Path:
        runtime_root = Path(
            os.environ.get("Q_OPTIMUS_RUNTIME_DIR", str(RUNTIME_ROOT))
        ).resolve()
        runtime_root.mkdir(parents=True, exist_ok=True)
        return runtime_root / f"wsl-verifier-{uuid4().hex}.pid"

    def _wsl_python(self) -> str:
        return _resolve_wsl_python()

    def _should_use_wsl(self, request: VerificationRequest) -> bool:
        """Determine whether verification should route through WSL runner."""
        if request.use_wsl_runner is not None:
            return request.use_wsl_runner
        # Policy: An unspecified official verification on Windows uses WSL.
        if sys.platform == "win32" and request.official:
            return True
        return False

    def build_command(self, request: VerificationRequest) -> tuple[str, ...]:
        benchmark = get_benchmark(request.benchmark_id)
        verifier = request.verifier_root / benchmark.verifier_path
        if not verifier.is_file():
            raise FileNotFoundError(f"Verifier does not exist: {verifier}")
        if not request.candidate_artifact.exists():
            raise FileNotFoundError(
                f"Candidate artifact does not exist: {request.candidate_artifact}"
            )
        if benchmark.benchmark_id == "DFTHCSOS" and request.candidate_artifact.is_dir():
            candidate_args = [
                str(request.candidate_artifact / "submission.npz"),
                str(request.candidate_artifact),
            ]
        else:
            candidate_args = [str(request.candidate_artifact)]
        command = [sys.executable, str(verifier), *candidate_args]
        if request.benchmark_id.upper() == "DQI":
            command.extend(["--max-workers", "1"])
        command.extend(request.extra_args)
        return tuple(command)

    def run(self, request: VerificationRequest) -> VerificationResult:
        benchmark_id = request.benchmark_id.upper()
        if not request.authorized:
            return self._error_result(
                request,
                "Verification requires explicit authorization",
                VerificationStatus.NOT_RUN,
            )
        lock = self._dqi_lock if benchmark_id == "DQI" else _NullLock()
        with lock:
            if self._should_use_wsl(request):
                return self._run_wsl(request, benchmark_id)
            try:
                command = self.build_command(request)
            except (FileNotFoundError, IsADirectoryError) as exc:
                return self._error_result(request, str(exc), VerificationStatus.ERROR)

            digest = _digest(request.candidate_artifact)
            started = time.monotonic()
            try:
                process = subprocess.Popen(
                    command,
                    cwd=request.verifier_root,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                stop_sampling = Event()
                peak_memory = [None]
                sampler = Thread(
                    target=_sample_peak_memory,
                    args=(process.pid, stop_sampling, peak_memory),
                    daemon=True,
                )
                sampler.start()
                stdout, stderr = process.communicate(timeout=request.timeout_seconds)
                stop_sampling.set()
                sampler.join()
            except subprocess.TimeoutExpired:
                process.kill()
                stdout, stderr = process.communicate()
                return VerificationResult(
                    benchmark_id=benchmark_id,
                    candidate_id=request.candidate_id,
                    status=VerificationStatus.TIMEOUT,
                    official_score=None,
                    verified_shots=None,
                    resource_metrics={},
                    circuit_sha256=digest,
                    verifier_exit_code=process.returncode,
                    verification_parameters={},
                    runtime_seconds=time.monotonic() - started,
                    peak_memory_kb=peak_memory[0],
                    stdout=stdout,
                    stderr=stderr,
                    error=f"Verifier timed out after {request.timeout_seconds} seconds",
                    official_run=request.official,
                    command=command,
                )
            except OSError as exc:
                return self._error_result(
                    request, f"Unable to execute verifier: {exc}", VerificationStatus.ERROR,
                    command=command, digest=digest,
                )
            finally:
                if "stop_sampling" in locals():
                    stop_sampling.set()
                if "sampler" in locals():
                    sampler.join(timeout=1)

        payload = _parse_json_output(stdout)
        if payload is None:
            return VerificationResult(
                benchmark_id=benchmark_id,
                candidate_id=request.candidate_id,
                status=VerificationStatus.ERROR,
                official_score=None,
                verified_shots=None,
                resource_metrics={},
                circuit_sha256=digest,
                verifier_exit_code=process.returncode,
                verification_parameters={},
                runtime_seconds=time.monotonic() - started,
                peak_memory_kb=peak_memory[0],
                stdout=stdout,
                stderr=stderr,
                error="Verifier did not emit a JSON result",
                official_run=request.official,
                command=command,
            )

        reported_status = str(
            payload.get("status", payload.get("result", "FAIL"))
        ).upper()
        passed = (
            request.official
            and process.returncode == 0
            and reported_status == VerificationStatus.PASS
        )
        status = VerificationStatus.PASS if passed else VerificationStatus.FAIL
        if process.returncode != 0 and reported_status not in {"PASS", "FAIL"}:
            status = VerificationStatus.ERROR
        metrics = payload.get("resource_metrics", payload.get("metrics", {}))
        if not isinstance(metrics, dict):
            metrics = {}
        return VerificationResult(
            benchmark_id=benchmark_id,
            candidate_id=request.candidate_id,
            status=status,
            official_score=_number(payload.get("official_score", payload.get("score"))),
            verified_shots=_integer(payload.get("verified_shots")),
            resource_metrics=metrics,
            circuit_sha256=digest,
            verifier_exit_code=process.returncode,
            verification_parameters=dict(payload.get("verification_parameters", {})),
            runtime_seconds=_number(
                payload.get("runtime_seconds", time.monotonic() - started)
            ),
            peak_memory_kb=(
                _integer(payload.get("peak_memory_kb"))
                or peak_memory[0]
            ),
            stdout=stdout,
            stderr=stderr,
            error=None if status != VerificationStatus.ERROR else payload.get("error"),
            official_run=request.official,
            command=command,
        )

    @staticmethod
    def _error_result(
        request: VerificationRequest,
        error: str,
        status: VerificationStatus,
        *,
        command: Sequence[str] = (),
        digest: str | None = None,
    ) -> VerificationResult:
        return VerificationResult(
            benchmark_id=request.benchmark_id.upper(),
            candidate_id=request.candidate_id,
            status=status,
            official_score=None,
            verified_shots=None,
            resource_metrics={},
            circuit_sha256=digest,
            verifier_exit_code=None,
            verification_parameters={},
            runtime_seconds=None,
            peak_memory_kb=None,
            stdout="",
            stderr="",
            error=error,
            official_run=request.official,
            command=tuple(command),
        )

    def _run_wsl(
        self, request: VerificationRequest, benchmark_id: str
    ) -> VerificationResult:
        if not request.authorized:
            return self._error_result(
                request,
                "Verification requires explicit authorization",
                VerificationStatus.NOT_RUN,
            )
        try:
            pid_file = self._pid_file()
            command = self._wsl_command(pid_file)
        except (RuntimeError, OSError, ValueError) as exc:
            return self._error_result(
                request,
                f"WSL environment resolution failed: {exc}",
                VerificationStatus.ERROR,
            )

        payload = {
            "benchmark_id": benchmark_id,
            "candidate_artifact": _to_wsl_path(request.candidate_artifact),
            "timeout_seconds": request.timeout_seconds,
            "authorized": request.authorized,
            "extra_args": list(request.extra_args),
        }
        started = time.monotonic()
        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            stdout, stderr = process.communicate(
                json.dumps(payload), timeout=request.timeout_seconds + 5
            )
        except OSError as exc:
            return self._error_result(
                request,
                f"Unable to execute WSL runner: {exc}",
                VerificationStatus.ERROR,
                command=command,
            )
        except subprocess.TimeoutExpired:
            cleanup_confirmed = _terminate_wsl_process_tree(pid_file)
            process.kill()
            stdout, stderr = process.communicate()
            return VerificationResult(
                benchmark_id=benchmark_id,
                candidate_id=request.candidate_id,
                status=VerificationStatus.TIMEOUT,
                official_score=None,
                verified_shots=None,
                resource_metrics={},
                circuit_sha256=None,
                verifier_exit_code=process.returncode,
                verification_parameters={},
                runtime_seconds=time.monotonic() - started,
                peak_memory_kb=None,
                stdout=stdout,
                stderr=stderr,
                error=(
                    "WSL verifier runner timed out"
                    if cleanup_confirmed
                    else "WSL verifier runner timed out; Linux process cleanup was not confirmed"
                ),
                official_run=request.official,
                command=command,
            )
        response = _parse_json_output(stdout)
        if response is None:
            return self._error_result(
                request,
                "WSL verifier runner did not emit JSON",
                VerificationStatus.ERROR,
                command=command,
            )
        status = _status_from_runner(response, process.returncode)
        parsed = response.get("parsed_json")
        if not isinstance(parsed, dict):
            parsed = {}
        resource_metrics = parsed.get("resource_metrics", {})
        if not isinstance(resource_metrics, dict):
            resource_metrics = {}
        verification_parameters = parsed.get("verification_parameters", {})
        if not isinstance(verification_parameters, dict):
            verification_parameters = {}
        return VerificationResult(
            benchmark_id=benchmark_id,
            candidate_id=request.candidate_id,
            status=status,
            official_score=_number(parsed.get("official_score", parsed.get("score"))),
            verified_shots=_integer(parsed.get("verified_shots")),
            resource_metrics=resource_metrics,
            circuit_sha256=response.get("candidate_sha256"),
            verifier_exit_code=response.get("exit_code"),
            verification_parameters=verification_parameters,
            runtime_seconds=_number(response.get("runtime_seconds")),
            peak_memory_kb=_integer(response.get("peak_memory_kb")),
            stdout=response.get("stdout", ""),
            stderr=response.get("stderr", stderr),
            error=response.get("error"),
            official_run=request.official,
            command=command,
        )

    def _wsl_command(self, pid_file: Path) -> tuple[str, ...]:
        """Build the production WSL runner command."""
        runner_path = _wsl_runner_path()
        env_vars = [
            "PYTHONPATH=/home/praneeth_0910/projects/q-optimus-quantum/official/kickmix/kickmix",
            f"Q_OPTIMUS_WSL_PID_FILE={_to_wsl_path(pid_file)}",
        ]
        app_root_override = os.environ.get("Q_OPTIMUS_APP_ROOT")
        if app_root_override:
            env_vars.append(f"Q_OPTIMUS_APP_ROOT={_to_wsl_path(Path(app_root_override))}")
        return (
            "wsl.exe",
            "-e",
            "env",
            *env_vars,
            self._wsl_python(),
            _to_wsl_path(runner_path),
            "--request",
            "-",
        )


def _wsl_runner_path() -> Path:
    return (Path(__file__).resolve().parent / "wsl_verifier_runner.py").resolve()


def _digest(path: Path) -> str:
    if path.is_dir():
        digest = hashlib.sha256()
        for child in sorted(item for item in path.rglob("*") if item.is_file()):
            digest.update(child.relative_to(path).as_posix().encode())
            digest.update(child.read_bytes())
        return digest.hexdigest()
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _to_wsl_path(path: Path) -> str:
    resolved = path.resolve()
    drive = resolved.drive.rstrip(":").lower()
    if drive and len(drive) == 1 and drive.isalpha():
        posix = resolved.as_posix()
        return f"/mnt/{drive}{posix[len(resolved.drive):]}"
    return resolved.as_posix()


def _status_from_runner(response: dict[str, Any], runner_exit_code: int) -> VerificationStatus:
    reported = response.get("status")
    if response.get("timed_out") or reported == "TIMEOUT":
        return VerificationStatus.TIMEOUT
    if reported == "NOT_RUN":
        return VerificationStatus.NOT_RUN
    if reported == "ERROR" or runner_exit_code != 0:
        return VerificationStatus.ERROR
    if reported == "PASS":
        return VerificationStatus.PASS
    if reported == "FAIL":
        return VerificationStatus.FAIL
    return VerificationStatus.ERROR


def _terminate_wsl_process_tree(pid_file: Path) -> bool:
    """Request Linux process-group cleanup through the WSL boundary."""
    try:
        record = json.loads(pid_file.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return False
    if not isinstance(record, dict):
        return False
    verifier_pid = record.get("verifier_pid")
    verifier_pgid = record.get("verifier_pgid")
    runner_pid = record.get("runner_pid")
    verifier_starttime = record.get("verifier_starttime")
    runner_starttime = record.get("runner_starttime")
    if not all(
        isinstance(value, int) and value > 0
        for value in (
            runner_pid,
            verifier_pid,
            verifier_pgid,
            runner_starttime,
            verifier_starttime,
        )
    ):
        return False
    if runner_pid == verifier_pid:
        return False
    cleanup_script = """\
import json
import os
import signal
import sys

record = json.load(sys.stdin)

def starttime(pid):
    try:
        stat = open(f"/proc/{pid}/stat", encoding="ascii").read()
        return int(stat.rsplit(")", 1)[1].split()[19])
    except (OSError, ValueError, IndexError):
        return None

if (
    starttime(record["verifier_pid"]) != record["verifier_starttime"]
    or starttime(record["runner_pid"]) != record["runner_starttime"]
):
    sys.exit(3)
os.killpg(record["verifier_pgid"], signal.SIGKILL)
if starttime(record["runner_pid"]) == record["runner_starttime"]:
    os.kill(record["runner_pid"], signal.SIGKILL)
"""
    cleanup_command = (
        _resolve_wsl_python(),
        "-c",
        cleanup_script,
    )
    try:
        cleanup = subprocess.run(
            ("wsl.exe", "-e", *cleanup_command),
            input=json.dumps(record),
            text=True,
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        return False
    if cleanup.returncode != 0:
        return False
    probes: list[tuple[str, ...]] = []
    probes.append(("wsl.exe", "-e", "kill", "-0", "--", f"-{verifier_pgid}"))
    probes.append(("wsl.exe", "-e", "kill", "-0", str(runner_pid)))
    deadline = time.monotonic() + 1.0
    while probes and time.monotonic() < deadline:
        try:
            if all(
                subprocess.run(
                    probe,
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                ).returncode != 0
                for probe in probes
            ):
                pid_file.unlink(missing_ok=True)
                return True
        except OSError:
            return False
        time.sleep(0.05)
    try:
        confirmed = bool(probes) and all(
            subprocess.run(
                probe,
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            ).returncode != 0
            for probe in probes
        )
    except OSError:
        return False
    if confirmed:
        try:
            pid_file.unlink(missing_ok=True)
        except OSError:
            return False
    return confirmed


def _parse_json_output(stdout: str) -> dict[str, Any] | None:
    decoder = json.JSONDecoder()
    parsed: dict[str, Any] | None = None
    parsed_length = -1
    for index, character in enumerate(stdout):
        if character != "{":
            continue
        try:
            value, end = decoder.raw_decode(stdout[index:])
        except json.JSONDecodeError:
            continue
        absolute_end = index + end
        if isinstance(value, dict) and absolute_end > parsed_length:
            parsed = value
            parsed_length = absolute_end
    return parsed


def _sample_peak_memory(pid: int, stop: Event, peak: list[int | None]) -> None:
    if psutil is None:
        return
    try:
        process = psutil.Process(pid)
    except psutil.Error:
        return
    while not stop.is_set():
        try:
            processes = [process, *process.children(recursive=True)]
            rss = sum(item.memory_info().rss for item in processes)
            peak[0] = max(peak[0] or 0, rss // 1024)
        except psutil.Error:
            pass
        stop.wait(0.05)


class _NullLock:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def _number(value: Any) -> float | None:
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _integer(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None
