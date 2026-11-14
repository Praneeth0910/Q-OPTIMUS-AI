"""Controlled execution interface for official benchmark verifiers."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import threading
import time
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
        try:
            command = self.build_command(request)
        except (FileNotFoundError, IsADirectoryError) as exc:
            return self._error_result(request, str(exc), VerificationStatus.ERROR)

        digest = _digest(request.candidate_artifact)
        started = time.monotonic()
        lock = self._dqi_lock if benchmark_id == "DQI" else _NullLock()
        with lock:
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
        if isinstance(value, dict) and end > parsed_length:
            parsed = value
            parsed_length = end
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
