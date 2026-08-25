# Phase 10B-3D：Metric Integrity, False Rejection Diagnosis & Evidence Recovery

## 状态

本补充阶段完成了 52 题已保存响应的指标重算、状态不变量检查、False Rejection 矩阵和缺失 Chunk 分析。没有重新调用模型，没有修改 Golden Set、Chunking、Embedding、检索模式、TopK、Candidate 或 Active 指针。由于 False Rejection 和 Expected Answer-point Coverage 仍未达标，Candidate 不激活，不进入 Phase 10C。

- candidate_generation_id：`5bca792c08fcf2f7b08cbaed09b6d525`
- candidate_generation_name：`g10b3c20260803`
- old_active_generation_id：`a2d1c77ce08b414495e9d845cc42f799`
- code_under_test_commit：`cff9617`
- report_commit：本报告随本阶段提交记录
- final_delivery_commit：见最终 Git HEAD

## 指标口径修正

此前评测器只把 `success` 放入回答质量分母，漏掉了 39 道 `partial_answer`，导致 Unsupported Answer 和 Citation Accuracy 错误显示为 1/1。Phase 10B-3D 将 `success` 与 `partial_answer` 统一视为 substantive answer，将 `failed` 单独保留，不计入拒答。

状态分布：

- 正例 50：success 1，partial_answer 39，insufficient_evidence 10，safety_blocked 0，failed 0。
- 负例 2：success 0，partial_answer 0，insufficient_evidence 2，safety_blocked 0，failed 0。
- 实质回答：40 道。

修正后指标：

- False Rejection Rate：10/50 = 20%。
- Negative Rejection Rate：2/2 = 100%。
- Question-level Unsupported Answer Rate：1/40 = 2.5%。
- Question-level Citation Accuracy：39/40 = 97.5%。
- Emitted Answer-point Support Rate：271/271 = 100%。
- Unsupported Emitted Answer-point Rate：0/271 = 0%。
- Claim-Citation Exact Mapping Rate：271/271 = 100%。
- Evidence Panel Completeness：40/40 = 100%。
- Trace Completeness：52/52 = 100%。
- Expected Answer-point Coverage：42/72 = 58.33%。
- Missing Expected Answer-point Rate：30/72 = 41.67%。

Chunk Recall@20、MRR、Page Recall 等检索指标沿用 Candidate 侧边栏映射后的真实结果：Chunk Recall@20 为 63/72，MRR 为 0.6994，Page Recall@20 为 50/50。表格能力仍记录为 unsupported，分子、分母和值均为 null。

## 不变量

`evaluation/phase10b3d/metric_invariant_check.json`：全部不变量通过，`final_metrics_valid=true`。检查包括 50/2/52 分区、状态总和、实质回答分母、Claim 分母和 failed 不隐藏。此次 1/1 分母错误已消除。

## False Rejection

10 道错误拒答已逐题记录于 `evaluation/phase10b3d/false_rejection_matrix.jsonl`。根因分布：

- 9 道：`partial_evidence_misclassified_as_refusal`。其中已召回或已选择目标证据，但 Answer Plan 没有形成 supported point，最终返回拒答；这些案例不能简单归因于模型问题。
- 1 道：`expected_chunk_recalled_not_selected`。目标 Chunk 在初始结果中，但未进入最终选择。

10 道均存在可用 Parent 关系；Adjacent 关系也存在，但当前 Trace 没有记录为实际 completion，说明后续应优先修复 coverage/grounding 判定和受控 Parent/Adjacent 触发，而不是扩大 TopK。

## Chunk Recall 缺口

9 条缺失黄金 Evidence 已输出到 `evaluation/phase10b3d/missing_chunk_analysis.jsonl`。全部 sidecar 映射为 exact substring、coverage=1.0，未发现 sidecar mapping error。缺口主要表现为目标 Chunk 不在 Top20，但多数存在同文档/同页或 Parent 可补充上下文，适合后续受控 completion 实验。

## 产物

- `evaluation/phase10b3d/metric_policy.json`
- `evaluation/phase10b3d/metric_invariant_check.json`
- `evaluation/phase10b3d/recomputed_baseline_metrics.json`
- `evaluation/phase10b3d/recomputed_case_statuses.jsonl`
- `evaluation/phase10b3d/case_status_matrix.jsonl`
- `evaluation/phase10b3d/false_rejection_matrix.jsonl`
- `evaluation/phase10b3d/false_rejection_summary.json`
- `evaluation/phase10b3d/missing_chunk_analysis.jsonl`
- `evaluation/phase10b3d/experiment_results.json`

## 阶段门禁

```json
{
  "phase10b3d_approved": false,
  "phase10b3a_approved": false,
  "phase10c_allowed": false,
  "candidate_activated": false,
  "holdout_used": false,
  "production_deployment_performed": false
}
```

下一步只能在 development/validation 上进行单变量 Evidence Selection、Parent/Adjacent Completion 或 Grounding 实验；每次实验必须重新执行完整 52 题并重新计算上述口径。当前报告完成后立即停止。
