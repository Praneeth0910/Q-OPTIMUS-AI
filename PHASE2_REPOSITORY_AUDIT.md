# Phase 2 Repository Audit

## Scope and evidence

This audit records the repository state inspected before Phase 2 implementation.
It is based on the tracked source tree, configuration, requirements, and tests
available in this workspace. The historical benchmark measurements in the Phase
2 execution prompt are treated as source-provided records; they have not been
reproduced from this checkout.

## Existing architecture

- `launch_agent.py` is the CLI entry point. It synchronizes task metadata,
  optionally initializes W&B/Weave, downloads Kaggle data, and starts
  `agents.main_agent.MainAgent`.
- `agents/main_agent.py` runs the top-level LLM tool loop. It dispatches
  developer, research, filesystem, and idea-pool operations and persists
  per-run artifacts under `task/<slug>/<run_id>/`.
- `agents/researcher.py` implements a web-research sub-agent with Exa,
  Firecrawl, shared filesystem tools, and per-call audit records.
- `tools/` contains the developer and filesystem tool implementations.
- `prompts/` and `schemas/` contain LLM prompts and tool schemas.
- `utils/` contains message compaction, competition-data setup, guardrail,
  idea-pool, output, and skill helpers.
- `scripts/viewer/` provides a Flask transcript and artifact viewer.
- `tests/` contains focused unit tests for the existing launcher, agent loops,
  tools, utilities, and viewer.

## Reusable components

- `project_config.get_config()` provides cached YAML configuration loading.
- `tools.filesystem.execute_filesystem_tool()` provides the existing bounded
  filesystem tool boundary.
- `utils.competition_data` provides Kaggle data setup and description
  generation.
- Existing JSONL run logs and per-run artifact directories provide a natural
  location for orchestration audit records.
- Existing pytest fixtures and monkeypatching patterns are suitable for
  verifier subprocess and storage tests.

## Missing Phase 2 components

The inspected source tree does not contain:

- A benchmark registry or benchmark configuration.
- Official benchmark artifacts or verifier source trees.
- A unified verifier adapter.
- Candidate structural validation.
- Normalized resource analysis.
- SQLite experiment tracking.
- A strategy engine.
- A verified/Pareto candidate archive.
- A benchmark-specific orchestration interface.
- Phase 2 architecture, integration, or implementation documentation.

The absence of official assets means reference artifact existence and hash
validation can only be implemented as explicit runtime checks. It is not
possible to claim that the references have been validated in this checkout.

## Dependency and compatibility risks

- `requirements.txt` is a broad ML stack and includes optional, heavyweight
  packages. Phase 2 should use the Python standard library where possible and
  should not add dependencies without a demonstrated need.
- The current source uses Python 3.10+ type syntax such as built-in generic
  collections and union operators.
- W&B, Weave, Exa, Firecrawl, and other integrations are optional in parts of
  the existing application; new benchmark infrastructure should not require
  network credentials to import or test.
- Official verifier command lines and output schemas are unavailable locally,
  so adapter execution must be configurable and must distinguish unknown
  configuration from a verified PASS.
- DQI verification requires an exclusive, one-worker policy because the
  supplied historical record reports approximately 7.26 GiB peak RSS.

## Required implementation approach

1. Add immutable, data-driven benchmark baseline records with explicit
   provenance and unknown values where local evidence is unavailable.
2. Add a unified subprocess adapter that validates paths, applies benchmark
   policies, captures output, enforces timeouts, and parses official status
   rather than trusting exit codes.
3. Add SQLite persistence for immutable candidate identities and experiment
   metadata.
4. Add resource normalization and metric-aware improvement calculations without
   converting missing values to zero.
5. Add explainable strategy ranking in log-score space using explicit
   uncertainty and resource constraints.
6. Add candidate validation and a verified-only Pareto archive.
7. Integrate these components through a small orchestration service without
   replacing the existing MainAgent loop.
8. Add focused unit tests with mocked subprocesses and temporary databases;
   never run expensive official verifiers as part of routine tests.
9. Document only behavior that is actually implemented and identify all
   unverified benchmark facts as unknown.

## Planned implementation stages

### Stage 1 — Baseline registry

Create `benchmarks/` and `config/benchmarks.yaml` with the three benchmark
identities and the historical records supplied in the execution prompt.
Reference hashes and metrics will be stored as provenance-bearing values, while
local artifact validation will fail explicitly when the referenced assets are
not present.

### Stage 2 — Verifier adapter

Create `evaluation/verifier_adapter.py` with typed request/result objects,
benchmark policies, controlled subprocess execution, timeout handling, output
capture, and official-result parsing.

### Stage 3 — Experiment tracking

Create `storage/` with a SQLite schema and transaction-safe tracker. Candidate
artifacts will be identified by content hash and will never overwrite baseline
records or prior experiments.

### Stage 4 — Resource analysis

Create metric normalization and comparison helpers. Metrics absent from an
official result remain `None` and are excluded from invalid percentage
calculations.

### Stage 5 — Strategy engine

Create an explainable opportunity model and deterministic ranking function that
optimizes expected log-score improvement per research hour while accounting for
success probability, verification risk, runtime, memory, and uncertainty.

### Stage 6 — Candidate validation and archive

Create structural validation, verified-pass promotion rules, and a
metric-aware Pareto archive that keeps references, unverified candidates, and
verified candidates separate.

### Stage 7 — Orchestrator integration

Expose a small benchmark workflow service that composes registry, validation,
verification, tracking, analysis, and archive components. Existing MainAgent
behavior will remain unchanged unless an explicit integration point is needed.

### Stage 8 — Validation and reporting

Run focused tests first, then the existing suite. Produce the required Phase 2
documentation and report actual test results and known limitations.

## Architectural conflicts and constraints

- The existing `task/` run directory is intended for LLM-generated artifacts,
  whereas benchmark candidate and experiment artifacts require separate,
  stable storage. The new storage layer should accept explicit paths rather
  than silently placing candidates in the existing developer directories.
- Existing agent tools are LLM-facing and permissive within their configured
  boundaries. Official verification must remain a non-LLM, explicit API with
  allowlisted executable paths and no authority to edit official assets.
- A zero subprocess exit code is not sufficient evidence of a benchmark PASS.
  The adapter must require a parsed official status.
- Partial-domain or reduced-shot executions must be represented as non-official
  runs and must never promote a candidate.

## Audit conclusion

The repository is suitable for incremental Phase 2 development, but the
official benchmark assets and verifier implementations required for local
execution are not present. The implementation can therefore provide reliable
schemas, provenance, validation, controlled execution, persistence, analysis,
ranking, and archive rules, while reporting official verification as unknown
until those assets are supplied and run.
