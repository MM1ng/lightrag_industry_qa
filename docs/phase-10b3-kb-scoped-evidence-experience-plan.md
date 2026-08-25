# Phase 10B-3 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Move Streamlit to KB-scoped querying and provide bounded, traceable evidence completion with an exact user-facing claim/evidence panel.

**Architecture:** Extend the typed API client and public response models first. Add an immutable per-request evidence registry and deterministic completion in the query service, then split Streamlit into pure adapters/components. Evaluate only development/validation against the frozen Phase 10B-2 retrieval configuration.

**Tech Stack:** FastAPI, Pydantic, SQLAlchemy/LightRAG runtime, Python dataclasses, Streamlit, httpx, pytest, Ruff.

## Global Constraints

- Do not rerun or tune against Holdout.
- Do not modify Golden Set, Chunking, TopK, query normalization, Rerank or Generation.
- Do not introduce LangGraph, feedback backend, Tag, RC packaging or production deployment.
- Ordinary chat uses SERVICE_API_KEY and KB-scoped POST only.
- Evidence excerpts are bounded and never expose scores, vectors, prompts, paths or secrets.

### Task 1: KB-scoped API Client and knowledge-base selection

**Files:** `app/api_client.py`, `app/chat_state.py`, `app/p3_chat.py`, `app/streamlit_app.py`, corresponding tests.

- Add typed `list_knowledge_bases()`, `get_knowledge_base(kb_id)`, and `query_knowledge_base(kb_id, question, history)`.
- Parse KB and Generation identifiers, preserve response status and evidence fields.
- Make Streamlit select a queryable KB, clear conversation on change, reject empty KB lists, and never call `/v1/query`.
- Commit and run client/state tests.

### Task 2: Preserve partial_answer end-to-end

**Files:** `src/industrial_rag/api.py`, `app/api_client.py`, `app/chat_state.py`, `app/p3_chat.py`, UI status components.

- Extend all status literals with `partial_answer` and `safety_blocked` where public parsing requires it.
- Persist KB ID, Generation ID, request ID, claims, citations, evidence, status and latency on AssistantMessage.
- Render distinct complete/partial/insufficient/safety/error states; never coerce partial to success.
- Commit and run API/client/state tests.

### Task 3: Exact Claim → Evidence → Citation mapping

**Files:** `src/industrial_rag/evidence_registry.py`, `src/industrial_rag/api.py`, `src/industrial_rag/answer_grounding.py`, tests.

- Create server-owned GroundedEvidence records with per-request stable IDs.
- Build evidence-to-citation maps for current KB/Generation and map each AnswerPoint only to its own citations.
- Reject unknown IDs, wrong chunks and wrong generations; emit a metric/warning instead of all-citation fallback.
- Commit and run mapping tests.

### Task 4: Bounded Parent/Adjacent/Table/multi-evidence completion

**Files:** `src/industrial_rag/evidence_completion.py`, query/evidence policy modules, retrieval trace models, tests, `evaluation/phase10b3/evidence_completion_policy.json`.

- Reuse existing generation-local chunk metadata when available; otherwise add a read-only registry adapter without scanning PDFs or mutating Qdrant.
- Add same-document/same-generation parent and previous/next bounded completion; add deterministic table-header lookup.
- Enforce total additional evidence ≤2, context budget, de-duplication and explicit completion reasons.
- Record completion candidates, completed evidence, coverage requirements/status and exposed-to-user in Trace.
- Commit and run completion tests.

### Task 5: Evidence panel and Streamlit split

**Files:** `app/pages/chat_page.py`, `app/components/*`, `app/utils/*`, `app/streamlit_app.py`, UI tests.

- Move chat rendering and graph rendering behind page/component functions.
- Add KB selector, status badge, claim-specific citation badges and bounded evidence cards.
- Escape excerpts/highlights; never render score or internal trace fields.
- Commit and run UI adapter/component tests plus a headless Streamlit smoke check.

### Task 6: Development/validation acceptance and report

**Files:** `scripts/run_phase10b3_evaluation.py`, `evaluation/phase10b3/*`, `docs/phase-10b3-kb-scoped-evidence-experience-report.md`.

- Run exactly 36 development and 16 validation cases with the frozen retrieval configuration; do not run Holdout.
- Calculate FRR, Unsupported, citation accuracy, answer-point coverage/support, multi-evidence coverage, partial rate, negative rejection, exact mapping, panel completeness, Trace completeness and p50/p95 latency.
- Run Ruff, pytest, secret scan; record real HEAD, commits, API path proof, UI acceptance, limitations and boolean approval fields.
- Stop after the report; set `phase10c_allowed` according to actual gates.
