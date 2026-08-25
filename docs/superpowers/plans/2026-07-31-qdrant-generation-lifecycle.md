# Qdrant Generation Lifecycle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver safe per-knowledge-base Nano/Qdrant migration and rollback using isolated, verified vector-index generations.

**Architecture:** A normalized `vector_index_generations` table owns immutable backend-generation metadata, its complete LightRAG workspace, exact Qdrant collections, and reproducibility hashes. `KnowledgeBase` retains only the active backend and active generation foreign key. Lifecycle tasks build a shadow generation from existing ChildChunk artifacts, validate it before activation, and perform only exact-generation cleanup on failure or KB deletion.

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy async, Alembic, LightRAG 1.5.4, Qdrant client, pytest, Ruff.

## Global Constraints

- The application runs in the current worktree with the local `industrial-rag` Conda environment and local Uvicorn.
- Qdrant Docker is database infrastructure only; do not create a second application container or use the old Phase 3 application.
- Audit the installed LightRAG 1.5.4 storage implementation before defining Qdrant namespaces; one centralized resolver owns namespace-to-collection naming.
- A generation owns a complete LightRAG workspace at `KB/nano/workspace` or `KB/qdrant/generations/<generation>/workspace`.
- Migration rebuilds from existing ChildChunk artifacts and never reparses PDFs.
- Do not promote a shadow generation before local and Qdrant validation succeeds.
- Never list-delete or fuzzy-delete Qdrant collections. Tests use an independent approved test prefix and delete only exact resolver outputs.
- Preserve Nano workspace for rollback; rollback may switch metadata only when the stored Nano generation fingerprint equals current KB input fingerprints.
- Keep full-KB shadow rebuild for document deletion/reparse/reindex; do not introduce point-level deletion.
- Stop after Phase 3; do not begin rerank.

---

## File Structure

- `src/industrial_rag/db/models.py`: active generation FK and generation/task enums.
- `src/industrial_rag/repositories/vector_index_generation_repository.py`: generation persistence and exact active-generation queries.
- `src/industrial_rag/services/generation_fingerprint_service.py`: deterministic current-input fingerprints.
- `src/industrial_rag/vector_collections.py`: audited namespace constants and centralized physical-name resolver.
- `src/industrial_rag/services/vector_generation_service.py`: generation creation, health validation, activation, and exact cleanup.
- `src/industrial_rag/services/index_service.py`: shadow indexing into a supplied generation.
- `src/industrial_rag/services/knowledge_base_service.py`, `handler_impls.py`, `routers/*`: idempotent migration/rollback lifecycle task API.
- `src/industrial_rag/services/runtime_manager.py`, `api.py`: generation-aware runtime selection and async KB query.
- `src/industrial_rag/services/cleanup_service.py`: exact generation record and workspace cleanup.
- `migrations/versions/*`: normalized schema migration.
- `tests/test_*generation*.py`, `tests/test_qdrant_*`: unit and opt-in infrastructure coverage.

### Task 1: Restore verified test environment and audit installed LightRAG namespaces

**Files:**
- Modify: `docs/qdrant-storage-design.md`
- Test: `tests/test_vector_collections.py`

- [x] Run `conda env list`, identify the documented `industrial-rag` Python 3.11 interpreter, then run its `import fitz` and `python -m pytest --collect-only -q`.
- [x] Inspect installed `lightrag.kg.qdrant_impl.QdrantVectorDBStorage` through the verified interpreter and record exact namespace construction behavior and LightRAG package version in the design record.
- [x] Update `QDRANT_VECTOR_NAMESPACES` only from the audited behavior, retaining a single `CollectionNameResolver.names_for(kb_id, generation)` interface.
- [x] Run `python -m pytest tests/test_vector_collections.py -q` and `ruff check src/industrial_rag/vector_collections.py tests/test_vector_collections.py`.

### Task 2: Normalize vector generation persistence and workspace layout

**Files:**
- Modify: `src/industrial_rag/db/models.py`, `src/industrial_rag/storage_layout.py`, `src/industrial_rag/repositories/knowledge_base_repository.py`
- Create: `src/industrial_rag/repositories/vector_index_generation_repository.py`, `migrations/versions/<revision>_add_vector_index_generations.py`, `tests/test_vector_index_generation_repository.py`

- [x] Write failing tests for generation ownership, `KnowledgeBase.active_vector_generation_id`, exact backend/generation uniqueness, and workspace layouts.
- [x] Add `VectorIndexGeneration` with the required provenance/fingerprint/status fields, FK to KB, indexes, and a uniqueness constraint for `(knowledge_base_id, backend, generation)`.
- [x] Add a nullable `active_vector_generation_id` FK to `KnowledgeBase`; remove JSON generation history only in the migration that safely copies existing records, retaining compatibility during transition if data migration requires it.
- [x] Add paths `KB/nano/workspace` and `KB/qdrant/generations/<generation>/workspace`; migrate new KB construction to the Nano path without touching existing existing workspaces until their generation record is established.
- [x] Implement repository methods `create_shadow`, `get_active`, `list_for_kb`, `activate`, `mark_failed`, and `list_cleanup_candidates`.
- [x] Run targeted tests plus Alembic upgrade/downgrade against a disposable SQLite database.

### Task 3: Deterministic generation fingerprints and validation

**Files:**
- Create: `src/industrial_rag/services/generation_fingerprint_service.py`, `tests/test_generation_fingerprint_service.py`
- Modify: `src/industrial_rag/services/qdrant_collection_service.py`

- [x] Write failing tests with reordered documents/chunks and changed document versions, embeddings, or chunking settings.
- [x] Implement a fingerprint object containing active document IDs/versions, document manifest hash, ChildChunk manifest hash, embedding config hash, and chunking config hash.
- [x] Hash canonical JSON sorted by document ID and ChildChunk ID so ordering does not change a valid fingerprint.
- [x] Validate Qdrant exact collections from the centralized resolver, expected chunks count, and vector dimension; do not require entities/relationships to be nonempty.
- [x] Run the new tests and collection-service unit tests with a fake async Qdrant client.

### Task 4: Build and activate isolated Nano/Qdrant shadow generations

**Files:**
- Modify: `src/industrial_rag/services/index_service.py`, `src/industrial_rag/kb_runtime_settings.py`, `src/industrial_rag/lightrag_service.py`, `src/industrial_rag/physical_qdrant_storage.py`
- Create: `src/industrial_rag/services/vector_generation_service.py`, `tests/test_index_service_qdrant.py`

- [x] Write tests proving a Qdrant generation binds an independent workspace, three resolver-derived collections, all fingerprints, and its creating task before activation.
- [x] Pass `qdrant_api_key` explicitly through LightRAG storage kwargs and use it before environment fallback in the storage adapter.
- [x] Build a complete shadow workspace from ChildChunk artifacts, then validate the local index and exact Qdrant collections.
- [x] Activate only after validation: close KB runtimes, atomically activate the generation record, then update `KnowledgeBase.vector_backend` and active generation FK.
- [x] On failure mark the generation failed, delete only exact resolved collections and only that workspace; leave current active generation unchanged.
- [x] Run targeted tests.

### Task 5: Idempotent migration and validated rollback lifecycle API

**Files:**
- Modify: `src/industrial_rag/db/models.py`, `src/industrial_rag/repositories/task_repository.py`, `src/industrial_rag/services/knowledge_base_service.py`, `src/industrial_rag/services/handler_impls.py`, `src/industrial_rag/routers/schemas.py`, `src/industrial_rag/routers/knowledge_bases.py`
- Create: `tests/test_vector_backend_api.py`, `tests/test_vector_backend_handlers.py`

- [x] Add `migrate_to_qdrant` and `rollback_to_nano` task types.
- [x] Implement `POST /v1/knowledge-bases/{kb_id}/vector-backend`, returning the extant task for equivalent pending/running work, a healthy idempotent result for the already-active target, and 409 for conflicts.
- [x] Migrate by full ChildChunk shadow rebuild through `IndexService`; do not reparse.
- [x] Roll back by fingerprint comparison between the current KB input fingerprint and the active Nano generation fingerprint. If unequal or Nano generation unhealthy, return 409 and preserve Qdrant activation.
- [x] Preserve Qdrant generation records and collections on a successful Nano rollback.
- [x] Run API and handler tests.

### Task 6: Generation-aware runtime, async query, cleanup, and restart recovery

**Files:**
- Modify: `src/industrial_rag/services/runtime_manager.py`, `src/industrial_rag/api.py`, `src/industrial_rag/services/cleanup_service.py`, `src/industrial_rag/services/lifecycle_task_executor.py`, `src/industrial_rag/repositories/task_repository.py`
- Create: `tests/test_runtime_manager_generations.py`, `tests/test_qdrant_cleanup.py`, `tests/test_lifecycle_restart_recovery.py`

- [x] Cache runtimes by KB, backend, generation ID, workspace, and embedding configuration; `close_runtime(kb_id)` closes every generation cache entry for the KB.
- [x] Replace `run_until_complete` in KB query with an async route that uses application-resolved settings and preserves domain errors.
- [x] Delete Qdrant collections only from persisted generation collection records via resolver; delete the exact generation workspace tree, including Qdrant workspace root on KB deletion.
- [x] Recover all `running` tasks to retrying on local single-process startup and test immediate restart behavior.
- [x] Run targeted tests.

### Task 7: Baselines, opt-in Qdrant integration, migration verification, and report

**Files:**
- Modify: `docs/qdrant-storage-design.md`, `.env.example`
- Create: `tests/test_qdrant_integration.py`, `docs/phase-3-qdrant-storage-report.md`

- [x] Run verified Conda `pytest --collect-only`, full `pytest`, and `ruff check .` baselines before and after changes.
- [x] Add opt-in tests guarded by `IRA_QDRANT_INTEGRATION=1`, require a safe test prefix, use generated KB IDs/generations, and clean only exact resolver outputs.
- [x] Test Qdrant persistence, FastAPI restart selection, cross-KB isolation, exact KB deletion, outage non-promotion, and Nano rollback fingerprint rejection.
- [ ] Freeze Nano results, run the same fixed fixture/retrieval configuration against Qdrant, and document comparable inputs and outcomes without claiming an unavailable real evaluation.
- [x] Run Alembic upgrade/downgrade verification on a disposable database; record commands and results.
- [x] Write the final Phase 3 report and explicitly state that rerank was not started.
