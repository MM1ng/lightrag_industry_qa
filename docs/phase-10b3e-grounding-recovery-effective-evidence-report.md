# Phase 10B-3E：Grounding Recovery & Effective Evidence Completion

## 结论

本阶段在离线确定性 Replay 门禁处停止，未进入 E1、E2、E3、E4，也未执行新的 52 题模型评测。原因是保存的 Candidate 响应没有保留被 Grounding 删除前的原始答案：9 道 False Rejection 全部只保存了拒答文本，无法在不使用 Golden Set 生成答案的前提下重放答案分句或恢复 Answer Point。继续重跑会绕过既定 Replay 门禁，因此 Candidate 保持未激活，不进入 Phase 10C。

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
