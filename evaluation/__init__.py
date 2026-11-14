"""Evaluation, verification, and resource-analysis primitives."""

from .verifier_adapter import (
    VerificationRequest,
    VerificationResult,
    VerificationStatus,
    VerifierAdapter,
)

__all__ = [
    "VerificationRequest",
    "VerificationResult",
    "VerificationStatus",
    "VerifierAdapter",
]
