# Phase R3-M — Ragas Evaluation Framework Migration

Status: `MIGRATION_PASS`

This report records the evaluation-only migration of Conversation Retrieval Development to Ragas. It does not change retrieval, query rewriting, prompts, evidence selection, grounding, citations, gold data, or thresholds. Validation and Holdout were not accessed.

## Audit and inputs

- Baseline HEAD: `d1ddf0024d8b72e3343b770f28db63c775b11f49`
- Python: `3.11.15` in Conda environment `industrial-rag`
- Ragas: `0.3.9` (evaluation-only; declared in `pyproject.toml` optional dependency `evaluation` and `requirements-evaluation.txt`)
- Development dataset: `data/evaluation/conversation_retrieval_development.jsonl`
- Cases: `18`, in the source order; Validation/Holdout guard passed
- Raw dataset SHA-256: `d326aff3a96a025c79a36a6566e37015419c3c7ab9763a783b7da2963bde4094`
- Baseline-commit raw SHA-256: `d326aff3a96a025c79a36a6566e37015419c3c7ab9763a783b7da2963bde4094`
- Fingerprint parity: `true`
- Canonical historical report was read but not regenerated or overwritten.

Ragas uses `Dataset`, `@experiment`, `Experiment`, official `IDBasedContextRecall` / `IDBasedContextPrecision` smoke scoring, and deterministic custom `MetricResult` metrics for the historical Hit Recall@K, Evidence Recall@K, and MRR@K definitions. No LLM semantic metric was used.

## Parity result

The real staging backend used the same KB, Generation, Qdrant, workspace, embedding, query options, and naive retrieval mode as the canonical run. The Ragas experiment persisted 18 rows under `evaluation/ragas/experiments/`.

| Metric | Canonical before | Ragas before | Canonical after | Ragas after |
|---|---:|---:|---:|---:|
| Hit Recall@5 | 0.6111111111111112 | 0.6111111111111112 | 0.9444444444444444 | 0.9444444444444444 |
| Hit Recall@10 | 0.7222222222222222 | 0.7222222222222222 | 1.0 | 1.0 |
| MRR@5 | 0.449074074074074 | 0.449074074074074 | 0.7592592592592593 | 0.7592592592592593 |
| MRR@10 | 0.46318342151675485 | 0.46318342151675485 | 0.7662037037037037 | 0.7662037037037037 |

All six canonical metrics (including Evidence Recall@5/@10) matched with tolerance `1e-9`; all recorded absolute deltas are `0.0`. Denominator parity and case classification parity both passed. There are no metric mismatches.

- Improved: `conv-s001, conv-s002, conv-s003, conv-s004, conv-s005, conv-s007, conv-s008, conv-s010, conv-s011, conv-d002`
- Unchanged: `conv-s006, conv-s009, conv-d003, conv-d004, conv-d005`
- Regressed: `conv-d001, conv-d006, conv-d007`

## Verification and frozen components

- Migration contract tests: passed
- Relevant conversation retrieval tests: passed
- Full `pytest`: `931 passed, 12 skipped, 1 warning`
- Ruff: passed; `pip check`: passed; production import scan: passed
- Production `src/industrial_rag` contains no `ragas` import.
- Frozen legacy runner: `scripts/evaluate_conversation_retrieval_development.py`
- Frozen canonical metric definitions: `src/industrial_rag/conversation/retrieval_evaluation.py`
- Historical artifact reader and report: preserved

Machine-readable details are in `evaluation/phase10/ragas_migration_development_report.json`; the Ragas experiment rows are in `evaluation/ragas/experiments/`.
