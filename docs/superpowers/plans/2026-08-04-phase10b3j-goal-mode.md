# Phase 10B3J Goal Mode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Certify and, only after all frozen gates pass, locally activate the fixed Candidate generation for the industrial-manual cited Q&A service.

**Architecture:** Three isolated worker branches share the same frozen base commit. Agent A owns deterministic evaluation evidence and lifecycle fixtures, Agent B owns claim-level citation/support behavior, and Agent C owns bounded post-retrieval recovery and partial generation. Agent D reviews and integrates their commits in feature-flag order, runs the permitted evaluations, then either performs the guarded local activation or produces an evidence-backed blocked delivery.

**Tech Stack:** Python, FastAPI, LightRAG, pytest, Ruff, JSON/JSONL evaluation artifacts, SQLite/Qdrant local staging.

## Global Constraints

- Candidate is fixed at `5bca792c08fcf2f7b08cbaed09b6d525`; old Active is `a2d1c77ce08b414495e9d845cc42f799`.
- Do not read or execute Holdout, alter Golden, chunking, embedding, initial TopK, rerank, Candidate contents or Candidate state to bypass a query gate.
- Keep `QA_SUPPLEMENTAL_RETRIEVAL_ENABLED=false`; no second retrieval or LLM call.
- New flags default to false and must be represented in settings, safe `/version`, retrieval trace, evaluation config and config SHA.
- Validation is one frozen run only and is reachable only after every Development gate passes.
- Do not activate Candidate, tag, package RC, deploy, force-push, delete old generation/collection, or record secrets unless the respective explicit gate permits it.
- Every worker commits only its own scoped changes; D integrates and verifies independently.

---

### Task 1: Freeze common baseline and J0 evidence

**Files:**
- Create: `evaluation/phase10b3j_goal/j0_development_metrics.json`
- Create: `evaluation/phase10b3j_goal/lifecycle_contract_results.json`
- Create: `evaluation/phase10b3j_goal/machine_review_results.json`
- Modify: `scripts/phase10b3j_*` and `tests/unit|integration/*` only where required for deterministic evidence.

**Interfaces:**
- Consumes: `evaluation/phase10b3j_r1/j0_development_results.jsonl`, lineage/coverage/grounding matrices, `evaluation/phase10b3j/manual_support_review_packet.jsonl`, and the `phase10b3d-metric-policy-v1` policy.
- Produces: J0 metric and lifecycle contracts that D uses as the pre-experiment baseline.

- [ ] Recompute J0 without model calls and emit all named metric dimensions with `definition_version=phase10b3d-metric-policy-v1`.
- [ ] Compare J0 against R2 and fail if any non-regression threshold is exceeded; repair only instrumentation and rerun affected unit tests plus the offline computation (at most three rounds).
- [ ] Test ready/staged/building/failed/deleting/deleted/missing/wrong generation paths against an isolated fixture DB, recording before/after Active pointers.
- [ ] Run three independent machine review passes over the 15-case packet, preserving ambiguous outcomes and writing reviewer outputs plus the required adjudicated decisions.
- [ ] Commit the Agent A changes with test commands/results and limitations in its handoff.

### Task 2: Implement and prove J1 citation pruning

**Files:**
- Modify: `src/industrial_rag/claim_citation_pruning.py`, `src/industrial_rag/config.py`, `src/industrial_rag/api.py`, `src/industrial_rag/retrieval_trace.py`
- Create: `src/industrial_rag/claim_support_matcher.py`
- Test: `tests/unit/test_claim_citation_pruning.py`, `tests/unit/test_claim_support_matcher.py`

**Interfaces:**
- Consumes: answer claims and Provider Registry evidence identities.
- Produces: minimal per-claim supported evidence, with unsupported claims and invalid/parent/wrong-generation references removed.

- [ ] Write failing tests covering minimal citation sets, unknown evidence rejection, parent non-disclosure, wrong-generation rejection, and independent removal of unsupported claims.
- [ ] Implement the matcher and pruning behind `QA_CLAIM_CITATION_PRUNING_ENABLED`, default false; add safe observability and config hashing inputs without changing disabled behavior.
- [ ] Run unit tests, deterministic replay, and 20-case overcitation subset; D alone will run the full Development evaluation with only this flag enabled.
- [ ] Commit Agent B changes and give D exact commands/results/limitations.

### Task 3: Implement bounded J2-J4 recovery features

**Files:**
- Modify: `src/industrial_rag/post_retrieval_recovery.py`, `src/industrial_rag/structured_generation_policy.py`, `src/industrial_rag/config.py`, `src/industrial_rag/api.py`, `src/industrial_rag/retrieval_trace.py`
- Test: `tests/unit/test_post_retrieval_recovery.py`, `tests/unit/test_structured_generation_policy.py`

**Interfaces:**
- Consumes: only existing Top20 evidence, answer points, Provider Registry and resolved requirement IDs.
- Produces: support-verified false-negative recoveries, up-to-five complementary evidence selection, and partial answers that follow the stated structured schema.

- [ ] Add failing tests proving object/parameter/numeric/unit/condition/model/negation checks, no lexical-only recovery, no extra retrieval, and disabled-feature parity.
- [ ] Implement `QA_GROUNDING_FALSE_NEGATIVE_RECOVERY_ENABLED`, `QA_COVERAGE_AWARE_SELECTION_ENABLED`, and `QA_PARTIAL_GENERATION_ENABLED`, all default false, with safe version/trace/config-SHA representation.
- [ ] Enforce max five selected evidence records and remove invalid evidence IDs and unsupported points independently; schema failures use the old safe path.
- [ ] Run unit tests plus deterministic J2 five-case, J3 three-case and J4 replay fixtures; commit Agent C changes with evidence.

### Task 4: Integrate and run one-variable Development experiments

**Files:**
- Create: `evaluation/phase10b3j_goal/j1_results.json`, `j2_results.json`, `j3_results.json`, `j4_results.json`, `experiment_results.json`, `development_results.jsonl`
- Modify: integration glue only after review of worker diffs.

**Interfaces:**
- Consumes: three worker commits and J0 metrics.
- Produces: accepted/rejected commit sequence and a frozen Development configuration.

- [ ] Inspect each worker diff and execute its declared tests; integrate in J1→J2→J3→J4 order, resolving only minimal wiring conflicts.
- [ ] For each flag: record base/code commits and config SHA; run unit tests, deterministic replay, affected failure subset, and exactly one full 36-question Development evaluation.
- [ ] Compute all gates. On failure, turn off and revert only that experiment, record the reason and rollback commit; no composed results between runs.
- [ ] Verify all accepted experiments and, if Development still fails after two deterministic repair rounds, write the required failure matrix and skip all Validation work.

### Task 5: Freeze, final evaluate, activate or block, and deliver

**Files:**
- Create: `evaluation/phase10b3j_goal/validation_results.jsonl`, `final_52_results.jsonl`, `final_metrics.json`, `activation_results.json`, `rollback_proof.json`, `secret_scan.json`, `evaluation_manifest.json`, `delivery_manifest.json`
- Create: `docs/phase-10b3j-goal-mode-final-report.md`

**Interfaces:**
- Consumes: a passing frozen Development commit/config only.
- Produces: a remote-auditable final delivery, or a truthful blocked package that keeps old Active intact.

- [ ] If and only if Development gates pass, freeze commit/prompt/config/flags, run Validation once, then one complete 52-question run; do not tune afterward.
- [ ] If final gates pass, back up/transaction-protect activation, switch the active pointer, run five ordinary-query staging smokes, prove explicit old-generation queryability and execute a rollback proof. Immediately restore old Active on activation smoke failure.
- [ ] If any final gate fails, preserve old Active and write failure cases, single root cause, failure matrix, and next manual dependency.
- [ ] At final HEAD run collection, full pytest, Ruff and scoped secret scan. Commit report only after evidence has been checked, push the branch without force, and refresh delivery manifest with remote head SHA in a follow-up commit if needed.
