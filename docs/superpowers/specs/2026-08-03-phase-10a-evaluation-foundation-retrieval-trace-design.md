# Phase 10A Evaluation Foundation & Retrieval Trace Design

## 1. Objective and boundary

Phase 10A establishes trustworthy measurement before retrieval tuning. It adds an immutable, admin-only retrieval trace tied to the exact ordinary-query execution, expands and freezes the real-PDF golden set to 64 questions, reruns the current baseline without cache, and produces per-question diagnosis plus retrieval/answer metrics.

Phase 10A does not change chunking, tables, TopK, retrieval weights, Rerank, prompts, answer generation, evidence thresholds, refusal thresholds, or the ordinary API/UI contract. It does not create a tag, repackage an RC, deploy production, introduce LangGraph, or begin Phase 10B–10D.

## 2. Delivery decomposition

Phase 10 is delivered serially:

1. Phase 10A: Evaluation Foundation & Retrieval Trace.
2. Phase 10B: Retrieval and Answer Quality Optimization.
3. Phase 10C: Feedback Backend & Lightweight Conversation Context.
4. Phase 10D: User-facing Product Interface.

This design covers only Phase 10A. Acceptance stops immediately after its report; the next subphase requires a new design and explicit user approval.

## 3. Considered trace-storage approaches

### 3.1 Database immutable records — selected

Persist one JSON trace record per request in SQLite through a dedicated table and repository. A unique request ID prevents replacement, an expiry index supports TTL cleanup, and every API instance can resolve the record. This follows the existing persistence architecture and supports deterministic tests.

### 3.2 Process-local TTL cache — rejected

This is simpler but loses traces on restart, cannot serve a request handled by another instance, and makes evaluator results depend on process affinity.

### 3.3 Append-only JSONL — rejected

JSONL is useful as an evaluation artifact but is unsuitable as the online lookup store: request lookup is unindexed, concurrent append and crash semantics are weaker, and TTL removal would require rewriting files.

## 4. Query-path architecture

The existing query execution remains authoritative:

`ordinary FastAPI query → QueryApplicationService → LightRAGRuntime → LightRAGService.aquery_data → evidence selection → answer generation → ordinary response`

The LightRAG service will create an internal `RetrievalExecutionTrace` during that same call. It will capture ordered raw retrieval candidates before evidence selection, the selected candidates after evidence policy, answer citations, and stage timings. It will not call LightRAG or Qdrant a second time.

`QueryResult` will carry the internal trace alongside the existing answer, citations, mode, retrieval chunk IDs, and retrieval metadata. This is an internal Python contract only; the ordinary `QueryResponse` remains unchanged.

`QueryApplicationService` enriches the internal trace with trusted knowledge-base, Generation, Generation epoch, and document IDs already resolved for the response. The API adapter supplies request ID and trace ID, then attempts to insert the immutable record after the ordinary result is known.

Trace persistence is best effort. A failed insert increments `retrieval_trace_write_failure_total` and emits a structured warning containing request ID, trace ID, error type, and no credential or payload content. The ordinary response still succeeds.

## 5. Trace record schema

The trace version is `phase10a-retrieval-trace-v1`. Each record contains:

- `request_id`, `trace_id`, `trace_version`;
- `knowledge_base_id`, `generation_id`, `generation_epoch`;
- `original_query`, `normalized_query`;
- the effective retrieval configuration (`mode`, `top_k`, `chunk_top_k`, `rerank_enabled`);
- ordered `initial_results`;
- `rerank_applied`, ordered `reranked_results`;
- ordered `final_selected_chunks`;
- `normalization_ms`, `retrieval_ms`, `rerank_ms`, `evidence_selection_ms`, and `end_to_end_ms`;
- `created_at` and `expires_at`.

Every result item contains:

- `initial_rank`, `initial_score`, and `retrieval_source`;
- `document_id`, `document_name`, `page_number`, `chunk_id`;
- `section_path`, `matched_terms`;
- `reranked_rank`, `reranked_score`;
- `used_for_answer`, `cited_in_answer`.

Phase 10A does not introduce query expansion or alias normalization. `normalized_query` is the exact whitespace-trimmed query currently passed into LightRAG; it is recorded, not substituted with a new semantic rewrite.

The currently disabled Rerank is represented exactly as:

- `rerank_applied=false`;
- `reranked_results=[]`;
- every candidate has `reranked_rank=null` and `reranked_score=null`.

Initial rank is never copied into a reranked field. A missing upstream score is stored as null rather than fabricated. `retrieval_source` uses the source supplied by LightRAG evidence; if upstream provides no per-item source, the explicit stable value `lightrag_mix_unspecified` is used and documented in the manifest.

`matched_terms` is a deterministic, ordered intersection between normalized query terms and the candidate text already present in the same evidence payload. Candidate text itself is not persisted. `section_path` uses evidence metadata when present and otherwise remains an empty list.

The trace never contains Authorization, credentials, environment values, system or model prompts, raw vectors, full document text, local paths, or unsanitized endpoints. Original query is retained because it is required for diagnosis; TTL and admin-only access bound its exposure.

## 6. Persistence, TTL, and API

Migration `phase10a_retrieval_traces` creates:

- primary key `request_id`;
- indexed `trace_id`, `knowledge_base_id`, `generation_id`, and `expires_at`;
- non-null `trace_version`, JSON `payload`, `created_at`, and `expires_at`.

Records are insert-only. Repository code exposes `create_immutable`, `get_unexpired`, and `delete_expired`; no update method exists. Duplicate request IDs do not replace data.

`RETRIEVAL_TRACE_TTL_SECONDS` is read from the environment, defaults to 86,400 seconds, and must be between 60 and 604,800 seconds. The setting is recorded only as a number; no secret is involved. Expired records return the same sanitized 404 result as absent records.

The diagnostic router adds:

`GET /v1/admin/diagnostics/requests/{request_id}/retrieval-trace`

Authorization behavior:

- missing, malformed, empty, or unknown Bearer token: 401;
- valid SERVICE_API_KEY: 403 with `ADMIN_PERMISSION_REQUIRED`;
- valid ADMIN_API_KEY: 200 for an unexpired record;
- absent or expired trace: 404 with `RETRIEVAL_TRACE_NOT_FOUND`.

All errors use the existing request/trace/error envelope. Ordinary query responses, Streamlit data models, and ordinary logs do not expose ranks or scores.

## 7. Golden set expansion and freezing

The current 50-question set has 48 positives and 2 negatives but lacks Phase 10 annotations. Phase 10A produces `evaluation/phase10/expanded_golden_set.jsonl` with 64 questions drawn only from the two real manuals and their parsed child chunks.

Each record contains:

- `question_id`, `question`, `answerable`;
- `document_name`, `page_number`, `chunk_id`;
- `expected_answer_points`, `evidence_text`;
- `question_type`, `difficulty`, `negative_reason`;
- `split` (`development`, `validation`, or `holdout`).

Positive evidence must resolve to an actual child chunk and its actual document/page. Evidence text is a bounded excerpt copied from that chunk. Expected answer points are manually or deterministically extracted factual clauses from that evidence; no model-generated fact is accepted as ground truth. Negative records have null document/page/chunk, empty evidence and answer points, and a concrete negative reason.

The 64 records are stratified across parameter, table, procedure, safety warning, troubleshooting, maintenance interval, component description, condition limit, cross-page, multi-evidence, confusing device, Chinese/English terminology, unit expression, negative, and manual-not-covered types.

The frozen split is 36 development, 16 validation, and 12 holdout questions. IDs and split assignments are explicit and immutable. Phase 10B may tune on development, select on validation, and may run holdout only for final locked evaluation. Phase 10A may execute the holdout once to establish its untouched baseline because no tuning occurs in this phase.

`golden_set_manifest.json` records dataset SHA-256, record count, positive/negative counts, split and type distributions, source PDF hashes, child-chunk artifact hashes, annotation policy version, creation commit, and a `holdout_not_used_for_tuning=true` assertion.

## 8. Baseline runner and metrics

The evaluator always performs two calls in order:

1. POST the real ordinary knowledge-base query endpoint and read its request ID.
2. GET the admin retrieval-trace endpoint using that request ID.

It never calls a private retrieval function. The baseline staging process runs with LLM cache disabled and records this in its environment manifest.

Per question, the evaluator stores expected evidence, ordered initial and reranked retrieval, selected evidence, final citations, answer status, and stage latencies. It computes:

- Document Recall@1/3/5;
- Page Recall@1/3/5/10;
- Chunk Recall@1/3/5/10/20;
- MRR and nDCG@10;
- correct chunk first rank;
- correct evidence count in TopK;
- retrieval, rerank, and end-to-end latency;
- Answer Citation Accuracy;
- False Rejection Rate;
- Unsupported Answer Rate;
- Citation Trace Completeness;
- Negative Rejection Rate.

Metrics are emitted overall and by split, question type, difficulty, document, and answerability. Rates include numerator and denominator. Empty denominators are null, never zero-filled.

## 9. Failure diagnosis

`baseline_diagnosis.jsonl` contains one record for each fixed 20 question with:

- question ID and text;
- expected document, page, chunk, and answer points;
- retrieved document, page, chunk, initial rank, reranked rank, and final citations;
- answer status, failure layer, failure category, and specific reason.

Failure layers are Retrieval Error, Ranking Error, Evidence Selection Error, Answer Generation Error, Refusal Decision Error, and Citation Error.

Supported categories are `wrong_document`, `page_not_recalled`, `chunk_not_recalled`, `correct_chunk_rank_too_low`, `table_parse_failure`, `cross_page_context_missing`, `query_term_mismatch`, `metadata_filter_failure`, `evidence_threshold_too_high`, `generation_extraction_failure`, and `citation_binding_failure`. Successful questions use `failure_layer=null`, `failure_category=null`, and `failure_reason=null`.

Classification is deterministic from expected evidence, trace ranks, selected chunks, answer status, and citations. A question is never classified only as “model answered incorrectly.”

## 10. Artifacts and report

Phase 10A produces:

- `evaluation/phase10/baseline_diagnosis.jsonl`;
- `evaluation/phase10/baseline_summary.json`;
- `evaluation/phase10/expanded_golden_set.jsonl`;
- `evaluation/phase10/golden_set_manifest.json`;
- `evaluation/phase10/retrieval_metrics.json`;
- `evaluation/phase10/retrieval_trace_schema.json`;
- `evaluation/phase10/baseline_results.jsonl`;
- `docs/phase-10a-evaluation-foundation-retrieval-trace-report.md`.

The report includes actual Git commits, migration, auth matrix, TTL behavior, trace write-failure behavior, fixed-20 rerun, expanded-set metrics, per-type metrics, failure taxonomy, cache-disabled proof, secret scan, test/Ruff results, known limitations, no-tag/no-package/no-production proof, and a Phase 10A approval decision.

## 11. Testing and acceptance

Automated tests cover:

- the internal trace is produced by the same LightRAG query call;
- no second retrieval occurs for diagnostics;
- ordinary response schema is unchanged;
- initial order and nullable scores are preserved;
- disabled Rerank fields are false/empty/null and not copied;
- selected/cited flags are accurate;
- no forbidden data enters payloads, logs, or errors;
- immutable duplicate insert rejection and TTL expiry;
- persistence failure does not fail ordinary query and increments metrics;
- diagnostic 401/403/200/404 behavior;
- 64-question schema, provenance, split, type, hash, and holdout rules;
- all retrieval and answer metric formulas including per-type breakdown;
- all failure layers and categories;
- evaluator call order: ordinary query first, trace GET second.

Staging acceptance uses the frozen Active Generation, real FastAPI queries, admin trace reads, and disabled LLM cache. Phase 10A passes only if:

- trace completeness is 100% for every successful ordinary request;
- all required trace fields are present without forbidden data;
- the ordinary API contract is unchanged;
- the frozen set has at least 60 valid, provenance-backed records;
- fixed-20 and expanded baselines are actually executed;
- diagnostics and metrics artifacts are reproducible and hash-addressed;
- existing tests and new tests pass, Ruff passes;
- confirmed secret count is zero;
- no Tag, RC package, production deployment, or Phase 10B tuning occurs.

After acceptance the agent stops and reports Phase 10A. It does not automatically start Phase 10B.
