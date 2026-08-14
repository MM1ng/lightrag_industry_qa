# Conversation E2E Ragas Development Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Measure whether conversation query rewriting transfers frozen retrieval gains into real LightRAG answer quality using a fair baseline/candidate A/B experiment on the 18-case Development dataset.

**Architecture:** Keep production query behavior unchanged. Add an evaluation-only runtime adapter that sends the dependent query directly to `LightRAGService.query()` for BASELINE and sends the frozen `QueryRewriter` output to the same service/config for CANDIDATE. Persist complete case traces, deterministic custom metrics, Ragas 0.3.9 semantic-judge results, gate decisions, and a Markdown/JSON report without reading Validation or Holdout data.

**Tech Stack:** Python 3.11, pytest/pytest-asyncio, Ragas 0.3.9, Pydantic, existing `LightRAGService`, frozen conversation dataset, local JSONL Ragas experiment backend.

## Global Constraints

- Keep `ragas==0.3.9`; do not upgrade it.
- Use exactly `data/evaluation/conversation_retrieval_development.jsonl`, preserving 18 rows, source order, IDs, history, queries, gold standalone queries, and gold chunk IDs.
- BASELINE bypasses `QueryApplicationService` rewrite handling and calls the same `LightRAGService.query()` with the dependent query.
- CANDIDATE uses the existing `QueryRewriter`, validates its output against frozen `expected_standalone_query`, then calls the same `LightRAGService.query()`.
- Keep every LightRAG/runtime/generation/evidence/grounding/citation setting identical except runtime query text.
- Do not import Ragas from `src/industrial_rag` or change production API behavior.
- Do not access Validation or Holdout data and do not alter Gold, retrieval, embedding, reranker, generation, grounding, or citation policy.
- Semantic metrics use actual provider contexts for Faithfulness and the frozen standalone query as the same evaluator input for both arms.
- Judge failures remain in the denominator as `judge_error`; no fabricated scores or silent row removal.
- A blocked run contains no fabricated BASELINE/CANDIDATE metrics.

---

### Task 1: Freeze the evaluation contracts and helper projections

**Files:**
- Create: `evaluation/phase10/conversation_e2e_contracts.py`
- Create: `tests/evaluation/test_conversation_e2e_contracts.py`

**Interfaces:**
- Produces immutable `RuntimeConfigFingerprint`, `ArmRuntimeTrace`, `ConversationE2ECase`, and `JudgeConfig` dataclasses.
- Produces `fingerprint_dataset(path)`, `runtime_config_fingerprint(settings, query_options)`, `provider_context_payload(result)`, and `resolved_evaluation_user_input(case)`.
- Later runner code consumes these objects without importing production code into a new runtime path.

- [ ] **Step 1: Write failing tests** for 18-row fingerprint preservation, Validation/Holdout rejection, identical evaluator input, provider context hash extraction, and runtime fingerprint equality.
- [ ] **Step 2: Run `pytest tests/evaluation/test_conversation_e2e_contracts.py -q` and confirm the new imports/functions fail for the expected missing-symbol reason.
- [ ] **Step 3: Implement the dataclasses and pure helpers.** The dataset fingerprint must include raw SHA-256, semantic canonical SHA-256, case count, and ordered case IDs. The runtime fingerprint must include KB, generation, workspace, vector backend, embedding model/config, query options, and all relevant generation/evidence/grounding/citation settings exposed by `Settings`; it must omit query text.
- [ ] **Step 4: Run the focused tests and confirm they pass.
- [ ] **Step 5: Run `ruff check evaluation/phase10/conversation_e2e_contracts.py tests/evaluation/test_conversation_e2e_contracts.py`.

### Task 2: Build the fair development-only A/B runtime adapter

**Files:**
- Create: `evaluation/phase10/conversation_e2e_adapter.py`
- Create: `tests/evaluation/test_conversation_e2e_adapter.py`

**Interfaces:**
- Produces `async run_case(service, case, query_options, rewriter=None) -> ConversationE2ECase`.
- BASELINE calls only `service.query(case["dependent_query"], ...)` and never calls the rewriter.
- CANDIDATE calls `QueryRewriter.rewrite(case["dependent_query"], case["history"])`, validates `rewritten_query` against frozen gold, then calls the same service with the normalized rewrite.
- Captures answer status, answer, citations, answer points, grounding removals, retrieved ranks, selected evidence IDs, provider evidence IDs, provider context order/hash, latency, rewrite metadata, and failure layer.

- [ ] **Step 1: Write failing async tests** with a recording fake service and fake rewriter proving baseline bypass, candidate rewrite, same query options, same context construction, and complete per-arm fields.
- [ ] **Step 2: Run the focused adapter tests and verify they fail because the adapter is absent.
- [ ] **Step 3: Implement the adapter using only `LightRAGService.query()` as the downstream boundary.** Convert `QueryResult.retrieval_trace` into a JSON-safe trace; derive selected chunk IDs from the trace, provider evidence IDs/context hash from the trace, and classify failures using the existing taxonomy names without changing runtime behavior.
- [ ] **Step 4: Run the focused adapter tests and confirm they pass.
- [ ] **Step 5: Add tests for answer-status accounting and missing/failed runtime traces; run the focused file again.

### Task 3: Add canonical deterministic answer-quality metrics and paired gate

**Files:**
- Create: `evaluation/phase10/conversation_e2e_metrics.py`
- Create: `tests/evaluation/test_conversation_e2e_metrics.py`

**Interfaces:**
- Produces deterministic metric functions for supporting/evidence recall, false rejection, question-level citation accuracy, unsupported answer rate, expected/answer coverage, and retrieval Hit/Evidence Recall/MRR at 5/10.
- Produces `score_case(case)`, `aggregate_arm(rows)`, `paired_case_counts(rows)`, `classify_failure_layer(case)`, and `evaluate_gate(summary)`.
- Metrics with insufficient frozen fields return `{status: "metric_unavailable", reason: ...}` and never infer gold answers.

- [ ] **Step 1: Write failing tests** for canonical field use, unavailable metrics, denominator integrity, paired improved/unchanged/regressed counts, failure-layer precedence, and every R3_PASS/R3_MIXED/R3_FAIL/BLOCKED gate condition.
- [ ] **Step 2: Run the focused metrics tests and confirm expected failures.
- [ ] **Step 3: Implement the metric projection around existing canonical definitions where available; use explicit unavailable results where the dataset lacks answer/reference fields. Preserve all 18 cases in every denominator.
- [ ] **Step 4: Run the focused tests and confirm they pass.

### Task 4: Add Ragas 0.3.9 semantic judge execution with frozen contract

**Files:**
- Create: `evaluation/phase10/conversation_e2e_semantic.py`
- Create: `tests/evaluation/test_conversation_e2e_semantic.py`

**Interfaces:**
- Produces `JudgeConfig`, `semantic_smoke_test()`, and `score_semantic_rows(rows, judge_config)`.
- Uses Ragas `Faithfulness` with each arm's actual provider context and `ResponseRelevancy` with `resolved_evaluation_user_input(case)` for both arms.
- Persists per-case score, judge error, raw/structured result metadata where available, and exact judge configuration; failed calls remain denominator rows.

- [ ] **Step 1: Write failing tests** proving actual provider contexts are passed, both arms receive the same evaluation question, judge config is equal, failures become `judge_error`, and no row is dropped.
- [ ] **Step 2: Run the focused semantic tests and confirm the expected missing-symbol failures.
- [ ] **Step 3: Implement the Ragas 0.3.9 adapter with dependency-injected judge/embedding providers. Freeze model, embedding, temperature, timeout, retry, seed, and max concurrency in one config object. Never alter the config based on scores.
- [ ] **Step 4: Run the focused semantic tests and confirm they pass without requiring live credentials.
- [ ] **Step 5: Add the one-time infrastructure smoke-test path; it may validate API/parser execution but must not consume Development scores to tune prompts.

### Task 5: Implement the Ragas experiment runner and artifacts

**Files:**
- Create: `evaluation/phase10/conversation_e2e_runner.py`
- Create: `scripts/run_phase10_conversation_e2e_ragas.py`
- Create: `tests/evaluation/test_conversation_e2e_runner.py`
- Create: `evaluation/phase10/conversation_e2e_ragas_development_report.json`
- Create: `docs/phase-10-conversation-e2e-ragas-development-report.md`

**Interfaces:**
- Runner entrypoint `run_development_experiment(service, settings, output_json, output_markdown) -> report`.
- Uses Ragas `Experiment`/`@experiment` and writes rows below `evaluation/ragas/experiments/` without overwriting R3-M or historical R2 artifacts.
- A missing staging configuration, unavailable Ragas runtime, or failed semantic execution emits `status: BLOCKED`, preserves dataset fingerprint/reason, and omits fabricated metric sections.

- [ ] **Step 1: Write failing tests** for Ragas row count/order, baseline/candidate query separation, artifact paths, blocked-report no-fabrication, dataset fingerprint, no Validation/Holdout access, and no production Ragas import.
- [ ] **Step 2: Run the focused runner tests and confirm they fail before implementation.
- [ ] **Step 3: Implement the runner.** Load the frozen dataset, build identical query config for both arms, execute all cases, score deterministic and semantic metrics, persist case-level traces and Ragas experiment rows, aggregate means/medians/deltas/largest changes, and evaluate the paired gate.
- [ ] **Step 4: Implement the script using `.env.local_staging` only as existing evaluation scripts do; require the existing Qdrant/LightRAG settings and never silently switch dependency versions or configurations.
- [ ] **Step 5: Run focused runner tests and confirm they pass.

### Task 6: Produce and audit the real Development report

**Files:**
- Modify: `evaluation/phase10/conversation_e2e_ragas_development_report.json`
- Modify: `docs/phase-10-conversation-e2e-ragas-development-report.md`
- Modify: `evaluation/ragas/experiments/<new timestamped experiment>.jsonl`

- [ ] **Step 1: Run the configured experiment script once.** If Ragas/judge/runtime cannot execute, retain a `BLOCKED` report and do not fabricate scores.
- [ ] **Step 2: Audit the JSON report for 18 case rows, both arms, exact dataset fingerprint, judge contract, case-level semantic scores/errors, denominator integrity, gate rationale, failure-layer distribution, and artifact linkage.
- [ ] **Step 3: Render the Markdown report with the required final summary fields, including largest semantic improvements/regressions and next-phase recommendation only (do not start the next phase).
- [ ] **Step 4: Run report/schema tests and `ruff check .`.

### Task 7: Full verification and handoff

**Files:**
- Modify only files already listed above if verification exposes a defect.

- [ ] **Step 1: Run the complete test suite with `C:\Users\12189\.conda\envs\industrial-rag\python.exe -m pytest -q`.
- [ ] **Step 2: Run `C:\Users\12189\.conda\envs\industrial-rag\python.exe -m ruff check .`.
- [ ] **Step 3: Re-read the report and verify every requirement from this plan; if semantic execution is unavailable, report BLOCKED with evidence rather than PASS.
- [ ] **Step 4: Report only the requested final fields and include the actual commit SHA after the user-approved commit/push workflow; do not begin another phase.

---

## Spec Coverage Review

- Dataset freeze, source order, IDs, raw/semantic fingerprints, and Development-only guard: Tasks 1 and 5.
- Fair BASELINE/CANDIDATE LightRAG downstream path and identical runtime configuration: Tasks 1 and 2.
- Deterministic retrieval and industrial answer metrics: Task 3.
- Actual provider-context Faithfulness, shared resolved evaluator input, judge contract, smoke test, and failure retention: Task 4.
- Failure taxonomy, paired gate, artifacts, Ragas experiment rows, blocked semantics, and final report: Tasks 3, 5, and 6.
- Full pytest, lint, production import regression, and no fabricated result check: Task 7.
