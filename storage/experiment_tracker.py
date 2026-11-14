"""SQLite-backed, append-preserving experiment history."""

from __future__ import annotations

import json
import platform
import sqlite3
import sys
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


@dataclass
class Experiment:
    benchmark_id: str
    candidate_id: str
    circuit_artifact_path: str
    strategy: str
    experiment_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    parent_candidate_id: str | None = None
    source_revision: str | None = None
    circuit_sha256: str | None = None
    parameters: Mapping[str, Any] = field(default_factory=dict)
    random_seed: int | None = None
    python_version: str = field(default_factory=lambda: sys.version.split()[0])
    environment: Mapping[str, Any] = field(
        default_factory=lambda: {"platform": platform.platform()}
    )
    started_at: str | None = None
    ended_at: str | None = None
    runtime_seconds: float | None = None
    peak_memory_kb: int | None = None
    verification_status: str = "NOT_RUN"
    official_score: float | None = None
    reference_score: float | None = None
    relative_improvement: float | None = None
    failure_reason: str | None = None
    research_notes: str | None = None


class ExperimentTracker:
    """Persist every experiment without overwriting prior candidates."""

    def __init__(self, database_path: Path):
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS experiments (
                    experiment_id TEXT PRIMARY KEY,
                    benchmark_id TEXT NOT NULL,
                    candidate_id TEXT NOT NULL,
                    parent_candidate_id TEXT,
                    source_revision TEXT,
                    circuit_artifact_path TEXT NOT NULL,
                    circuit_sha256 TEXT,
                    strategy TEXT NOT NULL,
                    parameters_json TEXT NOT NULL,
                    random_seed INTEGER,
                    python_version TEXT NOT NULL,
                    environment_json TEXT NOT NULL,
                    started_at TEXT,
                    ended_at TEXT,
                    runtime_seconds REAL,
                    peak_memory_kb INTEGER,
                    verification_status TEXT NOT NULL,
                    official_score REAL,
                    reference_score REAL,
                    relative_improvement REAL,
                    failure_reason TEXT,
                    research_notes TEXT,
                    created_at TEXT NOT NULL,
                    UNIQUE (benchmark_id, candidate_id)
                )
                """
            )

    def create(self, experiment: Experiment) -> str:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO experiments (
                    experiment_id, benchmark_id, candidate_id,
                    parent_candidate_id, source_revision, circuit_artifact_path,
                    circuit_sha256, strategy, parameters_json, random_seed,
                    python_version, environment_json, started_at, ended_at,
                    runtime_seconds, peak_memory_kb, verification_status,
                    official_score, reference_score, relative_improvement,
                    failure_reason, research_notes, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                          ?, ?, ?, ?, ?)
                """,
                self._values(experiment, now),
            )
        return experiment.experiment_id

    def update(self, experiment_id: str, **changes: Any) -> None:
        allowed = {
            "parent_candidate_id", "source_revision", "circuit_artifact_path",
            "circuit_sha256", "strategy", "random_seed", "python_version",
            "started_at", "ended_at", "runtime_seconds", "peak_memory_kb",
            "verification_status", "official_score", "reference_score",
            "relative_improvement", "failure_reason", "research_notes",
            "parameters", "environment",
        }
        unknown = set(changes) - allowed
        if unknown:
            raise ValueError(f"Unknown experiment fields: {sorted(unknown)}")
        assignments = []
        values: list[Any] = []
        for key, value in changes.items():
            if key in {"parameters", "environment"}:
                continue
            assignments.append(f"{key} = ?")
            values.append(value)
        for key in ("parameters", "environment"):
            if key in changes:
                assignments.append(f"{key}_json = ?")
                values.append(json.dumps(changes[key], sort_keys=True))
        if not assignments:
            raise ValueError("At least one experiment field is required")
        values.append(experiment_id)
        with self._connect() as connection:
            cursor = connection.execute(
                f"UPDATE experiments SET {', '.join(assignments)} "
                "WHERE experiment_id = ?",
                values,
            )
            if cursor.rowcount != 1:
                raise KeyError(f"Unknown experiment ID: {experiment_id}")

    def get(self, experiment_id: str) -> Experiment:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM experiments WHERE experiment_id = ?",
                (experiment_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"Unknown experiment ID: {experiment_id}")
        return self._from_row(row)

    def list(
        self,
        *,
        benchmark_id: str | None = None,
        strategy: str | None = None,
        verification_status: str | None = None,
    ) -> list[Experiment]:
        clauses = []
        values: list[str] = []
        for column, value in (
            ("benchmark_id", benchmark_id),
            ("strategy", strategy),
            ("verification_status", verification_status),
        ):
            if value is not None:
                clauses.append(f"{column} = ?")
                values.append(value)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM experiments{where} ORDER BY created_at",
                values,
            ).fetchall()
        return [self._from_row(row) for row in rows]

    def compare(self, benchmark_id: str) -> list[Experiment]:
        return sorted(
            self.list(benchmark_id=benchmark_id),
            key=lambda item: (
                item.official_score is None,
                item.official_score if item.official_score is not None else float("inf"),
            ),
        )

    def export_json(self, output_path: Path) -> None:
        records = [asdict(item) for item in self.list()]
        Path(output_path).write_text(
            json.dumps(records, indent=2, sort_keys=True), encoding="utf-8"
        )

    @staticmethod
    def _values(experiment: Experiment, created_at: str) -> tuple[Any, ...]:
        return (
            experiment.experiment_id, experiment.benchmark_id,
            experiment.candidate_id, experiment.parent_candidate_id,
            experiment.source_revision, experiment.circuit_artifact_path,
            experiment.circuit_sha256, experiment.strategy,
            json.dumps(experiment.parameters, sort_keys=True),
            experiment.random_seed, experiment.python_version,
            json.dumps(experiment.environment, sort_keys=True),
            experiment.started_at, experiment.ended_at, experiment.runtime_seconds,
            experiment.peak_memory_kb, experiment.verification_status,
            experiment.official_score, experiment.reference_score,
            experiment.relative_improvement, experiment.failure_reason,
            experiment.research_notes, created_at,
        )

    @staticmethod
    def _from_row(row: sqlite3.Row) -> Experiment:
        values = dict(row)
        values["parameters"] = json.loads(values.pop("parameters_json"))
        values["environment"] = json.loads(values.pop("environment_json"))
        values.pop("created_at")
        return Experiment(**values)
