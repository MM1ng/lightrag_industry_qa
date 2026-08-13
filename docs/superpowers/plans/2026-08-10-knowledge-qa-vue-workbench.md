# Knowledge QA Vue Workbench Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build one Vue 3 frontend for ordinary knowledge QA and protected administration while preserving the existing FastAPI query and evidence contracts.

**Architecture:** Add a standalone `frontend/` Vue 3 + Vite + TypeScript SPA. The SPA owns navigation, chat state, evidence drawer, graph presentation, and admin views; FastAPI remains the source of truth for query, citation, feedback, knowledge-base, document, job, and Generation operations. Add only read-only graph projection endpoints needed by the SPA, and keep Streamlit available as a fallback during migration.

**Tech Stack:** Vue 3, Vite, TypeScript, Pinia, Vue Router, `@vue-flow/core`, native CSS tokens, Vitest, Playwright, FastAPI, NetworkX.

## Global Constraints

- Do not remove or overwrite the current Streamlit UI; it remains a fallback entrypoint.
- Do not change LightRAG retrieval, evidence policy, Generation lifecycle semantics, or database schemas.
- Ordinary user UI must not display API URLs, Chunk IDs, Generation IDs, trace IDs, model names, or credentials.
- All admin authorization must remain enforced by FastAPI; frontend route guards are presentation only. The admin token is entered by the user, kept in memory only, and sent as a Bearer header on admin requests.
- Use the existing public query response fields: `status`, `answer`, `citations`, `claims`, `evidence`, `partial_reason`, `request_id`, and `latency_ms`.
- Use `npm run build` and targeted Vitest checks for the new frontend; do not call real model APIs from tests.
- Preserve all unrelated dirty-worktree changes and do not commit unless the user explicitly requests a commit.

---

### Task 1: Scaffold the Vue frontend and shared contracts

**Files:**
- Create: `frontend/package.json`
- Create: `frontend/tsconfig.json`
- Create: `frontend/vite.config.ts`
- Create: `frontend/index.html`
- Create: `frontend/src/main.ts`
- Create: `frontend/src/app/App.vue`
- Create: `frontend/src/app/router.ts`
- Create: `frontend/src/types/api.ts`
- Create: `frontend/src/api/client.ts`
- Create: `frontend/src/styles/tokens.css`
- Test: `frontend/src/api/client.test.ts`

**Interfaces:**
- `ApiClient.listKnowledgeBases(): Promise<KnowledgeBase[]>`
- `ApiClient.queryKnowledgeBase(kbId: string, question: string, history: ChatHistoryItem[]): Promise<QueryResult>`
- `ApiClient.submitFeedback(input: FeedbackInput): Promise<void>`
- `ApiClient.getGraphOverview(limit: number): Promise<GraphPayload>`
- `ApiClient.getGraphNeighborhood(query: string, hops: 1 | 2): Promise<GraphPayload>`

- [ ] **Step 1: Create the package metadata and Vite proxy.**

  Configure Vue 3, TypeScript, Pinia, Vue Router, `@vue-flow/core`, Vitest, and `@vitejs/plugin-vue`. Proxy `/v1`, `/readyz`, and `/health` to `http://127.0.0.1:8000` in development.

- [ ] **Step 2: Define the API response types.**

  Model `ApiStatus` as `success | partial_answer | insufficient_evidence | safety_blocked | clarification_required | out_of_scope | failed`, and keep `Citation`, `Claim`, `Evidence`, `QueryResult`, `KnowledgeBase`, and graph node/edge types aligned with the current Python API response names.

- [ ] **Step 3: Implement the typed fetch client.**

  Normalize non-2xx responses to `{ code, message, retryable }` and never expose raw response bodies in UI errors. The query method must send `{ query, history }` to `/v1/knowledge-bases/{kbId}/query` and preserve `request_id` for feedback.

- [ ] **Step 4: Add client tests with mocked fetch.**

  Test a successful query, an insufficient-evidence response, a 503 public error, and a feedback submission. Run `npm --prefix frontend run test -- --run src/api/client.test.ts`; expected result: all tests pass without network access.

- [ ] **Step 5: Build the scaffold.**

  Run `npm --prefix frontend run build`; expected result: `frontend/dist` is created and TypeScript/Vue compilation succeeds.

### Task 2: Implement the unified shell, role-aware navigation, and theme

**Files:**
- Create: `frontend/src/layouts/WorkspaceLayout.vue`
- Create: `frontend/src/app/stores/session.ts`
- Create: `frontend/src/views/admin/AdminGateView.vue`
- Create: `frontend/src/components/AppStatusBadge.vue`
- Create: `frontend/src/components/ConfirmDialog.vue`
- Modify: `frontend/src/app/App.vue`
- Modify: `frontend/src/app/router.ts`
- Modify: `frontend/src/styles/tokens.css`
- Test: `frontend/src/app/stores/session.test.ts`

**Interfaces:**
- `sessionStore.activeKnowledgeBaseId: string | null`
- `sessionStore.role: 'user' | 'admin'`
- `sessionStore.adminToken: string | null`
- `sessionStore.enterAdminMode(token: string): void`
- `sessionStore.leaveAdminMode(): void`
- `sessionStore.selectKnowledgeBase(id: string): void`
- `sessionStore.clearChat(): void`
- `sessionStore.hasAdminAccess: boolean`

- [ ] **Step 1: Add route definitions.**

  Define `/chat`, `/graph`, `/admin/login`, `/admin/knowledge-bases`, `/admin/documents`, `/admin/jobs`, and `/admin/generations`. Mark admin routes with `meta.requiresAdmin = true`; redirect users without an in-memory admin token to `/admin/login`.

- [ ] **Step 2: Build the shell layout.**

  Render a left navigation rail, compact header, knowledge-base selector, connection badge, and responsive main slot. The rail must show only user routes unless `sessionStore.hasAdminAccess` is true, and must include an “管理员入口” action rather than exposing admin pages to anonymous users.

- [ ] **Step 3: Add the visual token system.**

  Implement the approved canvas/surface/ink/cobalt/amber palette, panel/control radii, panel shadow, 44px control sizing, focus rings, and reduced-motion media query. Use `Microsoft YaHei`/`Segoe UI` for content and `Bahnschrift`/`Cascadia Mono` for technical metadata.

- [ ] **Step 4: Add the administrator gate and confirmation dialog behavior.**

  `AdminGateView` accepts a token, stores it only in the Pinia runtime state, and calls a lightweight admin-protected endpoint before enabling admin navigation. When a knowledge-base switch or clear-chat action would discard messages, show a modal with cancel/confirm actions. Confirming must clear only the local chat session and not call a backend delete endpoint.

- [ ] **Step 5: Test shell state.**

  Test that a missing token redirects to `/admin/login`, an entered token enables admin routes, leaving admin mode removes the token, and clearing a session removes messages but preserves the selected knowledge base. Run `npm --prefix frontend run test -- --run src/app/stores/session.test.ts`.

### Task 3: Build the chat workbench and high-frequency entry points

**Files:**
- Create: `frontend/src/views/ChatView.vue`
- Create: `frontend/src/components/chat/HighFrequencyPrompts.vue`
- Create: `frontend/src/components/chat/ChatTimeline.vue`
- Create: `frontend/src/components/chat/ChatComposer.vue`
- Create: `frontend/src/components/chat/AnswerMessage.vue`
- Create: `frontend/src/components/chat/FeedbackActions.vue`
- Modify: `frontend/src/app/stores/session.ts`
- Test: `frontend/src/components/chat/chat-workbench.test.ts`

**Interfaces:**
- `ChatView` consumes `ApiClient`, `sessionStore.activeKnowledgeBaseId`, and `sessionStore.messages`.
- `ChatTimeline` accepts `messages: ChatMessage[]` and emits `select-citation`, `retry`, and `submit-feedback`.
- `AnswerMessage` accepts one `AssistantMessage` and renders all five UI statuses without exposing internal IDs.
- `HighFrequencyPrompts` emits `submit(question: string)`.

- [ ] **Step 1: Add empty-state task groups.**

  Render the groups “启动与停机”, “故障排查”, and “维修安全”, each with the confirmed Chinese sample questions. Clicking a prompt emits one submit event and inserts it into the composer before the query begins.

- [ ] **Step 2: Implement the chat session action.**

  Add the user message immediately, set `chatStatus = 'loading'`, send the last six non-error messages as history, append the assistant result, and always restore an enabled composer after success or failure.

- [ ] **Step 3: Render structured answers.**

  Render answer Markdown as returned; when the content contains headings such as “结论”, “操作步骤”, or “注意事项”, apply section styling without changing text. Render claims and citations using stable citation labels.

- [ ] **Step 4: Implement status-specific UI.**

  Use cobalt for success, amber for partial/insufficient evidence, red for safety-blocked/error, and plain explanatory copy for clarification/out-of-scope responses. Retry must reuse the original question without duplicating an unrelated message.

- [ ] **Step 5: Implement feedback actions.**

  “有帮助” submits immediately. “没帮助” expands a reason select and optional comment, then submits with the answer `request_id`; after success, show a local confirmation and prevent duplicate submission.

- [ ] **Step 6: Test the workbench.**

  Test prompt emission, loading lock, success rendering, insufficient-evidence rendering, and retry preservation with mocked API results. Run `npm --prefix frontend run test -- --run src/components/chat/chat-workbench.test.ts`.

### Task 4: Implement the evidence drawer and citation interaction

**Files:**
- Create: `frontend/src/components/chat/EvidenceDrawer.vue`
- Create: `frontend/src/components/chat/CitationTag.vue`
- Modify: `frontend/src/views/ChatView.vue`
- Modify: `frontend/src/components/chat/AnswerMessage.vue`
- Test: `frontend/src/components/chat/evidence-drawer.test.ts`

**Interfaces:**
- `CitationTag` emits `select(citationId: string)`.
- `EvidenceDrawer` accepts `visible`, `evidence`, and `selectedCitationId`, and emits `close` and `select`.

- [ ] **Step 1: Add citation tags.**

  Render `[1]`, `[2]`, and so on as keyboard-focusable buttons with a cobalt left rail. The accessible label must include document name and page number.

- [ ] **Step 2: Add the drawer.**

  Show document name, physical page, section path when present, excerpt, relevance label, and “查看上下文”. Keep internal `chunk_id`, `generation_id`, and trace IDs hidden from ordinary users.

- [ ] **Step 3: Add responsive behavior.**

  Use a right rail at desktop width, a 420px overlay panel below 1200px, and a full-width bottom sheet on mobile. Escape and backdrop click close the drawer; changing citations does not close it.

- [ ] **Step 4: Test drawer behavior.**

  Test open, switch citation, close with Escape, and empty-evidence behavior. Run `npm --prefix frontend run test -- --run src/components/chat/evidence-drawer.test.ts`.

### Task 5: Add the read-only graph projection and Vue graph view

**Files:**
- Create: `src/industrial_rag/routers/graph.py`
- Modify: `src/industrial_rag/api.py`
- Create: `frontend/src/views/GraphView.vue`
- Create: `frontend/src/components/graph/GraphCanvas.vue`
- Create: `frontend/src/components/graph/GraphFilters.vue`
- Modify: `frontend/src/api/client.ts`
- Test: `tests/test_graph_api.py`
- Test: `frontend/src/components/graph/graph-view.test.ts`

**Interfaces:**
- `GET /v1/graph/overview?limit=50` returns `{ nodes, edges, stats }`.
- `GET /v1/graph/neighborhood?query=<text>&hops=1|2` returns the same shape.
- The route is read-only and uses the configured `LIGHTRAG_WORKING_DIR` GraphML path.

- [ ] **Step 1: Extract graph projection helpers.**

  Reuse existing `graph_visualizer` functions to locate GraphML, build overview/neighborhood subgraphs, map display labels/types, and serialize only user-safe node/edge fields.

- [ ] **Step 2: Add FastAPI routes.**

  Return 404/503 public errors when GraphML is missing or unreadable. Do not call LightRAG or a model. Register the router under `/v1/graph`.

- [ ] **Step 3: Add graph controls.**

  Implement overview/search modes, the confirmed entity shortcuts, 1/2-hop control, and an advanced panel for edge labels and node labels. Keep node/edge counts as secondary metadata, not the page hero.

- [ ] **Step 4: Render the graph.**

  Use `@vue-flow/core` for pan/zoom, node focus, and edge rendering. The backend adds deterministic `x`/`y` positions from a seeded NetworkX layout so the frontend does not need a second graph-layout engine. Preserve click-to-focus and neighbor highlighting from the current Pyvis behavior without adding graph editing.

- [ ] **Step 5: Test the projection.**

  Use a temporary GraphML fixture in `tests/fixtures/` and verify overview, matching neighborhood, missing-file error, and bounded node count. Run `C:\Users\12189\.conda\envs\industrial-rag\python.exe -m pytest tests/test_graph_api.py -q` and the targeted frontend graph test.

### Task 6: Migrate admin views into the same Vue shell

**Files:**
- Create: `frontend/src/views/admin/KnowledgeBasesView.vue`
- Create: `frontend/src/views/admin/DocumentsView.vue`
- Create: `frontend/src/views/admin/JobsView.vue`
- Create: `frontend/src/views/admin/GenerationsView.vue`
- Create: `frontend/src/components/admin/DocumentActions.vue`
- Create: `frontend/src/components/admin/GenerationActions.vue`
- Modify: `frontend/src/api/client.ts`
- Test: `frontend/src/views/admin/admin-views.test.ts`

**Interfaces:**
- Admin methods mirror the existing Streamlit calls: list/detail knowledge bases, list/upload/replace/delete documents, list update jobs, validate/promote/rollback/diff generations.
- UI actions must require the in-memory admin token entered through `AdminGateView` and must show confirm dialogs for delete, promote, and rollback.

- [ ] **Step 1: Port knowledge-base and document lists.**

  Reuse the existing public fields and display status/document counts; never display credentials.

- [ ] **Step 2: Port update jobs.**

  Show operation, status, stage, retry count, and safe error code in a compact table with refresh action.

- [ ] **Step 3: Port Generation lifecycle actions.**

  Add validate, promote, rollback, and diff actions with explicit confirmation and visible result status. Preserve the backend atomic pointer semantics.

- [ ] **Step 4: Test authorization and destructive-action confirmations.**

  Verify unconfirmed promote/rollback/delete actions cannot call the client and that the admin view handles public API errors.

### Task 7: Integrate, verify, and document the migration path

**Files:**
- Create: `frontend/.env.example`
- Modify: `README.md`
- Create: `scripts/start_frontend.ps1`
- Test: `frontend/e2e/chat-workbench.spec.ts`

- [ ] **Step 1: Add local startup.**

  `scripts/start_frontend.ps1` starts Vite on port 5173 and documents the API proxy. Keep `scripts/start_ui.ps1` unchanged; it continues to start the Streamlit fallback.

- [ ] **Step 2: Add browser smoke coverage.**

  Start the Vue dev server, open `/chat`, verify the three high-frequency groups, submit a mocked or stubbed query, open a citation drawer, switch to `/graph`, and verify admin routes are hidden for a normal role.

- [ ] **Step 3: Build and inspect the production bundle.**

  Run `npm --prefix frontend run build`; confirm `frontend/dist/index.html` and hashed assets exist. Do not delete the existing `dist/industrial-energy-agent-0.1.0-rc.1.zip`.

- [ ] **Step 4: Update README.**

  Document Vue startup, API proxy, route map, role boundary, and the Streamlit fallback. State clearly that real query verification requires the API and a ready knowledge base.

- [ ] **Step 5: Final verification.**

  Run the targeted Python graph/API tests, all frontend unit tests, `npm --prefix frontend run build`, and browser smoke. Report separately what was verified with mocked data versus a live knowledge-base query.
