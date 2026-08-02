# Phase 9B Release Gate Closure & Multi-instance Operational Hardening Design

**Date:** 2026-08-02
**Approved scope:** Phase 9B only
**Baseline commit:** `ae9ef2640327574aad59c215b198410dabe7b566`

## 1. Outcome and boundaries

Phase 9B closes the release gates around the Phase 9 incremental-update flow. It adds durable validation evidence, database-backed concurrency control, crash-safe job recovery, cross-instance generation consistency, protected garbage collection, deterministic management authorization, and a compatible Qdrant version pair.

The phase does not add LangGraph, multi-hop retrieval, new question types, production deployment, a release tag, or an RC package. It does not change the frozen query strategy or the frozen formal knowledge base. All destructive and upgrade rehearsals run against isolated local-staging resources.

## 2. Options considered

### Option A — SQLite leases, database pointer reads, and explicit artifacts (selected)

Use the existing SQLite database as the coordination authority. Persist a per-KB lease with a monotonically increasing fencing token, re-read the Active Generation pointer on every query, persist validation and GC records, and store immutable JSONL artifacts whose hashes are checked before Promote or GC Execute.

This option fits the current single-host MVP, works across multiple Uvicorn processes, and adds no infrastructure. SQLite serializes the short conditional writes used to claim leases and jobs. Qdrant continues to hold generation-isolated vector data.

### Option B — filesystem locks and process notifications

File locks could coordinate local processes and notifications could evict caches. They do not provide durable audit history, monotonic fencing, safe stale-worker rejection, or reliable behavior when paths differ between deployments. This option is rejected.

### Option C — Redis/PostgreSQL queue and distributed locks

External coordination would support larger deployments but would add an unapproved operational dependency and a second deployment topology. It is unnecessary for the current local-staging MVP and is rejected.

## 3. Database model

One Alembic revision after `a7f3c9e2b1d4` adds the following state.

### `validation_runs`

Each row is append-only from the application perspective and records:

- `id`, `knowledge_base_id`, and `generation_id`;
- `status` (`running`, `passed`, `failed`, `abandoned`);
- `golden_set_version`, `golden_set_sha256`, and `runner_version`;
- `app_git_commit`, configured model, strategy fingerprint, and maximum age;
- generation manifest, Qdrant content, and document-registry fingerprints;
- timestamps, metrics, artifact path, artifact SHA-256, actor, request ID, and trace ID.

Only a completed `passed` row matching the current policy is eligible for Promote. Validation never overwrites a previous run.

### `kb_operation_leases`

There is one durable row per KB. It retains `fencing_token` even while unlocked and records owner, lease token, operation, job ID, acquisition, heartbeat, and expiry timestamps. Acquire is a conditional transaction that increments the fencing token. Heartbeat, release, and protected writes require the current owner, lease token, and fencing token. Release clears ownership but never resets the counter.

### `update_jobs` additions

Status values become `pending`, `claimed`, `running`, `validating`, `succeeded`, `failed`, `cancelled`, and `recovery_required`. Rows add worker ID, lease token, fencing token, claimed/heartbeat/expiry timestamps, attempt, maximum attempts, checkpoint, and cancellation metadata. Existing Phase 9 values are migrated deterministically.

### `gc_plans`

A GC plan records policy, exact item list, manifest hash, timestamps, status, creator, approver, and execution results. Items name exact KB, Generation, and Collection IDs; no prefix expression is accepted.

### generation protection metadata

Generation rows add `protect_from_delete`, `audit_frozen`, `retention_until`, `content_epoch`, and the latest validated fingerprint. Knowledge bases add a monotonically increasing `generation_epoch` and the most recent rollback target ID. These fields make protection and cross-instance switching explicit.

## 4. Authentication and authorization

The existing Bearer middleware becomes the single authentication mechanism.

- `SERVICE_API_KEY` maps to role `service`.
- `ADMIN_API_KEY` maps to role `admin`.
- Both use `Authorization: Bearer <token>` and constant-time comparison.
- Missing, malformed, empty, or unknown credentials return 401.
- A valid service credential on an admin route returns 403 with `ADMIN_PERMISSION_REQUIRED`.
- Both roles may call query and read-only business routes.
- All state-changing KB, document, Generation, job, and GC routes require admin.

The middleware stores an immutable `AuthenticatedActor` on request state. Actor IDs are `role:` plus the first 12 hexadecimal characters of SHA-256(token). Routers use `require_authenticated_actor` or `require_admin_actor`; they never parse tokens themselves. `created_by`, `approved_by`, and actor values come only from this context. Client identity headers or fields are rejected or ignored.

Application startup and `check_env.ps1` fail when either required staging credential is missing or when both values are equal. Secrets are excluded from repr, responses, logs, manifests, artifacts, database rows, and UI rendering.

## 5. Canonical Candidate validation

### Candidate query isolation

An admin-only route queries a specified Generation without changing the KB Active pointer. It derives settings from the requested Generation record, uses a cache key containing KB and Generation, runs the same safety, LightRAG query, evidence, citation, and response formatting path as the public KB query, and returns the actual Generation ID used.

The shared query application service eliminates divergent behavior between Active and Candidate routes. Candidate validation never promotes, rewrites the Active pointer, or places Candidate points in Active collections.

### Real HTTP runner

`CanonicalValidationRunner` loads the frozen 20-question subset from `data/evaluation/industrial_pump_golden_set_50.jsonl`. Its version and SHA-256 are fixed in policy. In local staging it sends actual HTTP requests to the admin Candidate query route, not direct service calls. Validation runtimes set LightRAG `enable_llm_cache=False`; each result records `cache_hit=false`.

The runner writes an immutable JSONL artifact containing KB, Generation, question, HTTP status, request/trace IDs, answer status, safety result, latency, failure reason, citations, and cited document/chunk/Generation IDs. It computes canonical 18-answerable/2-negative metrics and writes the artifact before finalizing the database row.

### Promote gate

Promote acquires the KB lease and then verifies, inside the protected operation:

1. the Candidate is `ready`;
2. a matching passed validation run exists;
3. golden-set version and SHA-256 equal the current policy;
4. the artifact exists and its SHA-256 matches;
5. the run is within its configured age;
6. app, strategy, parser, embedding, rerank, model, and query fingerprints still match;
7. generation manifest and `content_epoch` are unchanged;
8. a fresh full Qdrant point-content fingerprint matches;
9. a fresh document-registry fingerprint matches.

No request parameter can disable these checks. Test-only bypass is omitted entirely. Any change creates a mismatch and blocks Promote with stable HTTP 409.

## 6. Lease, fencing, and job recovery

The in-process lock remains only as a contention optimization. Every update build, validation state transition, Promote, Rollback, and GC execution holds a database lease.

Job creation and uploaded-file registration commit before candidate construction starts. A worker claims one eligible row with a conditional update, acquires the KB lease, and writes stage checkpoints after parsing, collection creation, point copy/upsert, manifest completion, validation, and final state changes. A heartbeat task renews both the job and KB lease.

Each important update includes a fencing predicate. A stale worker whose lease expired cannot change a Generation, Active pointer, job completion state, or collection lifecycle state. It records a stale-worker rejection metric and stops.

At startup and on an interval, workers scan only expired `claimed`, `running`, or `validating` jobs. Recovery classifies state from the job checkpoint, candidate row, exact collections, manifests, and lease:

- already-complete stages finalize idempotently;
- deterministic stages resume from the last durable checkpoint;
- inconsistent partial writes become `recovery_required` or safely failed;
- succeeded jobs are never rebuilt;
- Promote that committed before response loss returns `already_active` on retry.

## 7. Multi-instance Active consistency

Every KB query opens a short database session and reads the current Active Generation ID and `generation_epoch`. Runtime cache keys already include KB and Generation and will also carry the epoch. If the cached identity differs, the instance closes the stale runtime and creates the current one. Responses expose the actual Generation ID.

Because the database is consulted on every request, instance B observes a Promote or Rollback by instance A without restart or process-local notification. Local eviction remains an optimization. A mismatch counter records attempted stale-cache reuse.

## 8. Retention and garbage collection

`GenerationGarbageCollector.plan()` is read-only. It protects Active, ready, validating, rollback targets, the newest successful archived generation, the latest three archived generations, frozen/audited/protected rows, rows referenced by live jobs or validation, frozen KBs, and collections whose ownership cannot be proven.

Default retention is seven days for failed/cancelled candidates and three archived generations. Staging and production reject zero retention.

Execute requires a different explicit confirmation request on an unexpired plan and an admin actor. It acquires the KB lease, rechecks all protection rules and the Active pointer, verifies the plan manifest and exact collection fingerprints, and deletes only exact named collections. Each item has an independent result. Point counts before and after and actor approval are recorded. Dry Run cannot invoke any delete operation.

## 9. Qdrant compatibility

The selected pair is `qdrant-client==1.13.3` with `qdrant/qdrant:v1.13.6`. LightRAG 1.5.4 declares `qdrant-client>=1.11.0,<2.0.0`, and the project uses methods present in 1.13.3. Qdrant documents that client and server should be no more than one minor version apart; the selected pair shares minor version 1.13.

No existing Qdrant server or data directory is upgraded. A new `ira-phase9b-qdrant-staging` container and dedicated storage are used. Data is exported through read-only scroll operations and restored into the isolated server. The rehearsal covers create, read, copy, filter, count, and exact delete plus Phase 9 add/replace/delete/validate/promote/rollback. Logs must contain zero client/server compatibility warnings.

Primary references:

- https://qdrant.tech/documentation/faq/qdrant-fundamentals/
- https://qdrant.tech/documentation/operations/upgrades/
- https://github.com/HKUDS/LightRAG

## 10. Observability

A dependency-free operational metrics registry exposes counters, gauges, durations, and validation age through an authenticated read-only endpoint. It implements every metric named in the Phase 9B request. Structured operation logs include request, trace, job, validation, KB, Generation, masked lease owner, fencing token, operation, result, and sanitized error fields.

The existing forbidden-field sanitizer is extended to remove Authorization, tokens, endpoints, credentials, full local user directories, and document bodies recursively.

## 11. Verification and staging evidence

Unit and integration tests are written red-first for authorization, validation invalidation, leases/fencing, job recovery, multi-instance reads, GC safety, and Qdrant compatibility. Existing Phase 9 tests remain green.

The isolated local-staging rehearsal records immutable baseline fingerprints, runs two API instances against one staging SQLite database and the isolated Qdrant, validates and promotes a cloned-manual Candidate, confirms instance B changes Generation without restart, rolls back, injects a failing golden set, performs one crash recovery, and executes GC only for an eligible Phase 9 test Candidate.

The final report records exact Git commits and ahead/behind counts; pre/post tests and Ruff; migration and model details; Active and Candidate 20-question results; gate blocking and invalidation; two-instance behavior; lease/fencing; crash recovery; GC plan/execute; Qdrant versions and warning count; authentication; point counts; frozen-resource fingerprints; secret scan; known limitations; and all five final decision flags. No placeholders are permitted.

