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

The real Development retrieval run is `READY`. The Qdrant container `ira-phase9b-qdrant-staging` is running `qdrant/qdrant:v1.13.6` on `http://127.0.0.1:17333`. The configured Generation was restored from its existing local LightRAG workspace and verified before evaluation:

- chunks: 453
- entities: 1,012
- relationships: 1,061
- all collections: green, with the expected KB and Generation payload ownership

The evaluator executed 36 real retrieval calls (18 cases × BEFORE/AFTER), with the same KB, Generation, workspace, vector backend, embedding model, and query options in both paths. No answer-quality metrics were rerun.

| Metric | BEFORE | AFTER | Delta |
| --- | ---: | ---: | ---: |
| Hit Recall@5 | 0.6111 | 0.9444 | +0.3333 |
| Evidence Recall@5 | 0.6111 | 0.9444 | +0.3333 |
| MRR@5 | 0.4491 | 0.7593 | +0.3102 |
| Hit Recall@10 | 0.7222 | 1.0000 | +0.2778 |
| Evidence Recall@10 | 0.7222 | 1.0000 | +0.2778 |
| MRR@10 | 0.4632 | 0.7662 | +0.3030 |

The rewrite validation remained 18/18 (accuracy 1.0). Ten cases improved, five were unchanged, and three regressed only in rank while retaining gold evidence in the evaluated cutoffs: `conv-d001`, `conv-d006`, and `conv-d007`. Full per-case ranks and deltas are recorded in the machine-readable report.

Run with the project environment after starting the configured Development Qdrant:

```powershell
$env:PYTHONPATH = 'D:\industrial_energy_agent\src'
& 'C:\Users\12189\.conda\envs\industrial-rag\python.exe' -c "import asyncio; from pathlib import Path; from scripts.evaluate_conversation_retrieval_development import evaluate_configured_staging; raise SystemExit(asyncio.run(evaluate_configured_staging(Path('evaluation/phase10/conversation_retrieval_development_report.json'))))"
```

The machine-readable artifact is `evaluation/phase10/conversation_retrieval_development_report.json`.
