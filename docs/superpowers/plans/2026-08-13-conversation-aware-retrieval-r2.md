# Conversation-Aware Retrieval R2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a Development-only, provenance-backed Before/After retrieval evaluation proving whether safe conversation rewrite changes initial chunk retrieval on real Development Gold cases.

**Architecture:** Add a frozen conversation retrieval dataset derived only from evaluation/phase10/expanded_golden_set.jsonl Development positive rows, then add a retrieval-only evaluator that executes the existing normalize_query and backend.aquery_data path once per baseline/candidate query under one explicit runtime fingerprint. The evaluator will validate rewrite output before retrieval, calculate hit Recall@5/@10, evidence Recall@5/@10, and MRR@5/@10, and emit case-level regressions without changing production retrieval or QueryRewriter behavior.

**Tech Stack:** Python 3.11, existing QueryRewriter, normalize_query, LightRAGBackend.aquery_data, QueryOptions, JSONL evaluation artifacts, pytest.

## Global Constraints

- Use only split == development and answerable == true rows from evaluation/phase10/expanded_golden_set.jsonl.
- Allowed source IDs are exactly S001-S020 and D001-D016; any other source ID or split fails immediately.
- Never modify query_rewrite_development.jsonl, the frozen Development Gold, Validation, Holdout, index, or ingest data.
- BEFORE uses dependent_query directly; AFTER uses history + dependent_query -> existing QueryRewriter -> normalized retrieval.
- BEFORE and AFTER use the same KB/generation/workspace/vector backend/embedding/query mode/top_k/chunk_top_k/rerank configuration.
- Use only retrieval results from the existing backend.aquery_data path; do not evaluate answer generation or alter production QueryRewriter logic unless a real case exposes a regression.
- Gold chunk IDs are copied directly from source row expected_evidence.chunk_id.
- Do not cache one side's retrieval result or perform a second diagnostic retrieval.

---

### Task 1: Freeze the real conversation Development dataset

Files:
- Create data/evaluation/conversation_retrieval_development.jsonl
- Create tests/test_conversation_retrieval_dataset.py

Interfaces:
- Dataset rows contain case_id, source_question_id, history, dependent_query, expected_standalone_query, gold_chunk_ids, and category.
- Dataset loader returns validated rows and refuses non-Development/unknown source IDs.

Steps:
- Write failing dataset contract tests for exact allowed IDs, required fields, direct gold inheritance, category coverage, and no Validation/Holdout rows.
- Run the focused test and observe the missing-file/import failure.
- Add 16 natural cases derived from allowed S001-S020 and D001-D016, using only exact evidence chunk IDs. Cover Pronoun Resolution, Ellipsis, Property/Constraint Inheritance, and Topic Continuation.
- Run dataset tests and verify provenance and Development-only checks.

### Task 2: Add retrieval-only metric helpers

Files:
- Create src/industrial_rag/evaluation/conversation_retrieval.py
- Create tests/test_conversation_retrieval_metrics.py

Interfaces:
- ranked_chunk_ids(initial_results) -> tuple[str, ...]
- compute_retrieval_metrics(ranked_ids, gold_ids, ks=(5, 10)) -> dict
- compare_retrieval_metrics(before, after, gold_ids) -> dict

Definitions:
- Hit Recall@K is 1 if any gold chunk is in top K.
- Evidence Recall@K is retrieved unique gold count divided by total gold count.
- MRR@K is reciprocal rank of the first gold chunk within K, otherwise 0.
- Aggregate by mean over positive cases; empty case sets return None.
- Regressions include case ID, source ID, dependent query, rewritten query, gold IDs, before ranks, and after ranks.

Steps:
- Write failing tests for single/multi-gold metrics, empty gold rejection, duplicate results, deltas, and regression details.
- Run focused tests and observe missing-module failure.
- Implement pure helpers with no retrieval side effects.
- Run metric tests and verify pass.

### Task 3: Implement the fair Development-only evaluator

Files:
- Create scripts/evaluate_conversation_retrieval_development.py
- Create tests/test_conversation_retrieval_evaluator.py

Interfaces:
- load_conversation_cases(path=DATASET_PATH) -> list
- validate_development_cases(cases, source_gold_path=SOURCE_GOLD_PATH) -> None
- async evaluate_backend(backend, cases, config, fingerprint) -> dict

Required behavior:
- Load and validate the source Development Gold and conversation dataset.
- Assert source split, answerable flag, allowed IDs, and direct expected chunk provenance.
- Run exactly two backend.aquery_data calls per case: normalized dependent query for BEFORE and normalized rewritten standalone query for AFTER.
- Use the same QueryOptions(mode, top_k, chunk_top_k, enable_rerank) for both.
- Record initial ranks, normalized input queries, rewrite status/output, and immutable evaluation fingerprint.
- Fail if rewrite is not rewritten or normalized standalone differs from normalized expected gold; do not alter expected gold.
- Never invoke generation, index mutation, or production runtime query.

Steps:
- Write failing tests with a recording fake backend for exact call count, equal QueryOptions, normalized inputs, rewrite mismatch, and provenance leakage.
- Run evaluator tests and observe missing module/API failures.
- Implement evaluator and JSON report schema.
- Run evaluator tests and verify pass.

### Task 4: Add report artifact and documentation

Files:
- Create evaluation/phase10/conversation_retrieval_development_report.json
- Create docs/phase-10-conversation-retrieval-development-report.md
- Modify README.md only if the command needs a user-facing invocation note.
- Create tests/test_conversation_retrieval_report.py

Report must include dataset count/source IDs/category distribution/Development guard; rewrite accuracy/failed/ambiguous/unnecessary rewrite; BEFORE and AFTER metrics; deltas; improved/unchanged/regressed cases; fingerprint; and test results.

Steps:
- Write failing report schema tests.
- Generate the report only if the configured Development KB/Generation is available; otherwise produce explicit BLOCKED output with no fabricated retrieval metrics.
- Record actual retrieval results or a precise blocker, never invented values.
- Document command, definitions, provenance, and result.

### Task 5: Verify, review, commit, and push

Steps:
- Run focused dataset/metric/evaluator/report tests.
- Run the full project test suite using industrial-rag.
- Run Ruff on changed Python files and git diff --check.
- Verify no frozen Gold, Validation, Holdout, index, or user patch file is staged.
- Commit with feat: add development conversation retrieval evaluation.
- Push codex/knowledge-qa-platform-design to origin.
