# Phase 10B-3I-R1：Audit Artifact Repair & I0 Development Baseline Certification

## 结论

本纠正阶段完成了纯离线审计产物重建。未重新调用模型、API、Qdrant 或 HTTP 评测；未读取 Validation/Holdout；未修改问答运行时代码、Feature Flag、Grounding、Evidence Selection、Completion、Supplemental 策略或 Candidate。

`input_integrity_passed=true`，Development Expected Answer Point 动态计算为 39，Funnel 分类互斥且 `final_funnel_valid=true`。本结果是 I0 Development 基线认证，不是 52 题质量认证，因此 Phase 10C 仍不允许进入。

## 输入完整性

- I0：36 条；I1：36 条；两者 question_id 均唯一。
- split 全部为 `development`；Candidate Generation ID 唯一：`5bca792c08fcf2f7b08cbaed09b6d525`。
- request_id、Trace、response status、expected_answer_points、claims/citations/evidence 均存在。
- Development Expected Answer Point：39；Expected Evidence：39；每个 question_id+expected_point_id 只出现一次。
- Validation 记录：0；Holdout 记录：0。

## Funnel 修复

字段按保存数据来源重算：initial_recalled 来自 Trace initial_results；selected 来自 final_selected_chunks；completed 来自 completed_evidence；available_to_provider 来自 response evidence registry 与 answer_plan 的证据 identity，而非 selected=true 推断；generated/grounding_retained 来自 Grounding Audit；final_emitted 来自 retained_answer_points；citation_correct 仅对 final_emitted 点计算，并通过候选 Chunk identity 与 Generation 校验。

Funnel 结果：

- covered_final_emitted：3
- citation_wrong_evidence：21
- recalled_not_selected：3
- selected_not_available_to_provider：12
- unknown_due_to_missing_audit_data：0
- 总计：39；类别互斥；矛盾行：0；`final_funnel_valid=true`

未把未输出点标记为 `citation_correct=false`，未把 selected 自动标记为 provider 可用。

## Support / Citation 审计

`support_failure_cases.jsonl` 只记录最终实质回答中没有任何期望 Chunk 支持的点，包含 claim_text_sha256 和支持字段，不再复制 Funnel。`citation_failure_cases.jsonl` 按实质回答的问题级记录最终引用缺失或错误的案例，共 30 个问题。

I0 Development 指标中：

- False Rejection Rate：3/36 = 0.0833333333
- Question-level Citation Accuracy：3/33 = 0.0909090909
- Citation failure question count：30，等于 33 - 3
- Expected Answer-point Coverage：3/39 = 0.0769230769
- Emitted Answer-point Support Rate：3/29 = 0.1034482759
- Unsupported Emitted Answer-point Rate：26/29 = 0.8965517241
- Claim-Citation Exact Mapping Rate：198/198 = 1.0
- Evidence Panel Completeness：33/33 = 1.0
- Trace Completeness：36/36 = 1.0
- Negative Rejection Rate：空分母，值为 null（Development 无负样本）

所有指标均保存 numerator、denominator、value、included_statuses、excluded_statuses、definition_version 和 split。

## I1 触发诊断

未重新运行 I1。基于保存 Trace 的 36 条记录，H3 enabled=true、triggered=0/36，全部归为 `policy_dead_path`；没有放宽策略来人为增加触发数。因此仍不运行 Validation，不宣称 Supplemental 质量有效。Supplemental 接线的可控 mock 测试由既有 Phase 10B-3I 测试覆盖，证明实际使用新 query 字符串、H3=false 时调用次数为 0；这不等同于 I1 accepted。

## 验证与交付

- 新增 R1 离线修复脚本和 6 项审计测试。
- 全量 pytest：`732 passed, 12 skipped, 1 warning`；744 tests collected。
- Ruff：`All checks passed!`。
- Secret scan：`confirmed_secret_count=0`。
- Runtime code under test：`fb77693`；R1 artifact repair commit：`5fe1da7`。
- 报告提交和最终 delivery manifest 提交不在本报告中自引用，交付清单另行记录。

## 阶段边界

```json
{
  "phase10b3i_r1_approved": false,
  "input_integrity_passed": true,
  "final_funnel_valid": true,
  "validation_run": false,
  "holdout_used": false,
  "candidate_activated": false,
  "phase10c_allowed": false,
  "production_deployment_performed": false,
  "confirmed_secret_count": 0
}
```

本阶段完成后立即停止，等待人工验收。
