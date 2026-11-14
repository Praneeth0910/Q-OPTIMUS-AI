"""Evidence-labelled benchmark research hypotheses."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ResearchHypothesis:
    benchmark_id: str
    title: str
    track: str
    rationale: str
    evidence: tuple[str, ...]
    measurable_target: str
    risk: str


class HypothesisGenerator:
    """Return research directions without asserting experimental success."""

    def generate(self, benchmark_id: str) -> tuple[ResearchHypothesis, ...]:
        benchmark_id = benchmark_id.upper()
        tracks = {
            "INVSQRT": (
                ("Fixed-point reversible arithmetic", "reduce arithmetic cost"),
                ("Polynomial evaluation", "reduce approximation circuit cost"),
                ("Newton-Raphson alternatives", "test correction-depth trade-offs"),
                ("Ancilla and Toffoli reduction", "reduce resource footprint"),
            ),
            "DQI": (
                ("Reversible Reed-Solomon decoding", "reduce decoder cost"),
                ("Extended Euclidean Algorithm", "reduce syndrome-decoder depth"),
                ("Chien search", "reduce search arithmetic"),
                ("Uncomputation and circuit optimization", "reduce retained work"),
            ),
            "DFTHCSOS": (
                ("ANGLE circuit synthesis", "target dominant ANGLE resources"),
                ("Givens rotation trees", "reduce state-preparation cost"),
                ("QROM", "reduce lookup overhead"),
                ("State preparation and factorization", "test resource trade-offs"),
            ),
        }
        if benchmark_id not in tracks:
            raise KeyError(f"Unknown benchmark ID: {benchmark_id}")
        return tuple(
            ResearchHypothesis(
                benchmark_id,
                title,
                title,
                rationale,
                ("Phase 3 research track; no experiment result established",),
                "official PASS with lower official cost",
                "correctness and verifier acceptance are unknown",
            )
            for title, rationale in tracks[benchmark_id]
        )
