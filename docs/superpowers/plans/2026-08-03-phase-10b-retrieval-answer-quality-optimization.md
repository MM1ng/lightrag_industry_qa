# Phase 10B Retrieval and Answer Quality Optimization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Each task ends with an independent test and commit.

**Goal:** Use controlled development/validation experiments to improve retrieval, evidence selection, refusal, and citation quality while preserving the frozen Phase 10A dataset and holdout boundary.

**Architecture:** Keep the ordinary query API and Active Generation unchanged until an experiment is explicitly selected. Build analysis and experiment runners around immutable Phase 10A JSONL/Trace artifacts, with per-experiment manifests and isolated staging configuration. Holdout data is loaded only by the final frozen evaluator after all configuration decisions are committed.

**Tech Stack:** Python 3.11, FastAPI, Pydantic, SQLAlchemy, LightRAG, httpx, pytest, Ruff, JSONL/JSON manifests, PowerShell staging scripts.

## Global Constraints

- Development contains 36 questions and validation contains 16 questions; Task 1 analyzes exactly these 52 questions.
- Holdout contains 12 questions and must not be read at question level until the final configuration is frozen.
- Keep the golden set, its SHA-256, expected evidence, expected answer points, and metric denominator policy unchanged.
- Before Task 1 completes, do not modify Chunking, TopK, retrieval mode, retrieval weights, Rerank, Prompt, evidence selection, or refusal strategy.
- Do not infer Dense/Keyword/Graph provenance from `lightrag_mix_unspecified`.
- Do not expose internal strategy switches or scores in the ordinary user API.
- Every experiment records experiment ID, parent ID, Git commit, dataset SHA, Generation, cache state, config, model config, runtime, dev/validation metrics, latency, and failure changes.
- Do not modify Streamlit, develop feedback backend, introduce LangGraph, create Tag, package RC, or deploy production.

---

### Task 1: Development + Validation Failure Matrix

**Files:**
- Create: `src/industrial_rag/phase10b_failure_analysis.py`
- Create: `scripts/build_phase10b_failure_matrix.py`
- Create: `tests/test_phase10b_failure_matrix.py`
- Create: `evaluation/phase10/phase10b_failure_matrix.jsonl`
- Create: `evaluation/phase10/phase10b_failure_summary.json`

**Interfaces:**
- `build_failure_matrix(results: Iterable[dict[str, Any]], diagnoses: Iterable[dict[str, Any]]) -> list[dict[str, Any]]`
- `summarize_failure_matrix(rows: Iterable[dict[str, Any]]) -> dict[str, Any]`
- `classify_failure(case: dict[str, Any]) -> FailureClassification`
- The script reads only `baseline_results.jsonl` and `baseline_diagnosis.jsonl` rows whose golden split is `development` or `validation`.

- [ ] **Step 1: Write failing tests for split isolation and row schema.**

  Test that a synthetic holdout row is ignored, that a development positive with an expected Chunk absent from initial results is classified as `Retrieval/chunk_not_recalled`, and that a recalled but unselected evidence is classified as `Evidence Selection/evidence_not_selected`.

- [ ] **Step 2: Run the focused tests and confirm failure.**

  Run: `python -m pytest tests/test_phase10b_failure_matrix.py -q`

  Expected: import or assertion failures because the classifier and builder do not exist.

- [ ] **Step 3: Implement deterministic classification.**

  Derive initial ranks from `trace.initial_results`, selected evidence from `trace.final_selected_chunks`, and citations from the ordinary response. Determine failure layers in this order: Retrieval (wrong document/page/chunk/query term/metadata), Ranking (rank too low), Evidence Selection (not selected/threshold/cross-page incompleteness), Generation (answer-point extraction), Refusal (status or unsupported evidence), Citation (wrong page/chunk, incomplete binding). Never use a generic model-error category. Preserve full expected evidence and answer points in each output row.

- [ ] **Step 4: Implement grouped summaries.**

  Group counts and rates by `question_type`, `difficulty`, `document`, `split`, and `failure_layer`; include all required categories with zero counts when absent. Store numerator, denominator, and value for every rate. Record dataset SHA, source artifact names, source commit, analyzed question count, and an explicit `holdout_rows_loaded=false` field.

- [ ] **Step 5: Build the real 52-question matrix.**

  Run: `python scripts/build_phase10b_failure_matrix.py`

  Verify output has exactly 52 rows, split counts 36/16, no holdout IDs, and both JSONL/JSON files are atomically written.

- [ ] **Step 6: Run focused tests and Ruff.**

  Run: `python -m pytest tests/test_phase10b_failure_matrix.py -q` and `python -m ruff check src/industrial_rag/phase10b_failure_analysis.py scripts/build_phase10b_failure_matrix.py tests/test_phase10b_failure_matrix.py`.

- [ ] **Step 7: Commit Task 1.**

  `git add src/industrial_rag/phase10b_failure_analysis.py scripts/build_phase10b_failure_matrix.py tests/test_phase10b_failure_matrix.py evaluation/phase10/phase10b_failure_matrix.jsonl evaluation/phase10/phase10b_failure_summary.json && git commit -m "feat(phase10b): classify development validation failures"`

### Task 2: Deterministic Query Normalization Experiment

**Files:**
- Create: `src/industrial_rag/query_normalization.py`
- Create: `scripts/run_phase10b_normalization_experiment.py`
- Create: `tests/test_phase10b_query_normalization.py`
- Create: `evaluation/phase10/query_normalization_results.json`

- [ ] **Step 1:** Add red tests for full-width/half-width text, case and whitespace, model/component/parameter aliases, unit and temperature forms, and operation synonyms; assert no LLM call.
- [ ] **Step 2:** Implement a pure `normalize_query()` returning original query, normalized query, detected model/component/parameter, and added aliases.
- [ ] **Step 3:** Run baseline and normalization-only on development, then validation; keep all retrieval/generation settings and cache state fixed.
- [ ] **Step 4:** Save experiment manifest and dev/validation metrics; reject the experiment if citation trace completeness or negative rejection changes unexpectedly.
- [ ] **Step 5:** Run tests/Ruff and commit `feat(phase10b): add deterministic query normalization experiment`.

### Task 3: Isolated Retrieval Ablation

**Files:**
- Create: `src/industrial_rag/phase10b_experiment_manifest.py`
- Create: `scripts/run_phase10b_retrieval_ablation.py`
- Create: `tests/test_phase10b_retrieval_ablation.py`
- Create: `evaluation/phase10/retrieval_ablation_results.json`

- [ ] **Step 1:** Write manifest tests requiring one explicit changed variable, isolated instance/config, dataset SHA, Generation, model and cache declarations.
- [ ] **Step 2:** Implement controlled configurations for mix baseline, dense, keyword, hybrid, top_k, chunk_top_k, metadata filter, and Parent Context without ordinary API query switches.
- [ ] **Step 3:** Run each configuration on development only, compare against the Phase 10A baseline, and preserve actual `lightrag_mix_unspecified` source semantics.
- [ ] **Step 4:** Use validation only to select the best configuration; do not inspect holdout rows.
- [ ] **Step 5:** Run tests/Ruff and commit `feat(phase10b): add isolated retrieval ablations`.

### Task 4: Rerank Comparison

**Files:**
- Modify: existing retrieval/query service trace integration files only after Task 3 selection
- Create: `scripts/run_phase10b_rerank_experiments.py`
- Create: `tests/test_phase10b_rerank_experiments.py`
- Create: `evaluation/phase10/rerank_results.json`

- [ ] **Step 1:** Add tests for disabled Rerank false/empty/null behavior, real score preservation, explicit failure state, and no silent success fallback.
- [ ] **Step 2:** Implement one light Rerank and one effect-priority Rerank only if the installed environment supports them; keep candidate count fixed.
- [ ] **Step 3:** Run development comparisons, select on validation with accuracy and p50/p95 latency, and persist before/after rank and score fields.
- [ ] **Step 4:** Run tests/Ruff and commit `feat(phase10b): compare controlled rerank strategies`.

### Task 5: Evidence Selection and Refusal Calibration

**Files:**
- Create: `src/industrial_rag/phase10b_refusal_analysis.py`
- Create: `scripts/run_phase10b_refusal_calibration.py`
- Create: `tests/test_phase10b_refusal_calibration.py`
- Create: `evaluation/phase10/evidence_selection_results.json`
- Create: `evaluation/phase10/refusal_calibration_results.json`

- [ ] **Step 1:** Add tests covering no evidence, incomplete evidence, partial answer, conflicting evidence, out-of-scope, and safety-blocked cases.
- [ ] **Step 2:** Implement explainable states `success`, `partial_answer`, `insufficient_evidence`, and `safety_blocked` using evidence count, coverage, page consistency, and answer-point support; do not lower a global threshold.
- [ ] **Step 3:** Analyze the 14 baseline False Rejection cases only after retrieval/Rerank configuration is frozen; development tunes and validation selects.
- [ ] **Step 4:** Verify Negative Rejection Rate remains 100%, Unsupported Answer Rate does not worsen, and fabricated citation count is zero.
- [ ] **Step 5:** Run tests/Ruff and commit `feat(phase10b): calibrate evidence selection and refusal states`.

### Task 6: Citation Binding Evaluation

**Files:**
- Create: `src/industrial_rag/phase10b_citation_binding.py`
- Create: `scripts/run_phase10b_citation_binding.py`
- Create: `tests/test_phase10b_citation_binding.py`
- Create: `evaluation/phase10/citation_binding_results.json`

- [ ] **Step 1:** Add tests for wrong document/page/Chunk/Generation, answer-point coverage, safety-versus-operation citations, and incomplete multi-evidence answers.
- [ ] **Step 2:** Implement deterministic checks against frozen expected evidence and answer points; leave claim-level accuracy unavailable when automatic claims cannot be trusted.
- [ ] **Step 3:** Run on development and validation with the frozen retrieval/Rerank/refusal configuration.
- [ ] **Step 4:** Run tests/Ruff and commit `feat(phase10b): evaluate citation binding quality`.

### Task 7: Candidate Chunking Only If Matrix Justifies It, Then Final Holdout

**Files:**
- Create only if justified: `scripts/run_phase10b_chunking_candidates.py`, `tests/test_phase10b_chunking_candidates.py`, `evaluation/phase10/chunking_results.json`, and scheme-specific golden mappings.
- Create after all configuration decisions: `scripts/freeze_phase10b_final_config.py`, `evaluation/phase10/final_config_manifest.json`, `evaluation/phase10/holdout_results.jsonl`, `evaluation/phase10/final_metrics.json`, `docs/phase-10b-retrieval-answer-quality-report.md`.

- [ ] **Step 1:** Review the Task 1 matrix and document a written decision whether Candidate Chunking is necessary; do not run it when Top20 retrieval and evidence mapping are not the dominant bottleneck.
- [ ] **Step 2:** If necessary, run each scheme in an isolated Candidate Generation and map frozen evidence by document/page/text/hash, never by old Chunk ID.
- [ ] **Step 3:** Freeze normalization, retrieval, TopK, weights, Rerank, evidence selection, refusal, citation binding, chunking, code commit, model, Generation, and evaluator SHA in `final_config_manifest.json`.
- [ ] **Step 4:** Run holdout exactly once after the manifest is frozen; do not use results to tune or rerun.
- [ ] **Step 5:** Run full pytest/Ruff and Secret scan, write the final report with all target gates and remaining failures, commit, and stop before Phase 10C.
