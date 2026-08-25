# Phase 10B-3E：Grounding Recovery & Effective Evidence Completion

## 结论

以下“Replay 结果”保留了最初 10B-3E 阻塞时的历史记录；随后通过 10B-3F 补齐审计输入并重新执行 Replay，已通过门禁并完成 E1、E2、E3、E4 及一次完整 52 题评测。当前结论以文末“10B-3F 门禁通过后的最终执行结果”为准；质量门禁仍未通过，因此 Candidate 保持未激活，不进入 Phase 10C。

- candidate_generation_id：`5bca792c08fcf2f7b08cbaed09b6d525`
- candidate_generation_name：`g10b3c20260803`
- old_active_generation_id：`a2d1c77ce08b414495e9d845cc42f799`
- code_under_test_commit：`f1cd855`
- report_commit：本阶段提交记录
- final_delivery_commit：见最终 Git HEAD

## Replay 结果

Replay 只读取 development 36 题和 validation 16 题的已保存 response、selected evidence、Trace、Parent/Adjacent 注册信息；没有调用模型，没有读取 Holdout，没有用黄金答案合成新的回答。

- total：52
- positive：50
- negative：2
- 可重放的实质回答：40
- 不可重放：12（10 道拒答及 2 道负例）
- False Rejection 候选：10
- 恢复的 False Rejection：0
- unsupported emitted point：0
- Replay gate：`false`

关键事实：9 道 False Rejection 的 `response.answer` 均为“手册中未检索到充分依据，无法可靠回答该问题”，Trace 也没有保存原始模型答案或被删除的句子。根据本阶段规则，不能用 Golden Set 的 expected answer points 伪造新答案，因此这些案例只能标记为不可重放。

## 实验状态

| 实验 | 状态 | 说明 |
| --- | --- | --- |
| Replay baseline | 已完成 | 52 题保存结果的确定性重放 |
| E1 Grounding/状态决策 | 未执行 | Replay gate 未通过 |
| E2 Evidence Selection | 未执行 | Replay gate 未通过 |
| E3 Parent Completion | 未执行 | Replay gate 未通过 |
| E4 Adjacent Completion | 未执行 | Replay gate 未通过 |
| 新 52 题真实评测 | 未执行 | 禁止绕过 Replay gate |

初始检索指标没有被重新计算为 Completion 指标；Effective Evidence Recall、Completion Contribution Rate 和 Completion Evidence Precision 均记录为未测量，而非伪造数值。

## 阶段门禁

```json
{
  "phase10b3e_approved": false,
  "phase10b3a_approved": false,
  "phase10c_allowed": false,
  "candidate_activated": false,
  "holdout_used": false,
  "production_deployment_performed": false
}
```

## 产物

- `evaluation/phase10b3e/replay_baseline.jsonl`
- `evaluation/phase10b3e/replay_experiments.json`
- `evaluation/phase10b3e/replay_metric_comparison.json`
- `evaluation/phase10b3e/grounding_recovery_results.json`
- `evaluation/phase10b3e/evidence_selection_results.json`
- `evaluation/phase10b3e/parent_completion_results.json`
- `evaluation/phase10b3e/adjacent_completion_results.json`
- `evaluation/phase10b3e/experiment_results.json`
- `evaluation/phase10b3e/effective_evidence_metrics.json`
- `evaluation/phase10b3e/secret_scan.json`

## 后续必要条件

要继续 E1，必须先让一次完整 Candidate 查询持久化 Grounding 前的原始模型答案、分句结果和被删除的 Answer Point，同时保持同一检索配置、同一 Candidate 和同一 52 题集合。补齐该可审计输入后，应重新执行完整 Replay；在 Replay 明显恢复 9 道误拒答且不增加 unsupported emitted point 之前，不得进行真实 52 题重跑。

本阶段完成后立即停止，等待人工验收。

## 10B-3F 门禁通过后的最终执行结果

10B-3F 已补齐审计输入并通过 Replay 门禁，因此按授权继续执行了 E1、E2、E3、E4 和一次完整的 development 36 + validation 16 真实 Candidate 评测。以下结果覆盖本报告前面的阻塞结论。

- 最终状态分布：success=2，partial_answer=37，insufficient_evidence=13（正例 11、负例 2）。
- False Rejection：11/50 = 22%，未达到 ≤12%。
- Negative Rejection：2/2 = 100%。
- Question-level Unsupported Answer：2/39 = 5.13%，略高于 ≤5% 门禁。
- Question-level Citation Accuracy：37/39 = 94.87%，未达到 ≥95%。
- Expected Answer-point Coverage：40/72 = 55.56%。
- Effective Evidence Recall after Completion：64/72 = 88.89%。
- Parent Completion Trigger Rate：50/50 = 100%。
- Adjacent Completion Trigger Rate：50/50 = 100%。
- Completion Evidence Precision：104/104 = 100%。
- Completion Contribution Rate：0/104 = 0%，补全证据尚未被确定性地绑定到输出 Claim。
- Completion Wrong-document：0；Wrong-generation：0。
- Trace Completeness：52/52 = 100%。

初始检索配置保持不变，初始基线仍以 Phase 10B-3D 冻结值 Chunk Recall@20=63/72、MRR≈0.6994、Page Recall@20=50/50 为准；补全证据没有计入 Initial Recall 或 Initial MRR。由于质量门禁失败，`phase10b3e_approved=false`，Candidate 不激活，不进入 Phase 10C。
