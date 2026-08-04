# Phase 10B-3I-R2：Metric Semantics Restoration & Supplemental Dead-Path Proof

## 结论

R2 完成了纯离线指标语义恢复与 Supplemental dead-path 证明。本阶段没有调用模型、真实 API、Qdrant 或 HTTP 评测，没有重新运行 I0/I1，没有读取 Validation/Holdout，没有修改问答运行时代码、Feature Flag、Candidate 或 Golden Set。

I0 基线的指标定义已恢复为 Phase 10B-3D policy；结果仍未达到阶段批准条件，Phase 10C 不允许进入。

## 指标语义恢复

使用并保存了 `evaluation/phase10b3d/metric_policy.json`（`phase10b3d-metric-policy-v1`），没有创建替代定义：

- Identity Resolution：198/198 = 1.0；只表示 Evidence/Citation identity 可解析。
- Supporting Citation Recall：24/29 = 0.827586。
- Citation Precision：10.4167/29 = 0.359195。
- Overcitation Rate：21/29 = 0.724138。
- Claim Semantic Support：24/29 = 0.827586。
- Expected Answer-point Coverage：24/39 = 0.615385，其中 `covered_exact_citation=3`、`covered_with_overcitation=21`。
- Question-level Unsupported Answer Rate：11/33 = 0.333333。
- Question-level Citation Accuracy：28/33 = 0.848485。
- False Rejection Rate：3/36 = 0.083333。
- Citation Trace Completeness：36/36 = 1.0。

包含额外无关引用但至少有一条正确引用的点归入 `covered_with_overcitation`，仍进入 Coverage 分子，不再要求实际引用 Chunk 集合与黄金集合完全相等。

## Provider Evidence Lineage

`available_to_provider` 不再使用最终 `answer_plan`、最终 citations 或 Evidence Panel 反推。冻结 Trace 中没有 `provider_evidence_ids` 或 Grounding Audit Provider Context 字段，因此按 Phase 10B-3D 优先级使用 `trace.final_selected_chunks_pre_generation` 与 `completed_evidence` 作为生成前记录；每条记录保存：

- `provider_evidence_ids_source`；
- `provider_evidence_identity_resolved`；
- `generation_invoked`；
- `raw_answer_nonempty`；
- `generation_returned_refusal`。

若这些生成前字段缺失，分类会进入 `unknown_due_to_missing_audit_data`，不会用最终答案补猜。

## Expected Point 生成与 Citation 审计

每个点拆分记录：

- raw answer 是否非空；
- Expected Point 是否在 raw answer 中确定性出现；
- 是否在 grounding 后保留；
- 是否最终输出。

Citation 审计保存 expected/actual/supporting/unsupported Chunk、precision、recall、overcitation、wrong_generation、unresolved ID 和分类。Support Failure 读取 Claim、Expected Evidence 和 Candidate Context Registry 内容，字段使用 `true`、`false`、`not_applicable` 或 `ambiguous_needs_human_review`，不再全部为 null。

## I1 Supplemental Dead Path

没有重新运行 I1。36/36 条保存 Trace 的 `coverage_before` 与 `coverage_after` 均缺失，因此不能证明 `missing` 或 `parent_adjacent_resolved` 谓词；此前把全部记录标为 `trigger_eligible=true` 是错误的。R2 改为：

- `trigger_eligible=0/36`；
- `triggered=0/36`；
- 具体阻断：`missing_trace_field:coverage_before_blocks_missing_gap_predicate`；
- 运行时谓词定位：Supplemental policy 的 coverage-gap gate 无法由现有 Trace 证明，随后 parent/adjacent resolved gate 也无法评估。

未放宽策略、未人为增加触发数，也未将接线测试成功写成 I1 accepted。

## 不变量与验证

- Development Expected Point：39；唯一、类别和为 39。
- `unknown_due_to_missing_audit_data=0`；`final_funnel_valid=true`。
- Validation：未运行；Holdout：未读取。
- Candidate：未激活；Phase 10C：不允许。
- R2 定向测试：6 passed。
- 全量测试基线仍为前一轮已验证的 `732 passed, 12 skipped, 1 warning`；Ruff 通过；Secret scan `confirmed_secret_count=0`。

## 阶段状态

```json
{
  "phase10b3i_r2_approved": false,
  "final_funnel_valid": true,
  "i0_baseline_certified": false,
  "validation_run": false,
  "holdout_used": false,
  "candidate_activated": false,
  "phase10c_allowed": false,
  "production_deployment_performed": false,
  "confirmed_secret_count": 0
}
```

本阶段完成后立即停止，等待人工验收。
