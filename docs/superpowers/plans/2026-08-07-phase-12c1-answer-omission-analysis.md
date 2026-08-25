# Phase 12C-1 Answer Generation Omission Analysis Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Re-audit the six Phase 12A `generation_omitted` samples against the original Development answers and exact Runtime evidence, then decide Gate A without modifying Prompt or Generation.

**Architecture:** Use the Phase 12A canonical `evaluation/phase10b3i` Development artifacts for question, raw answer, final answer, trace, and funnel facts. Use the existing R1 hydrated rows only to verify exact Runtime chunk text. A small read-only audit script applies an explicit semantic answer-point checklist for the six questions and records reclassification evidence.

**Tech Stack:** Python, JSON/JSONL, pytest, existing Development-only artifacts.

## Global Constraints

- Do not read Validation or Holdout data.
- Do not re-run Retrieval, Generation, Citation, Grounding, or any model call.
- Keep Retrieval, TopK, Chunking, Context, Citation, Grounding, Refusal, Rerank, model, and sampling unchanged.
- Do not modify Prompt or production business code.
- Do not execute Experiment A unless Gate A confirms a generation root cause.

---

### Task 1: Add a read-only six-case audit

**Files:**
- Create: `scripts/phase12c1_answer_omission_analysis.py`
- Create: `evaluation/phase12c1/omission_audit.jsonl`
- Create: `evaluation/phase12c1/omission_summary.json`
- Test: `tests/test_phase12c1_answer_omission_analysis.py`

- [ ] Write tests for semantic point coverage, exact hydrated evidence presence, no grounding deletion of the audited points, and D015 knowledge-gap handling.
- [ ] Run the tests and observe failure because the audit module does not exist.
- [ ] Implement the deterministic audit using only the six fixed question IDs and explicit answer-point rules.
- [ ] Record original Phase 12A classification, corrected classification, evidence lengths, context/truncation fields, raw answer, final answer, and coverage before.
- [ ] Run the audit tests and verify they pass.

### Task 2: Generate the Gate A report

**Files:**
- Create: `docs/phase-12c1-answer-generation-omission-report.md`

- [ ] Report all six cases, subtype distribution, original-vs-corrected coverage, Prompt behavior observations, and missing context-budget evidence.
- [ ] State whether any case remains a confirmed generation omission.
- [ ] If Gate A fails, set status `ROOT_CAUSE_RECLASSIFIED`, do not design or execute Experiment A, and stop.

### Task 3: Validate scope and regression safety

- [ ] Validate all JSON/JSONL artifacts.
- [ ] Run the Phase 12C-1 audit tests and the full pytest suite.
- [ ] Confirm no online code, Prompt, Citation, Retrieval, Generation, or frozen dataset changed.
