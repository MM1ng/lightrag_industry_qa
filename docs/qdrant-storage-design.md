# Phase 3 Qdrant storage decision record

**Date:** 2026-07-31  
**Scope:** Qdrant vector storage, per-knowledge-base collection isolation, NanoVectorDB compatibility, migration/rollback, and reproducible comparison only.

## Fixed decisions

1. The current Worktree runs the business service locally with the `industrial-rag` Conda environment and Uvicorn. Qdrant is an independent database service; the legacy Phase-3 FastAPI container and its Worktree are never used.
2. `NanoVectorDBStorage` remains the default and legacy-safe backend. Existing Nano workspaces are neither renamed, overwritten, nor deleted.
3. Qdrant uses the project-defined `PhysicalQdrantVectorDBStorage`, not LightRAG 1.5.4's built-in `QdrantVectorDBStorage`. The built-in implementation places multiple workspaces in shared collections and therefore does not meet physical collection isolation.
4. Collection names are resolved only from a validated internal KB ID, an allow-listed LightRAG vector namespace, a validated generation token, and a validated configured prefix. User-provided KB names never participate.
5. Every Qdrant KB generation owns three physical collections: `chunks`, `entities`, and `relationships`. A generation is immutable after it is promoted.
6. Index/rebuild first writes a new **shadow generation**. It verifies all required collections and expected chunk points before atomically promoting the database metadata to that generation. On any Qdrant/index verification failure, promotion does not occur and the current Nano/Qdrant generation remains queryable.
7. A migration from Nano to Qdrant is a rebuild from existing `ChildChunk` artifacts, not a lossy import from Nano files. It preserves the existing parser, Parent–Child parameters, embedding model, `mix` retrieval mode, top-k values, and answer prompt.
8. Query runtime selects the database-recorded backend and active generation. Reverting to Nano changes only metadata; it does not delete Qdrant generations. Deleting a KB closes its runtime then deletes only exactly-resolved collections for that KB's recorded generations and verifies their absence.
9. Qdrant connection errors are propagated. They prevent generation promotion and lifecycle-task success; no silent Nano fallback can mark a Qdrant index successful.
10. All experiments use a configured dedicated test prefix. They never list-delete, overwrite, or alter pre-existing collections.

## Direct Phase-2 blockers addressed narrowly

- `src/industrial_rag/services/index_service.py:93-95` ignores `doc.id` and reads an impossible shared parsed-artifact location. It must load `parsed/documents/<doc-id>/current` before a Qdrant rebuild can index real ChildChunks.
- `src/industrial_rag/services/cleanup_service.py:27-36` lacks an external-vector cleanup step. It gains a checked, exact-name Qdrant cleanup step.
- `src/industrial_rag/api.py:220-235` creates the executor but stores `None`, so shutdown cannot stop it. It stores the actual executor.
- `src/industrial_rag/services/lifecycle_task_executor.py:114-125` and `:131-200` flush task status without committing. It receives the smallest transaction boundaries needed to persist Qdrant success/failure and recovery state.

Other previously observed Phase-2 discrepancies remain out of scope and are documented as known limitations in the final report.

## Verification contract

Offline unit tests cover resolver validation, physical isolation, exact cleanup, failure non-promotion, and backend selection. An opt-in integration suite uses only the test prefix to verify Qdrant persistence across client and FastAPI restart, cross-KB isolation, deletion, and outage failure propagation. The Nano/Qdrant comparison records immutable input hashes, fixed settings, warm-up policy, sample count, and latency/retrieval outputs without modifying the golden set.
