"""Small composition layer for benchmark candidate evaluation."""

from __future__ import annotations

from pathlib import Path

from benchmarks.registry import get_baseline
from evaluation.pareto_archive import ParetoArchive
from evaluation.resource_analyzer import normalize_metrics
from evaluation.verifier_adapter import (
    VerificationRequest,
    VerificationResult,
    VerificationStatus,
    VerifierAdapter,
)
from engine.candidate_validator import CandidateStatus, validate_candidate
from storage.experiment_tracker import Experiment, ExperimentTracker


class ExperimentRunner:
    """Run structural validation, official verification, and persistence."""

    def __init__(
        self,
        tracker: ExperimentTracker,
        archive: ParetoArchive | None = None,
        verifier: VerifierAdapter | None = None,
    ):
        self.tracker = tracker
        self.archive = archive or ParetoArchive()
        self.verifier = verifier or VerifierAdapter()

    def evaluate(
        self,
        *,
        benchmark_id: str,
        candidate_id: str,
        artifact: Path,
        verifier_root: Path,
        strategy: str,
        source_revision: str | None = None,
        timeout_seconds: float = 3600,
        authorized: bool = False,
        use_wsl_runner: bool | None = None,
    ) -> VerificationResult:
        validation = validate_candidate(benchmark_id, candidate_id, artifact)
        experiment = Experiment(
            benchmark_id=benchmark_id.upper(),
            candidate_id=candidate_id,
            circuit_artifact_path=str(artifact),
            circuit_sha256=validation.artifact_sha256,
            strategy=strategy,
            source_revision=source_revision,
            verification_status=(
                "VERIFICATION_PENDING"
                if validation.status is CandidateStatus.STRUCTURALLY_VALID
                else validation.status.value
            ),
        )
        self.tracker.create(experiment)
        if validation.status is not CandidateStatus.STRUCTURALLY_VALID:
            result = VerificationResult(
                benchmark_id=benchmark_id.upper(),
                candidate_id=candidate_id,
                status=VerificationStatus.ERROR,
                official_score=None,
                verified_shots=None,
                resource_metrics={},
                circuit_sha256=validation.artifact_sha256,
                verifier_exit_code=None,
                verification_parameters={},
                runtime_seconds=None,
                peak_memory_kb=None,
                stdout="",
                stderr="",
                error="; ".join(validation.errors),
                official_run=False,
            )
        else:
            result = self.verifier.run(
                VerificationRequest(
                    benchmark_id=benchmark_id,
                    candidate_id=candidate_id,
                    candidate_artifact=artifact,
                    verifier_root=verifier_root,
                    timeout_seconds=timeout_seconds,
                    authorized=authorized,
                    use_wsl_runner=use_wsl_runner,
                )
            )

        reference = get_baseline(benchmark_id)
        relative = None
        if reference.official_score and result.official_score:
            relative = reference.official_score / result.official_score - 1
        self.tracker.update(
            experiment.experiment_id,
            verification_status=result.status.value,
            official_score=result.official_score,
            reference_score=reference.official_score,
            relative_improvement=relative,
            circuit_sha256=result.circuit_sha256,
            runtime_seconds=result.runtime_seconds,
            peak_memory_kb=result.peak_memory_kb,
            failure_reason=result.error,
        )
        if result.status.value == "PASS":
            metrics = normalize_metrics(
                result.resource_metrics,
                runtime_seconds=result.runtime_seconds,
                peak_memory_kb=result.peak_memory_kb,
                verified_shots=result.verified_shots,
            )
            self.archive.promote(
                result,
                {
                    key: value
                    for key, value in metrics.values.items()
                    if isinstance(value, (int, float))
                },
            )
        return result
