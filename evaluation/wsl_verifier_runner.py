"""WSL-side runner for controlled execution of official verifiers.

The runner accepts one JSON request on stdin and emits one JSON response on
stdout. It is intentionally independent of the Windows application process so
Linux-native verifier dependencies and process locks stay inside WSL.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from typing import Any, Sequence

_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(_REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPOSITORY_ROOT))

from benchmarks.registry import get_benchmark

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows imports the module for tests
    fcntl = None

try:
    import psutil
except ImportError:  # pragma: no cover - exercised in environments without psutil
    psutil = None


OFFICIAL_ROOT = Path(
    "/home/praneeth_0910/projects/q-optimus-quantum/official"
).resolve()
KICKMIX_PATH = OFFICIAL_ROOT / "kickmix" / "kickmix"
APPLICATION_ROOT = Path("/mnt/d/Projects/Q-OPTIMUS-AI").resolve()
DQI_LOCK_PATH = Path("/tmp/q-optimus-ai-dqi-verifier.lock")
DEFAULT_LOCK_TIMEOUT_SECONDS = 30.0
DEFAULT_SAMPLE_INTERVAL_SECONDS = 0.05
DEFAULT_PID_FILE = APPLICATION_ROOT / ".runtime" / "wsl-verifier.pid"


class RunnerInputError(ValueError):
    """Raised when a runner request violates the execution policy."""


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _official_verifier_path(benchmark_id: str) -> Path:
    configured = Path(get_benchmark(benchmark_id).verifier_path)
    if configured.parts and configured.parts[0] == "official":
        configured = Path(*configured.parts[1:])
    return (OFFICIAL_ROOT / configured).resolve()


def _validate_request(request: dict[str, Any]) -> tuple[str, Path, tuple[str, ...], float]:
    if not isinstance(request, dict):
        raise RunnerInputError("request must be a JSON object")
    benchmark_id = request.get("benchmark_id")
    candidate = request.get("candidate_artifact")
    timeout = request.get("timeout_seconds", 3600.0)
    authorized = request.get("authorized", False)
    if not isinstance(benchmark_id, str) or not benchmark_id.strip():
        raise RunnerInputError("benchmark_id must be a non-empty string")
    if not isinstance(candidate, str) or not candidate:
        raise RunnerInputError("candidate_artifact must be a path string")
    if authorized is not True:
        raise RunnerInputError("runner requires explicit authorization")
    if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or timeout <= 0:
        raise RunnerInputError("timeout_seconds must be positive")
    try:
        benchmark = get_benchmark(benchmark_id)
    except KeyError as exc:
        raise RunnerInputError(str(exc)) from exc
    candidate_path = Path(candidate).resolve()
    allowed = _is_within(candidate_path, APPLICATION_ROOT) or _is_within(
        candidate_path, OFFICIAL_ROOT / "reference"
    )
    if not allowed:
        raise RunnerInputError("candidate_artifact is outside the approved roots")
    if not candidate_path.exists():
        raise RunnerInputError(f"candidate_artifact does not exist: {candidate_path}")
    verifier = _official_verifier_path(benchmark.benchmark_id)
    if not _is_within(verifier, OFFICIAL_ROOT) or not verifier.is_file():
        raise RunnerInputError(f"official verifier is unavailable: {verifier}")
    extra_args = request.get("extra_args", [])
    if not isinstance(extra_args, list) or not all(
        isinstance(item, str) for item in extra_args
    ):
        raise RunnerInputError("extra_args must be a list of strings")
    if benchmark.benchmark_id == "DQI" and any(
        item == "--max-workers" or item.startswith("--max-workers=")
        for item in extra_args
    ):
        raise RunnerInputError("DQI worker count is controlled by the runner")
    return benchmark.benchmark_id, candidate_path, tuple(extra_args), float(timeout)


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    paths = (
        sorted(item for item in path.rglob("*") if item.is_file())
        if path.is_dir()
        else [path]
    )
    for item in paths:
        if path.is_dir():
            digest.update(item.relative_to(path).as_posix().encode("utf-8"))
        with item.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    return digest.hexdigest()


def _command(benchmark_id: str, candidate: Path, extra_args: Sequence[str]) -> tuple[str, ...]:
    verifier = _official_verifier_path(benchmark_id)
    if benchmark_id == "DFTHCSOS" and candidate.is_dir():
        args = (str(candidate / "submission.npz"), str(candidate))
    else:
        args = (str(candidate),)
    command = [sys.executable, str(verifier), *args]
    if benchmark_id == "DQI":
        command.extend(["--max-workers", "1"])
    command.extend(extra_args)
    return tuple(command)


def _sample_peak_memory(
    pid: int, stop: "threading.Event", peak: list[int | None]
) -> None:
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
        stop.wait(DEFAULT_SAMPLE_INTERVAL_SECONDS)


def _kill_process_group(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    if hasattr(os, "killpg"):
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    else:
        process.kill()
    process.wait()


def _write_pid_file(path: Path, process: subprocess.Popen[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    record: dict[str, int] = {"runner_pid": os.getpid(), "verifier_pid": process.pid}
    if hasattr(os, "getpgid"):
        record["verifier_pgid"] = os.getpgid(process.pid)
    path.write_text(json.dumps(record), encoding="utf-8")


def _remove_pid_file(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def _lock_file(timeout: float):
    if fcntl is None:
        raise RuntimeError("fcntl is required for the WSL verifier runner")
    lock_path = Path(DQI_LOCK_PATH)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+")
    deadline = time.monotonic() + timeout
    while True:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return handle
        except BlockingIOError:
            if time.monotonic() >= deadline:
                handle.close()
                raise TimeoutError("timed out acquiring the DQI verifier lock")
            time.sleep(0.1)


def execute_command(
    command: Sequence[str],
    *,
    timeout_seconds: float,
    lock_dqi: bool = False,
    pid_file: Path | None = DEFAULT_PID_FILE,
) -> dict[str, Any]:
    """Execute a command with process-group cleanup and resource reporting."""
    lock = _lock_file(DEFAULT_LOCK_TIMEOUT_SECONDS) if lock_dqi else None
    started = time.monotonic()
    process: subprocess.Popen[str] | None = None
    peak: list[int | None] = [None]
    sampler = None
    stop = None
    try:
        process = subprocess.Popen(
            list(command),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        if pid_file is not None:
            _write_pid_file(Path(pid_file), process)
        if psutil is not None:
            from threading import Event, Thread

            stop = Event()
            sampler = Thread(
                target=_sample_peak_memory,
                args=(process.pid, stop, peak),
                daemon=True,
            )
            sampler.start()
        try:
            stdout, stderr = process.communicate(timeout=timeout_seconds)
            timed_out = False
        except subprocess.TimeoutExpired as exc:
            _kill_process_group(process)
            stdout, stderr = process.communicate()
            if exc.stdout:
                stdout = exc.stdout
            if exc.stderr:
                stderr = exc.stderr
            timed_out = True
        return {
            "exit_code": process.returncode,
            "stdout": stdout,
            "stderr": stderr,
            "runtime_seconds": time.monotonic() - started,
            "peak_memory_kb": peak[0],
            "peak_memory_available": psutil is not None,
            "timed_out": timed_out,
        }
    finally:
        if stop is not None:
            stop.set()
        if sampler is not None:
            sampler.join(timeout=1)
        if lock is not None:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
            lock.close()
        if pid_file is not None:
            _remove_pid_file(Path(pid_file))


def run_request(request: dict[str, Any]) -> dict[str, Any]:
    benchmark_id, candidate, extra_args, timeout = _validate_request(request)
    command = _command(benchmark_id, candidate, extra_args)
    result = execute_command(
        command,
        timeout_seconds=timeout,
        lock_dqi=benchmark_id == "DQI",
    )
    parsed = _parse_json(result["stdout"])
    if result["timed_out"]:
        status = "TIMEOUT"
    elif parsed is None:
        status = "ERROR"
    elif result["exit_code"] != 0:
        status = "ERROR"
    else:
        status = str(parsed.get("status", parsed.get("result", "FAIL"))).upper()
    result.update(
        {
            "status": status,
            "benchmark_id": benchmark_id,
            "candidate_sha256": _digest(candidate),
            "command": list(command),
            "parsed_json": parsed,
        }
    )
    return result


def _parse_json(stdout: str) -> dict[str, Any] | None:
    decoder = json.JSONDecoder()
    parsed = None
    parsed_end = -1
    for index, character in enumerate(stdout):
        if character != "{":
            continue
        try:
            value, end = decoder.raw_decode(stdout[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and index + end > parsed_end:
            parsed = value
            parsed_end = index + end
    return parsed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", default="-")
    args = parser.parse_args()
    try:
        payload = sys.stdin.read() if args.request == "-" else Path(args.request).read_text()
        response = run_request(json.loads(payload))
    except (OSError, RunnerInputError, TimeoutError, json.JSONDecodeError) as exc:
        response = {
            "status": "NOT_RUN" if isinstance(exc, RunnerInputError) else "ERROR",
            "error": str(exc),
            "peak_memory_kb": None,
            "peak_memory_available": psutil is not None,
        }
    except Exception as exc:  # runner boundary must always emit JSON
        response = {
            "status": "ERROR",
            "error": f"{type(exc).__name__}: {exc}",
            "peak_memory_kb": None,
            "peak_memory_available": psutil is not None,
        }
    print(json.dumps(response, sort_keys=True))
    status = response["status"]
    if status == "ERROR":
        return 1
    if status == "NOT_RUN":
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
