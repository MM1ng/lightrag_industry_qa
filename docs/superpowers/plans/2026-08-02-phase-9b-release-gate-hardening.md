# Phase 9B Release Gate Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the Phase 9 release gates with durable canonical validation, multi-instance coordination, recoverable jobs, protected GC, deterministic admin authorization, and a compatible Qdrant pair.

**Architecture:** SQLite is the coordination authority for leases, fencing tokens, jobs, validation runs, Active Generation epochs, and GC plans. FastAPI shares one authenticated query application service for Active and explicit Candidate generations; local-staging canonical validation reaches that service through actual HTTP. Qdrant collections stay Generation-isolated and are deleted only by exact, revalidated GC plan items.

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy async, Alembic, SQLite, Qdrant 1.13.6, qdrant-client 1.13.3, LightRAG 1.5.4, pytest, Ruff, PowerShell.

## Global Constraints

- Do not deploy production, create/push a Tag, or rebuild an RC package.
- Do not modify the frozen query strategy, frozen formal database, frozen formal KB, or existing formal Qdrant collections.
- Keep `phase3-uncommitted-backup.patch` untracked and untouched.
- Use real FastAPI HTTP and uncached model calls for the local-staging Candidate 20-question run.
- Never accept client-supplied actor, created_by, or approved_by identities.
- Never delete collections by prefix or run GC without a fresh explicitly approved plan.
- Keep every secret out of logs, responses, artifacts, database rows, reports, and committed configuration.

---

### Task 1: Deterministic Bearer actors and admin authorization

**Files:**
- Create: `src/industrial_rag/auth.py`
- Modify: `src/industrial_rag/config.py`
- Modify: `src/industrial_rag/api.py`
- Modify: `src/industrial_rag/errors.py`
- Modify: `src/industrial_rag/routers/documents.py`
- Modify: `src/industrial_rag/routers/generations.py`
- Modify: `src/industrial_rag/routers/knowledge_bases.py`
- Modify: `src/industrial_rag/routers/tasks.py`
- Modify: `src/industrial_rag/routers/update_jobs.py`
- Modify: `app/streamlit_app.py`
- Modify: `.env.example`
- Modify: `scripts/check_env.ps1`
- Test: `tests/test_phase9b_auth.py`

**Interfaces:**
- Produces: `AuthenticatedActor(role, actor, authenticated, credential_type)`.
- Produces: `authenticate_bearer(request)`, `require_authenticated_actor(request)`, and `require_admin_actor(request)` FastAPI dependencies.
- Produces: `Settings.admin_api_key` and startup validation that service/admin keys are present where required and unequal.

- [ ] **Step 1: Write failing authentication and secret-safety tests**

Cover missing/malformed/unknown credentials, both valid roles on query, service-on-admin 403, admin-on-admin success, stable hashed actor, forged identity rejection, equal-key startup failure, and response/log secret absence.

- [ ] **Step 2: Verify the tests fail for missing dual-role behavior**

Run: `python -m pytest tests/test_phase9b_auth.py -q`

Expected: failures show no `ADMIN_API_KEY`, no actor context, and management routes accept service identity.

- [ ] **Step 3: Implement actor parsing once in `auth.py`**

Use `secrets.compare_digest`, strict `Bearer ` parsing, SHA-256 actor IDs truncated to 12 hex characters, immutable actor objects, and stable 401/403 AppError codes. Attach actor context in middleware; dependencies read only that context.

- [ ] **Step 4: Apply admin dependencies and server-derived audit identities**

All mutations require admin. Query and read-only routes require either recognized role. Streamlit uses `SERVICE_API_KEY` for chat and `ADMIN_API_KEY` only for server-side management requests. Remove all use of `x-approved-by`.

- [ ] **Step 5: Verify auth tests and existing API tests**

Run: `python -m pytest tests/test_phase9b_auth.py tests/test_api.py tests/test_api_client.py -q`

Expected: all selected tests pass; neither test token appears in captured logs or response bodies.

- [ ] **Step 6: Commit**

Commit message: `feat(phase9b): enforce deterministic admin authorization`

### Task 2: Phase 9B persistence migration

**Files:**
- Create: `migrations/versions/b9c4e7f2a6d1_phase9b_operational_hardening.py`
- Modify: `src/industrial_rag/db/models.py`
- Create: `src/industrial_rag/repositories/validation_run_repository.py`
- Create: `src/industrial_rag/repositories/kb_lease_repository.py`
- Create: `src/industrial_rag/repositories/gc_plan_repository.py`
- Modify: `src/industrial_rag/repositories/update_job_repository.py`
- Test: `tests/test_phase9b_migration.py`

**Interfaces:**
- Produces: `ValidationRun`, `KBOperationLease`, and `GCPlan` ORM models.
- Produces: Phase 9B job claim/checkpoint fields, Generation protection/content epoch fields, and KB generation epoch/rollback target fields.

- [ ] **Step 1: Write failing migration upgrade/downgrade and model tests**

Create an `a7f3c9e2b1d4` SQLite database, insert representative Phase 9 rows, upgrade to head, assert deterministic status mapping and new constraints, downgrade, then upgrade again.

- [ ] **Step 2: Verify migration tests fail because the revision and models do not exist**

Run: `python -m pytest tests/test_phase9b_migration.py -q`

- [ ] **Step 3: Add focused models and the Alembic revision**

Use string statuses compatible with SQLite, foreign keys for KB/Generation/job references, indexes on validation eligibility and job lease expiry, and JSON only for immutable artifact metadata and exact GC items.

- [ ] **Step 4: Add repositories with conditional-update methods**

Repositories expose append-only validation creation/finalization, lease acquire/heartbeat/release/current-token checks, atomic job claim/heartbeat/checkpoint, and GC plan create/approve/finalize.

- [ ] **Step 5: Verify migration and repository tests**

Run: `python -m pytest tests/test_phase9b_migration.py -q`

- [ ] **Step 6: Commit**

Commit message: `feat(phase9b): add operational hardening persistence`

### Task 3: Durable KB leases, fencing, and recoverable jobs

**Files:**
- Create: `src/industrial_rag/services/kb_lease_service.py`
- Create: `src/industrial_rag/services/update_job_worker.py`
- Modify: `src/industrial_rag/services/incremental_update_service.py`
- Modify: `src/industrial_rag/api.py`
- Modify: `src/industrial_rag/routers/update_jobs.py`
- Modify: `src/industrial_rag/routers/schemas.py`
- Test: `tests/test_phase9b_leases.py`
- Test: `tests/test_phase9b_job_recovery.py`

**Interfaces:**
- Produces: `LeaseHandle(kb_id, owner, lease_token, fencing_token, expires_at)`.
- Produces: `KBLeaseService.acquire`, `heartbeat`, `assert_current`, and idempotent `release`.
- Produces: `UpdateJobWorker.claim_one`, `run_claimed`, `recover_expired`, `resume`, and `cancel`.

- [ ] **Step 1: Write failing multi-session lease and fencing tests**

Two independent SQLite sessions race for one KB; one succeeds. Different KBs acquire concurrently. Expiry permits a higher fencing token. Old owner heartbeat, release, Generation write, and job completion are rejected.

- [ ] **Step 2: Verify lease tests fail against the process-local lock**

Run: `python -m pytest tests/test_phase9b_leases.py -q`

- [ ] **Step 3: Implement transactional lease operations and fence checks**

Use SQLite conditional updates, preserve monotonic counters across release, and include fencing predicates in Candidate, validation, Active pointer, rollback, collection lifecycle, and job finalization writes.

- [ ] **Step 4: Write failing crash-checkpoint and idempotency tests**

Cover parse exit, embedding exit, post-Qdrant/pre-DB exit, validation exit, pre-Promote exit, post-commit response loss, expired-job scan, duplicate claim, successful-job no-op, and inconsistent state to `recovery_required`.

- [ ] **Step 5: Verify job recovery tests fail**

Run: `python -m pytest tests/test_phase9b_job_recovery.py -q`

- [ ] **Step 6: Refactor job execution around committed claims and checkpoints**

Commit file/job registration before building, heartbeat leases during long provider/Qdrant stages, inspect exact candidate/collection/manifest state during recovery, and start/stop the worker scanner in FastAPI lifespan.

- [ ] **Step 7: Verify lease, recovery, and original Phase 9 tests**

Run: `python -m pytest tests/test_phase9b_leases.py tests/test_phase9b_job_recovery.py tests/test_phase9.py -q`

- [ ] **Step 8: Commit**

Commit message: `feat(phase9b): persist leases fencing and job recovery`

### Task 4: Explicit-Generation query and cross-instance consistency

**Files:**
- Create: `src/industrial_rag/services/query_application_service.py`
- Modify: `src/industrial_rag/api.py`
- Modify: `src/industrial_rag/services/runtime_manager.py`
- Modify: `src/industrial_rag/kb_runtime_settings.py`
- Modify: `src/industrial_rag/routers/schemas.py`
- Test: `tests/test_phase9b_multi_instance.py`

**Interfaces:**
- Produces: `QueryApplicationService.query_active` and admin-only `query_generation`.
- Produces: response `generation_id` and cache identity `(kb_id, generation_id, generation_epoch, settings fingerprint)`.

- [ ] **Step 1: Write failing Candidate isolation and two-instance tests**

Assert Candidate queries leave the Active pointer unchanged; instance B sees A's Promote without eviction/restart; both see Rollback; citations belong to the response Generation; and cache keys include Generation plus epoch.

- [ ] **Step 2: Verify tests fail because routing is local-eviction dependent**

Run: `python -m pytest tests/test_phase9b_multi_instance.py -q`

- [ ] **Step 3: Extract shared query behavior and add explicit Generation route**

Both routes use the same safety/evidence/citation response path. Active queries read the database pointer and epoch on each request. Candidate queries validate KB/Generation ownership and never write state.

- [ ] **Step 4: Update runtime cache identity and mismatch handling**

Close mismatched runtimes before use, increment `cache_generation_mismatch_total`, and expose the actual Generation ID in every KB query response.

- [ ] **Step 5: Verify multi-instance and API regression tests**

Run: `python -m pytest tests/test_phase9b_multi_instance.py tests/test_api.py tests/test_phase9.py -q`

- [ ] **Step 6: Commit**

Commit message: `feat(phase9b): synchronize active generations across instances`

### Task 5: Immutable canonical validation and mandatory Promote gate

**Files:**
- Create: `src/industrial_rag/services/golden_set_policy.py`
- Create: `src/industrial_rag/services/canonical_validation_runner.py`
- Create: `src/industrial_rag/services/generation_content_fingerprint.py`
- Modify: `src/industrial_rag/services/generation_validation_service.py`
- Modify: `src/industrial_rag/services/incremental_update_service.py`
- Modify: `src/industrial_rag/lightrag_service.py`
- Modify: `src/industrial_rag/config.py`
- Modify: `src/industrial_rag/routers/generations.py`
- Modify: `src/industrial_rag/routers/schemas.py`
- Test: `tests/test_phase9b_validation_gate.py`

**Interfaces:**
- Produces: frozen `GoldenSetPolicy(version, path, sha256, ids, thresholds)`.
- Produces: full Qdrant point/vector/payload and document-registry fingerprints.
- Produces: append-only validation artifacts and `PromoteGate.verify(generation_id)`.

- [ ] **Step 1: Write failing gate tests**

Cover no run, missing runner, failed run, artifact tamper, artifact deletion, expired run, Candidate point mutation, document-state mutation, Generation manifest mutation, golden version/hash change, strategy/code/config change, and ready-only Promote.

- [ ] **Step 2: Verify the tests fail because Phase 9 accepts structural booleans**

Run: `python -m pytest tests/test_phase9b_validation_gate.py -q`

- [ ] **Step 3: Implement policy, uncached runtime setting, fingerprints, and artifacts**

Hash deterministic normalized representations. Write artifact to a temporary path, fsync/replace it, hash the final bytes, then finalize the validation row. Never infer a pass when the runner is absent.

- [ ] **Step 4: Implement real HTTP canonical runner and Promote verification**

The staging runner calls the Candidate FastAPI endpoint for all 20 frozen IDs with `enable_llm_cache=False`, records all required fields, calculates canonical metrics, and transitions to `ready` only on pass. Promote recomputes every fingerprint while holding the fenced lease.

- [ ] **Step 5: Verify validation and Phase 9 regression tests**

Run: `python -m pytest tests/test_phase9b_validation_gate.py tests/test_phase9.py -q`

- [ ] **Step 6: Commit**

Commit message: `feat(phase9b): require immutable canonical validation`

### Task 6: Protected retention and two-stage GC

**Files:**
- Create: `src/industrial_rag/services/generation_gc_service.py`
- Create: `src/industrial_rag/routers/gc.py`
- Modify: `src/industrial_rag/api.py`
- Modify: `src/industrial_rag/config.py`
- Modify: `src/industrial_rag/routers/schemas.py`
- Test: `tests/test_phase9b_gc.py`

**Interfaces:**
- Produces: `GenerationGCService.create_plan`, `approve_plan`, and `execute_plan`.
- Produces: admin routes for Dry Run and confirmed Execute using exact plan IDs.

- [ ] **Step 1: Write failing GC protection and staleness tests**

Dry Run performs zero deletes. Plans exclude Active, ready, validating, rollback target, recent archived, frozen/audited/protected, live-job-referenced, and unknown-owner collections. Expired or changed-content plans fail. Exact eligible items delete independently.

- [ ] **Step 2: Verify GC tests fail because no planner exists**

Run: `python -m pytest tests/test_phase9b_gc.py -q`

- [ ] **Step 3: Implement policy-driven planning without mutation**

Enforce seven-day failed/cancelled retention, at least three archived generations, nonzero staging/production settings, exact ownership proof, point count, protection checks, and a normalized plan manifest hash.

- [ ] **Step 4: Implement fenced, revalidated Execute**

Require admin approval, unexpired plan, unchanged fingerprints, current Active recheck, and exact collection names. Record per-item before/after counts and errors; never broaden a failed item into another delete.

- [ ] **Step 5: Verify GC, auth, and Phase 9 tests**

Run: `python -m pytest tests/test_phase9b_gc.py tests/test_phase9b_auth.py tests/test_phase9.py -q`

- [ ] **Step 6: Commit**

Commit message: `feat(phase9b): add protected generation garbage collection`

### Task 7: Operational metrics and Qdrant compatibility

**Files:**
- Create: `src/industrial_rag/operational_metrics.py`
- Create: `scripts/phase9b_qdrant_compatibility.py`
- Modify: `src/industrial_rag/observability.py`
- Modify: `src/industrial_rag/api.py`
- Modify: `pyproject.toml`
- Modify: `scripts/start_qdrant.ps1`
- Modify: `scripts/check_env.ps1`
- Create: `docs/qdrant-version-matrix.md`
- Test: `tests/test_phase9b_observability.py`
- Test: `tests/test_phase9b_qdrant_compatibility.py`

**Interfaces:**
- Produces: in-process `OperationalMetrics` counters/gauges/durations and authenticated metrics snapshot route.
- Produces: isolated compatibility runner for create/read/copy/filter/count/exact-delete and warning capture.

- [ ] **Step 1: Write failing metrics, recursive sanitization, dependency-pin, and warning tests**

Assert every requested metric exists, forbidden nested fields are removed, local user paths and endpoints are sanitized, dependency pins match the version matrix, and a 1.13.3 client against 1.13.6 emits no compatibility warning.

- [ ] **Step 2: Verify tests fail against current 1.18/1.13 pair**

Run: `python -m pytest tests/test_phase9b_observability.py tests/test_phase9b_qdrant_compatibility.py -q`

- [ ] **Step 3: Add metrics and structured operation logging**

Instrument jobs, validation, generation switches/rollbacks, leases, stale workers, GC, and cache mismatches. Do not add secret-valued labels.

- [ ] **Step 4: Pin and install the compatible client and isolate Qdrant rehearsal config**

Pin `qdrant-client==1.13.3`; keep server image `qdrant/qdrant:v1.13.6`; use a new Phase 9B container name, ports, and volume. Update environment checks and version documentation.

- [ ] **Step 5: Run isolated Qdrant compatibility tests**

Run: `python -m pytest tests/test_phase9b_qdrant_compatibility.py -q`

Expected: create/read/copy/filter/count/exact-delete pass and captured compatibility-warning count is zero.

- [ ] **Step 6: Run incremental lifecycle regression**

Run: `python -m pytest tests/test_phase9.py tests/test_qdrant_integration.py -q`

- [ ] **Step 7: Commit**

Commit message: `chore(phase9b): align Qdrant client and server versions`

### Task 8: Isolated local-staging acceptance and final report

**Files:**
- Create: `scripts/phase9b_staging_rehearsal.py`
- Create: `evaluation/experiments/phase9b/baseline/`
- Create: `evaluation/experiments/phase9b/validation/`
- Create: `evaluation/experiments/phase9b/multi_instance/`
- Create: `evaluation/experiments/phase9b/recovery/`
- Create: `evaluation/experiments/phase9b/gc/`
- Create: `evaluation/experiments/phase9b/security/`
- Create: `evaluation/experiments/phase9b/qdrant/`
- Create: `docs/phase-9b-release-gate-hardening-report.md`
- Test: `tests/test_phase9b_artifacts.py`

**Interfaces:**
- Produces: reproducible local-staging evidence and the complete Phase 9B report.

- [ ] **Step 1: Write failing artifact-contract tests**

Assert required files, row fields, hashes, exact Git values, decision flags, zero secret count, zero compatibility warnings, no placeholders, and immutable frozen-KB before/after fingerprints.

- [ ] **Step 2: Verify artifact tests fail before rehearsal**

Run: `python -m pytest tests/test_phase9b_artifacts.py -q`

- [ ] **Step 3: Capture pre-rehearsal baseline**

Record exact branch/commit/ahead/behind, staging DB content fingerprint, frozen KB ID/Active Generation, exact collections/point counts, configuration hash, versions, API count, and fresh Active canonical results without overwriting Phase 7/8/9 artifacts.

- [ ] **Step 4: Run isolated two-instance, validation-failure, crash, and GC rehearsals**

Start API-A and API-B against one copied staging DB and the new Qdrant container. Clone the frozen manual KB into a test KB, create and validate a Candidate over real HTTP, Promote on A, query on B without restart, Rollback, execute the failing-set gate, kill/recover one worker, and delete exactly one eligible test Candidate through an approved GC plan.

- [ ] **Step 5: Run secret and frozen-resource scans**

Scan logs, JSON/JSONL, UI/API responses, database text fields, and report inputs for both actual staging credentials without persisting their values. Confirm frozen DB/Qdrant fingerprints and all unrelated collection counts are unchanged.

- [ ] **Step 6: Write the report with exact evidence**

Include all 29 required report items and final decision flags. State any failed completion condition as false; do not infer or fabricate evidence.

- [ ] **Step 7: Run full fresh verification**

Run: `python -m pytest --collect-only -q`

Run: `python -m pytest -q`

Run: `python -m ruff check .`

Run: `git diff --check`

- [ ] **Step 8: Run artifact contract tests and placeholder scan**

Run: `python -m pytest tests/test_phase9b_artifacts.py -q`

Run: `rg -n "N个|待填写|以实际为准|TBD|TODO" docs/phase-9b-release-gate-hardening-report.md evaluation/experiments/phase9b`

Expected: artifact tests pass and the placeholder scan returns no matches.

- [ ] **Step 9: Commit**

Commit message: `test(phase9b): record operational hardening acceptance`

- [ ] **Step 10: Commit final report after recalculating Git facts**

Commit message: `docs(phase9b): report release gate hardening`

