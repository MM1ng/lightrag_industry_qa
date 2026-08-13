# Conversation-Aware Retrieval R2 Development Proof

## Scope

This experiment uses only `split=development` and `answerable=true` rows from `evaluation/phase10/expanded_golden_set.jsonl`. The derived dataset contains 18 natural follow-up cases from S001–S020 and D001–D016. Each `gold_chunk_ids` list is copied directly from the source row's `expected_evidence[].chunk_id`; the semantic rewrite set is not modified.

The existing deterministic `QueryRewriter` rewrote all 18 cases and matched the expected standalone queries after normalization. No production rewrite logic was changed in this round.

## Retrieval protocol

For every case the evaluator performs exactly two calls to the existing `backend.aquery_data` path:

- BEFORE: `normalize_query(dependent_query)`.
- AFTER: `history + dependent_query -> QueryRewriter -> normalize_query(standalone_query)`.

Both calls use the same `QueryOptions`: mode `naive`, `top_k=12`, `chunk_top_k=20`, rerank disabled, and the same Development KB, Generation, workspace, Qdrant backend, and embedding configuration.

Hit Recall@K is any gold chunk in top K. Evidence Recall@K is the number of unique gold chunks retrieved divided by the number of gold chunks. MRR@K is the reciprocal rank of the first gold chunk within K, or zero when none is found.

## Result

The report is intentionally `BLOCKED`. The configured Development Qdrant endpoint `http://127.0.0.1:17333` was unreachable while `LightRAGService.initialize()` checked collections. No retrieval call ran, no index was modified, and no Recall/MRR value was fabricated.

Run with the project environment after starting the configured Development Qdrant:

```powershell
$env:PYTHONPATH = 'D:\industrial_energy_agent\src'
& 'C:\Users\12189\.conda\envs\industrial-rag\python.exe' -c "import asyncio; from pathlib import Path; from scripts.evaluate_conversation_retrieval_development import evaluate_configured_staging; raise SystemExit(asyncio.run(evaluate_configured_staging(Path('evaluation/phase10/conversation_retrieval_development_report.json'))))"
```

The machine-readable artifact is `evaluation/phase10/conversation_retrieval_development_report.json`.
