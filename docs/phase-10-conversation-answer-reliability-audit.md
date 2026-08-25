# Phase 10 — Conversation Answer Reliability Audit

Status: **DIAGNOSIS_COMPLETE**

- LightRAGService calls: `0`
- Snapshot SHA verified: `8d551a2f02e4141cf0d355c6271a17883617a0519a7b1f80534496784cec0cde`
- Candidate unsupported cases: `14 / 18`
- Candidate unsupported answer points: `26`
- Baseline unsupported cases: `15 / 18`
- Baseline unsupported answer points: `35`

## Case transitions

- unsupported -> supported: `3`
- supported -> unsupported: `2`
- unsupported -> unsupported: `12`
- supported -> supported: `1`

## Root cause distribution

| Root cause | Answer points | Cases |
|---|---:|---:|
| Retrieval Miss | 0 | 0 |
| Ranking / Truncation Loss | 0 | 0 |
| Evidence Selection Miss | 0 | 0 |
| Evidence Completion Miss | 0 | 0 |
| Generation Overreach | 0 | 0 |
| Grounding False Negative | 7 | 5 |
| Grounding False Positive | 1 | 1 |
| Citation Binding Error | 18 | 13 |
| Evaluation Artifact | 0 | 0 |
| Insufficient Evidence to Classify | 0 | 0 |

## Top 3 root causes

1. Citation Binding Error: 18 answer points / 13 cases
2. Grounding False Negative: 7 answer points / 5 cases
3. Grounding False Positive: 1 answer points / 1 cases

## Faithfulness focus cases

- `conv-s006`: faithfulness `{'baseline': 1.0, 'candidate': 0.75, 'delta': -0.25}`, unsupported points `['P3', 'P4', 'P7']`; retrieval and provider lineage were compared; unsupported points are classified in the point audit
- `conv-d005`: faithfulness `{'baseline': 1.0, 'candidate': 0.8, 'delta': -0.19999999999999996}`, unsupported points `['P6', 'P7']`; retrieval and provider lineage were compared; unsupported points are classified in the point audit
- `conv-s011`: faithfulness `{'baseline': 1.0, 'candidate': 0.8235294117647058, 'delta': -0.17647058823529416}`, unsupported points `['P7', 'P8']`; retrieval and provider lineage were compared; unsupported points are classified in the point audit
- `conv-s004`: faithfulness `{'baseline': 1.0, 'candidate': 0.8333333333333334, 'delta': -0.16666666666666663}`, unsupported points `['P3', 'P4', 'P7']`; retrieval and provider lineage were compared; unsupported points are classified in the point audit
- `conv-d004`: faithfulness `{'baseline': 1.0, 'candidate': 0.8333333333333334, 'delta': -0.16666666666666663}`, unsupported points `['P10']`; retrieval and provider lineage were compared; unsupported points are classified in the point audit

## Decision

唯一下一阶段生产变量：**Citation Binding Correction**。

本报告仅审计冻结快照；没有重跑 RAG、重新生成回答或访问 Validation/Holdout。
