# Phase 11 Online Feedback and Evaluation Sample Feedback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a small, demo-oriented feedback loop that persists trusted answer snapshots, accepts idempotent helpful/unhelpful feedback, lets an administrator filter/export samples, and supports offline Development/Error Set export without changing the frozen evaluation sets.

**Architecture:** Persist one server-created answer snapshot per `request_id` in a new `answer_feedback` table. The KB query path writes the snapshot best-effort after constructing the trusted response; the feedback endpoint only accepts `request_id` and feedback fields, then updates the existing snapshot. A small admin router provides paginated filters, quality-only aggregate metrics, review fields, and JSON/CSV export. The existing Streamlit chat state keeps the response identifiers and adds feedback controls beneath each assistant message.

**Tech Stack:** FastAPI, Pydantic, SQLAlchemy async sessions, Alembic, SQLite-compatible JSON columns, existing Streamlit client/state, pytest/httpx.

## Global Constraints

- This phase is only an answer-quality feedback loop for a demo/evaluation/resume project, not an operations monitoring, observability, rollout, or A/B platform.
- Persist answer snapshots only for business-answer outcomes: `answered`, `insufficient_evidence`, and `refused`. Validation failures, authentication failures, and internal/upstream errors without a business answer are excluded.
- Use exactly these negative-feedback reasons: `answer_incorrect`, `citation_unsupported`, `answer_incomplete`, `answer_not_found`, `false_refusal`, `unsafe_or_unnecessary_answer`, `response_too_slow`, `other`.
- Persist only bounded evaluation summaries in `retrieved_chunks`: `chunk_id`, `document_name`, `page`, `initial_rank`, `reranked_rank`, `score`, and `content_excerpt`; never persist a full Retrieval Trace, full parent chunk, or full document.
- Long-term feedback metrics must use the answer snapshot table. Retrieval Trace is consulted once during answer creation to extract the bounded retrieval summary and is not a metrics data source.
- Snapshot persistence is best-effort and must not change, re-run, or block the core answer, citation, refusal, or retrieval path.
- Do not add an administrator visualization page. Provide administrator query, review, metrics, and JSON/CSV APIs only; Streamlit only receives answer-level feedback controls.
- Keep one row per `request_id`; repeated feedback overwrites the existing feedback fields. Do not add feedback history, review workflow, assignment, or audit infrastructure.
- Do not add login, user accounts, complex permissions, dashboards, alerts, SLOs, CPU/memory/disk metrics, gray release, auto activation, rollback, LangGraph, multi-hop retrieval, or retrieval algorithm changes.
- Do not trust client-supplied answer, citations, `generation_id`, or `knowledge_base_id`; resolve all snapshot data by `request_id`.
- Duplicate feedback for one answer must update the existing row; `request_id` is unique.
- Helpful feedback may omit a reason; unhelpful feedback requires one of the fixed reasons.
- Existing golden, validation, and holdout sets remain unchanged; exported samples can only enter a new Development/Error Set.
- Recall@K and MRR@K remain offline metrics; citation presence is not citation support, and refusal rate is not appropriate-refusal rate.
- Preserve all existing dirty/untracked user files and do not commit, deploy, tag, or activate a Generation.

---

### Task 1: Add answer-feedback model and migration

**Files:**
- Modify: `src/industrial_rag/db/models.py`
- Create: `migrations/versions/<new_revision>_phase11_answer_feedback.py`
- Test: `tests/test_phase11_feedback_migration.py`

**Interfaces:**
- Produces model `AnswerFeedbackRecord` with one row per eligible business answer and a unique `request_id`.
- Stores `id`, request/trace/generation/KB identifiers, question, answer, normalized answer status, feedback fields, citations JSON, bounded retrieved chunk summaries JSON, timestamps, and nullable manual review fields.

- [ ] **Step 1: Write failing model and migration tests**

  Assert the model has all required columns, `id` is the primary key, `request_id` is unique/indexed, and Alembic upgrade/downgrade creates/removes only the new table and indexes.

- [ ] **Step 2: Run the focused tests and verify they fail because the table is absent**

  Run: `C:\Users\12189\.conda\envs\industrial-rag\python.exe -m pytest tests/test_phase11_feedback_migration.py -q`

- [ ] **Step 3: Add the minimal SQLAlchemy model and Alembic migration**

  Use `Text` for question/answer/comment, `JSON` for citations/retrieved chunks, nullable `String` identifiers for legacy compatibility, and string review fields constrained by service validation rather than introducing a new workflow enum.

- [ ] **Step 4: Run the focused migration tests and verify they pass**

  Run: `C:\Users\12189\.conda\envs\industrial-rag\python.exe -m pytest tests/test_phase11_feedback_migration.py -q`

---

### Task 2: Persist trusted answer snapshots and implement feedback service

**Files:**
- Create: `src/industrial_rag/repositories/answer_feedback_repository.py`
- Create: `src/industrial_rag/services/answer_feedback_service.py`
- Modify: `src/industrial_rag/api.py`
- Test: `tests/test_phase11_feedback_service.py`

**Interfaces:**
- `AnswerFeedbackRepository.create_or_get_by_request_id(...) -> AnswerFeedbackRecord`
- `AnswerFeedbackRepository.update_feedback_by_request_id(...) -> AnswerFeedbackRecord | None`
- `AnswerFeedbackRepository.list_filtered(...) -> tuple[list[AnswerFeedbackRecord], int]`
- `AnswerFeedbackService.record_answer_best_effort(...) -> None`
- `AnswerFeedbackService.submit_feedback(...) -> AnswerFeedbackRecord`
- `AnswerFeedbackService.metrics(...) -> dict[str, object]`

- [ ] **Step 1: Write failing service tests**

  Cover trusted snapshot creation, feedback update idempotency, missing request ID, rejection of client-owned snapshot fields, required reason for unhelpful feedback, and preservation of citations/retrieved chunk summaries.

- [ ] **Step 2: Run focused service tests and verify the expected failures**

  Run: `C:\Users\12189\.conda\envs\industrial-rag\python.exe -m pytest tests/test_phase11_feedback_service.py -q`

- [ ] **Step 3: Implement repository and service validation**

  Normalize feedback reasons to the fixed allow-list, cap comments and serialized summaries, use an update on repeated `request_id`, and never copy answer/IDs/citations from the feedback request.

- [ ] **Step 4: Add non-blocking best-effort snapshot persistence to the authoritative query paths**

  After the query route constructs a business-answer response, schedule a background best-effort write using the already-produced execution/result objects. Map `success` and `partial_answer` to `answered`, `insufficient_evidence` to `insufficient_evidence`, and safety refusal to `refused`; skip validation/auth/internal/upstream errors. Extract only bounded chunk summaries from the in-memory execution Trace; do not call the retrieval API, do not query again, and do not await snapshot persistence on the answer path.

- [ ] **Step 5: Run focused tests and verify they pass**

  Run: `C:\Users\12189\.conda\envs\industrial-rag\python.exe -m pytest tests/test_phase11_feedback_service.py -q`

---

### Task 3: Add feedback and administrator sample APIs

**Files:**
- Create: `src/industrial_rag/routers/feedback.py`
- Modify: `src/industrial_rag/routers/__init__.py`
- Modify: `src/industrial_rag/api.py`
- Modify: `src/industrial_rag/errors.py`
- Test: `tests/test_phase11_feedback_api.py`

**Interfaces:**
- `POST /v1/feedback` accepts `{request_id, feedback_type, feedback_reason?, feedback_comment?}` and recognizes only the fixed negative-reason values.
- `GET /v1/admin/feedback` supports pagination, feedback type/reason, KB, Generation, answer status, and ISO time-range filters.
- `GET /v1/admin/feedback/export` returns a small JSON or CSV Development/Error Set export for selected/filtered samples.

- [ ] **Step 1: Write failing API tests**

  Cover helpful success, unhelpful-without-reason validation, unknown request ID, client field forgery, duplicate update without duplicate rows, admin negative-feedback filtering, metrics denominator semantics, and JSON/CSV export fields.

- [ ] **Step 2: Run focused API tests and verify they fail**

  Run: `C:\Users\12189\.conda\envs\industrial-rag\python.exe -m pytest tests/test_phase11_feedback_api.py -q`

- [ ] **Step 3: Implement Pydantic request/response schemas and router**

  Keep feedback submission service-authenticated using the existing bearer middleware. Protect list/export/review updates with `require_admin_actor`. Return clear 2xx/4xx responses and never include credentials or request headers.

- [ ] **Step 4: Implement quality-only metrics from persisted answer snapshots**

  Return `feedback_coverage_count`, `feedback_coverage_rate`, `negative_feedback_count`, `negative_feedback_rate_among_feedback`, `negative_feedback_rate_among_eligible_answers`, `citation_presence_rate`, `empty_evidence_answer_rate`, and `refusal_rate` from the snapshot table only, with explicit numerator/denominator/value objects and null for empty denominators. Do not use the 24-hour Retrieval Trace table for these metrics.

- [ ] **Step 5: Run focused API tests and verify they pass**

  Run: `C:\Users\12189\.conda\envs\industrial-rag\python.exe -m pytest tests/test_phase11_feedback_api.py -q`

---

### Task 4: Add minimal Streamlit feedback controls and client method

**Files:**
- Modify: `app/api_client.py`
- Modify: `app/streamlit_app.py`
- Test: `tests/test_phase11_api_client.py`
- Test: `tests/test_phase11_feedback_ui.py`

**Interfaces:**
- `KnowledgeApiClient.submit_feedback(request_id, feedback_type, feedback_reason=None, feedback_comment=None) -> None`.
- Existing `AssistantMessage` identifiers remain the source for `request_id`, `trace_id`, `generation_id`, and KB ID display/binding; no new user/account state.

- [ ] **Step 1: Write failing client/UI behavior tests**

  Assert the client sends only the four feedback request fields, maps API failures to `ApiError`, and the renderer exposes exactly helpful/unhelpful controls for answer messages with a request ID.

- [ ] **Step 2: Run focused client/UI tests and verify they fail**

  Run: `C:\Users\12189\.conda\envs\industrial-rag\python.exe -m pytest tests/test_phase11_api_client.py tests/test_phase11_feedback_ui.py -q`

- [ ] **Step 3: Implement the client method and local Streamlit interaction**

  Add two buttons below citations/evidence. On unhelpful, show a selectbox/radio for the fixed reasons and a short comment box for `其他`; submit through the existing client, store only a per-message submitted marker in Streamlit session state, and show a non-blocking success/error message.

- [ ] **Step 4: Run focused client/UI tests and verify they pass**

  Run: `C:\Users\12189\.conda\envs\industrial-rag\python.exe -m pytest tests/test_phase11_api_client.py tests/test_phase11_feedback_ui.py -q`

---

### Task 5: Add review fields, offline export contract, and regression coverage

**Files:**
- Modify: `src/industrial_rag/routers/feedback.py`
- Modify: `src/industrial_rag/services/answer_feedback_service.py`
- Test: `tests/test_phase11_feedback_review_and_export.py`
- Create: `docs/phase-11-online-feedback-report.md`

**Interfaces:**
- `PATCH /v1/admin/feedback/{id}/review` accepts only `true|false|unknown|not_applicable` review values and the fixed root-cause list.
- Export rows contain `question`, `answer`, `knowledge_base_id`, `retrieved_chunks`, `citations`, `feedback_reason`, and `review_result`.

- [ ] **Step 1: Write failing review/export tests**

  Assert invalid review values are rejected, valid review fields round-trip, and exported samples do not mutate any golden/validation/holdout file.

- [ ] **Step 2: Run focused tests and verify they fail**

  Run: `C:\Users\12189\.conda\envs\industrial-rag\python.exe -m pytest tests/test_phase11_feedback_review_and_export.py -q`

- [ ] **Step 3: Implement the small admin review update and export**

  Keep review as direct record editing; do not add assignment, workflow, audit, visualization, or dashboard infrastructure. Export only bounded `retrieved_chunks` summaries and the requested `review_result` fields.

- [ ] **Step 4: Run the full relevant regression suite**

  Run: `C:\Users\12189\.conda\envs\industrial-rag\python.exe -m pytest tests/test_api.py tests/test_api_client.py tests/test_p3_chat.py tests/test_phase10a_migration.py tests/test_phase11_feedback_migration.py tests/test_phase11_feedback_service.py tests/test_phase11_feedback_api.py tests/test_phase11_api_client.py tests/test_phase11_feedback_ui.py tests/test_phase11_feedback_review_and_export.py -q`

- [ ] **Step 5: Write the Phase 11 implementation report**

  Document audit findings, changed files, migration, request examples, admin examples, formulas and denominators, test output, manual verification, and known limitations. State explicitly that online feedback finds errors and supplements a new Development/Error Set; it does not replace frozen offline evaluation.

## Self-review checklist

- [ ] No server/CPU/memory/disk metrics, alerts, SLOs, gray release, A/B platform, or automatic Generation activation were added.
- [ ] No client-provided answer, citation, KB, or Generation metadata is trusted.
- [ ] A second feedback submission updates the same `request_id` row.
- [ ] Empty denominators are represented explicitly rather than silently as zero.
- [ ] Existing query, citation, migration, and UI tests remain green.
- [ ] The frozen golden, validation, and holdout data are untouched.
