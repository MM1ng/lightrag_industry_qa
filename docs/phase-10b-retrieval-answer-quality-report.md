# Phase 10B Retrieval and Answer Quality Report

日期：2026-08-03  
状态：完成，停在 Phase 10B；未进入 Phase 10C/10D，未创建 Tag、未重新打包 RC、未部署生产。

## 范围与冻结边界

- development 36 题、validation 16 题用于诊断和调参；holdout 12 题仅在最终配置冻结后运行一次。
- Golden Set、实际 child chunk 文件路径/SHA、Generation `a2d1c77ce08b414495e9d845cc42f799` 和 Phase 10A 指标口径未修改。
- 未修改 Prompt、证据阈值、Chunking；最终 TopK=12、chunk_top_k=20。

## Task 1–6 结果

- 失败矩阵：52 题，未加载 holdout；主要确定性层为 Retrieval 11、Ranking 3、Evidence Selection 3、Refusal 3，跨页证据缺失 7 题。
- 确定性标准化：validation 保留，Chunk Recall@5 +3.03pp、Chunk Recall@20 +12.12pp、MRR +7.86pp、FRR -14.29pp；Trace completeness 52/52。
- 检索消融：development 比较 mix、naive、hybrid、TopK=8、chunk_top_k=32；选择 `naive`。validation 复核 MRR 0.5269、Chunk Recall@20 0.6970、FRR 0.3571。
- 组合 validation（标准化 + naive）：Chunk Recall@5 0.4545、Chunk Recall@20 0.6970、MRR 0.6078、FRR 0.3571、Negative Rejection 100%。
- Rerank：生产链保持关闭，返回 `rerank_applied=false`/空结果/null 分数；离线 deterministic 候选排序仅作诊断，没有伪造 provider score 或静默回退。
- 证据/拒答：未修改全局阈值；validation False Rejection 5/14，负样本拒答 2/2。
- 引用绑定：执行文档、页、Chunk 和 answer-point 支持检查；claim-level accuracy 仍标记 unavailable。

## 最终 holdout（冻结后唯一一次）

最终配置：标准化开启、`naive`、TopK=12、chunk_top_k=20、Rerank 关闭、Phase 10A Chunking/evidence/refusal 策略冻结。结果文件为 `evaluation/phase10/holdout_results.jsonl`，共 12 题，Trace completeness 100%。

- Chunk Recall@5：9/11 = 0.8182；Chunk Recall@20：10/11 = 0.9091
- Any Evidence Recall@5：8/10 = 0.8000；Complete Evidence Recall@20：9/10 = 0.9000
- MRR：0.7444；graded nDCG@10：0.7765
- False Rejection：1/10 = 0.1000；Negative Rejection：2/2 = 1.0000
- Unsupported Answer：1/9 = 0.1111；question-level citation accuracy：8/9 = 0.8889

Holdout 结果只用于最终报告，不用于调参或再次运行。

## 交付物

- [最终配置清单](../evaluation/phase10/final_config_manifest.json)
- [检索消融汇总](../evaluation/phase10/retrieval_ablation_results.json)
- [Rerank 诊断](../evaluation/phase10/rerank_results.json)
- [证据/拒答结果](../evaluation/phase10/evidence_selection_results.json)
- [引用绑定结果](../evaluation/phase10/citation_binding_results.json)
- [最终指标](../evaluation/phase10/final_metrics.json)

Phase 10B 到此停止，等待下一步授权。
