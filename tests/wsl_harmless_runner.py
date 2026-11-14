"""Test-only WSL entrypoint that exercises the production runner safely."""

from __future__ import annotations

import io
import json
import os
from pathlib import Path
import sys

from evaluation import wsl_verifier_runner as runner


def main() -> int:
    request = json.loads(sys.stdin.read())
    candidate = Path(request["candidate_artifact"])
    runner.APPLICATION_ROOT = candidate.parent
    runner.OFFICIAL_ROOT = candidate.parent / "not-official"
    verifier = candidate.parent / "not-official" / "harmless_verifier.py"
    marker = candidate.parent / "harmless-child.pid"
    verifier.parent.mkdir(parents=True, exist_ok=True)
    verifier.write_text(
        "import subprocess, sys, time\n"
        f"child = subprocess.Popen([sys.executable, '-c', "
        f"'import time; time.sleep(120)'])\n"
        f"open({str(marker)!r}, 'w', encoding='utf-8').write(str(child.pid))\n"
        "time.sleep(120)\n",
        encoding="utf-8",
    )
    runner._official_verifier_path = lambda _benchmark_id: verifier
    runner.DEFAULT_PID_FILE = Path(os.environ["Q_OPTIMUS_WSL_PID_FILE"])
    sys.stdin = io.StringIO(json.dumps(request))
    return runner.main()


if __name__ == "__main__":
    raise SystemExit(main())
