# Conversation E2E Semantic Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Harden Phase R3 development-only conversation E2E evaluation so deterministic runtime evidence is frozen and semantic judge retries produce safe, auditable `BLOCKED`, `R3_MIXED`, or `R3_FAIL` results.

**Architecture:** Separate the runtime pass from semantic scoring with a JSONL snapshot containing both arms' actual provider context text. Validate snapshot dataset/runtime parity before reuse; if it is valid, do not create `LightRAGService` calls. Report writing uses an fsync-and-replace helper, and the report builder maps a single aggregate schema into the real gate function.

**Tech Stack:** Python 3, pytest, Ragas 0.3.9, DashScope OpenAI-compatible client, JSONL artifacts.

## Global Constraints

- Development-only frozen dataset; never access Validation or Holdout.
- Keep Ragas `0.3.9`, judge `qwen-plus-2025-07-28`, embedding `text-embedding-v4`, retry `2`, temperature `0.0`.
- Do not change production RAG behavior, query rewriting, retrieval, generation, grounding, or the gold dataset.
- Never replace the judge model after an HTTP 500.
- Preserve `dbaf649e6fd59f710def1e99aa46a93cc514484f` as prior BLOCKED provenance.

---

### Task 1: Define snapshot and safe artifact contracts

**Files:**
- Modify: `evaluation/phase10/conversation_e2e_contracts.py`
- Modify: `evaluation/phase10/conversation_e2e_runner.py`
- Test: `tests/evaluation/test_conversation_e2e_runner.py`

- [ ] Add failing tests for non-empty JSON round-trip and an injected write failure preserving the canonical artifact.
- [ ] Add `atomic_write_text(path, text)` that writes a sibling temporary file, fsyncs it, then uses `Path.replace` only after close.
- [ ] Ensure every BLOCKED report contains status, reason code/reason, fingerprints, case count, judge config, Ragas version, semantic execution, judge errors, and timestamp.
- [ ] Run `pytest tests/evaluation/test_conversation_e2e_runner.py -q`.

### Task 2: Freeze and validate runtime snapshots

**Files:**
- Modify: `evaluation/phase10/conversation_e2e_contracts.py`
- Modify: `evaluation/phase10/conversation_e2e_runner.py`
- Test: `tests/evaluation/test_conversation_e2e_runner.py`

- [ ] Add failing tests for 18-case snapshot completeness, SHA-256 validation, persisted provider context text, invalid parity, and resume with zero service calls.
- [ ] Implement snapshot serialization/deserialization with one record per case plus immutable manifest fields: ordered case IDs, dataset/runtime fingerprints, case count, and SHA-256.
- [ ] Make snapshot validation reject missing provider contexts, count/order mismatch, checksum mismatch, and fingerprint mismatch with a BLOCKED reason.
- [ ] Run the snapshot test subset.

### Task 3: Implement isolated provider and Ragas preflights

**Files:**
- Modify: `evaluation/phase10/conversation_e2e_semantic.py`
- Test: `tests/evaluation/test_conversation_e2e_semantic.py`

- [ ] Add failing tests for direct chat, direct embedding, isolated Faithfulness, isolated ResponseRelevancy, bounded 5xx retry evidence, and error attribution.
- [ ] Implement four independently reported preflights with immutable client/model configuration and attempt/request-id/http-status audit records.
- [ ] Keep semantic scoring on actual snapshot `provider_contexts` and the same frozen `standalone_query` for both arms.
- [ ] Run `pytest tests/evaluation/test_conversation_e2e_semantic.py -q`.

### Task 4: Normalize gate inputs and status policy

**Files:**
- Modify: `evaluation/phase10/conversation_e2e_metrics.py`
- Modify: `evaluation/phase10/conversation_e2e_runner.py`
- Test: `tests/evaluation/test_conversation_e2e_metrics.py`
- Test: `tests/evaluation/test_conversation_e2e_runner.py`

- [ ] Add failing tests that pass runtime optional-metric objects into `evaluate_gate` and cover unavailable metrics, R3_MIXED, severe semantic regression, and semantic BLOCKED.
- [ ] Adapt `evaluate_gate` to the real `{status, value, denominator}` metric schema and prohibit R3_PASS while mandatory downstream metrics are unavailable.
- [ ] Call `evaluate_gate` from `build_report`, include its reasons, and preserve paired semantic deltas and largest changes.
- [ ] Run the metrics and runner tests.

### Task 5: Make the command resume from snapshot and produce final artifacts

**Files:**
- Modify: `scripts/run_phase10_conversation_e2e_ragas.py`
- Modify: `docs/phase-10-conversation-e2e-ragas-development-report.md`
- Create: `evaluation/phase10/conversation_e2e_runtime_snapshot_development.jsonl`
- Modify: `evaluation/phase10/conversation_e2e_ragas_development_report.json`
- Test: `tests/evaluation/test_conversation_e2e_script.py`

- [ ] Add failing tests that a valid snapshot avoids service creation/calls and invalid snapshot emits a valid BLOCKED report.
- [ ] Run runtime E2E only if a trustworthy snapshot is absent; otherwise score only from the frozen snapshot.
- [ ] Run four-layer preflight once; on block write atomic JSON/Markdown without re-running RAG.
- [ ] Record prior blocked commit, snapshot SHA/parity, preflight outcomes, and real deterministic evidence in final artifacts.
- [ ] Run the command against Development only and inspect artifacts.

### Task 6: Verify and deliver

**Files:**
- Test: `tests/evaluation/test_conversation_e2e_*.py`

- [ ] Run targeted tests and `ruff check` over every changed source/test/script file.
- [ ] Run `pytest -q`.
- [ ] Confirm no Validation/Holdout paths were accessed and only intended files are staged.
- [ ] Commit `eval: harden conversation e2e semantic recovery` and push the current branch.
