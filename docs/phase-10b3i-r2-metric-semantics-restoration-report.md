# Phase 10B-3I-R2：Metric Semantics Restoration & Supplemental Dead-Path Proof

## 结论

R2 完成了纯离线指标语义恢复与 Supplemental dead-path 证明。本阶段没有调用模型、真实 API、Qdrant 或 HTTP 评测，没有重新运行 I0/I1，没有读取 Validation/Holdout，没有修改问答运行时代码、Feature Flag、Candidate 或 Golden Set。

I0 基线的指标定义已恢复为 Phase 10B-3D policy；结果仍未达到阶段批准条件，Phase 10C 不允许进入。

## 指标语义恢复

使用并保存了 `evaluation/phase10b3d/metric_policy.json`（`phase10b3d-metric-policy-v1`），没有创建替代定义：

- Identity Resolution：198/198 = 1.0；只表示 Evidence/Citation identity 可解析。
- Supporting Citation Recall：21/23 = 0.913043。
- Citation Precision：8.1667/23 = 0.355072。
- Overcitation Rate：20/23 = 0.869565。
- Claim Semantic Support：21/23 = 0.913043。
- Expected Answer-point Coverage：21/39 = 0.538462，其中 `covered_exact_citation=1`、`covered_with_overcitation=20`。
- Question-level Unsupported Answer Rate：12/33 = 0.363636。
- Question-level Citation Accuracy：31/33 = 0.939394。
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
- 全量 pytest：`738 passed, 12 skipped, 2 warnings`；750 tests collected。两条 warning 为 Starlette/httpx 弃用提示和 Phase 9 测试线程退出时的 aiosqlite Event Loop 警告；Ruff 通过；Secret scan `confirmed_secret_count=0`。

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

## 附录 A：LLM 可独立审核的证据包

以下内容是本报告对应的机器可读证据摘要。审核者不需要依赖外部文件才能复核结论；外部 JSONL 仅用于逐条追溯。

### A.1 输入完整性证明

```json
{
  "input_integrity_passed": true,
  "i0_record_count": 36,
  "i1_record_count": 36,
  "i0_question_id_unique": true,
  "i1_question_id_unique": true,
  "splits": ["development"],
  "candidate_generation_ids": ["5bca792c08fcf2f7b08cbaed09b6d525"],
  "request_id_present": true,
  "trace_present": true,
  "response_status_present": true,
  "answer_points_present": true,
  "claims_citations_evidence_present": true,
  "expected_point_unique": true,
  "validation_records": 0,
  "holdout_records": 0,
  "issues": []
}
```

### A.2 冻结指标 policy 证明

```json
{
  "definition_version": "phase10b3d-metric-policy-v1",
  "substantive_statuses": ["success", "partial_answer"],
  "refusal_statuses": ["insufficient_evidence", "safety_blocked"],
  "failed_status": "failed",
  "positive_count": 50,
  "negative_count": 2,
  "source_path": "evaluation/phase10b3d/metric_policy.json",
  "source_sha256": "8850674f37430afece9088b53e9f1eaa874b831d341db04bc42e648b55bcd03d",
  "restored_definition_version": "phase10b3d-metric-policy-v1"
}
```

### A.3 I0 Development 指标完整值

每项均为 `numerator / denominator = value`；空分母按 policy 返回 null。

| 指标 | Numerator | Denominator | Value |
|---|---:|---:|---:|
| claim_evidence_identity_resolution_rate | 198 | 198 | 1.000000 |
| supporting_citation_recall | 21 | 23 | 0.913043 |
| citation_precision | 8.166667 | 23 | 0.355072 |
| overcitation_rate | 20 | 23 | 0.869565 |
| claim_semantic_support | 21 | 23 | 0.913043 |
| false_rejection_rate | 3 | 36 | 0.083333 |
| question_level_unsupported_answer_rate | 12 | 33 | 0.363636 |
| question_level_citation_accuracy | 31 | 33 | 0.939394 |
| expected_answer_point_coverage | 21 | 39 | 0.538462 |
| citation_trace_completeness | 36 | 36 | 1.000000 |

状态和分母：I0 共 36 条，positive=36、negative=0；`success=3`、`partial_answer=30`、`insufficient_evidence=3`。实质回答分母为 33，最终输出点分母为 23，Expected Point 分母为 39。

### A.4 Funnel 不变量证明

```json
{
  "development_expected_point_count": 39,
  "counts_sum_point_count": true,
  "unique_points": true,
  "unknown_due_to_missing_audit_data": 0,
  "covered_exact_citation": 1,
  "covered_with_overcitation": 20,
  "coverage_numerator": 21,
  "no_validation": true,
  "no_holdout": true,
  "final_funnel_valid": true,
  "stage_counts": {
    "covered_exact_citation": 1,
    "covered_with_overcitation": 20,
    "emitted_without_supporting_citation": 1,
    "generation_omitted": 6,
    "generation_refusal": 3,
    "grounding_false_negative": 5,
    "recalled_not_selected": 3
  }
}
```

Coverage 计算明确为：`covered_exact_citation + covered_with_overcitation = 1 + 20 = 21`，不要求实际 Citation Chunk 集合与黄金集合完全相等。

### A.5 Provider lineage 与生成阶段证明

逐点 39 条的分布如下：

```json
{
  "provider_evidence_ids_source": {"trace.final_selected_chunks_pre_generation": 39},
  "provider_evidence_identity_resolved": {"true": 39},
  "generation_invoked": {"true": 39},
  "raw_answer_nonempty": {"true": 39},
  "generation_returned_refusal": {"false": 36, "true": 3},
  "expected_point_present_in_raw_answer": {"true": 28, "false": 11},
  "expected_point_present_after_grounding": {"true": 29, "false": 10},
  "expected_point_final_emitted": {"true": 23, "false": 16},
  "semantic_support": {"true": 24, "ambiguous_needs_human_review": 15}
}
```

`trace.final_selected_chunks_pre_generation` 是冻结 Trace 中生成前的选择记录，不是最终 answer_plan、Citation 或 Evidence Panel。若该字段缺失，R2 逻辑会输出 unknown，而不会反推。

### A.6 Citation / Support 证明

最终输出点 23 条的 Citation 分类：

```json
{
  "exact_support": 1,
  "supported_with_overcitation": 20,
  "only_wrong_citations": 2,
  "unresolved_citation": 0,
  "wrong_generation": 0
}
```

Support Failure：`case_count=2`，来源为 Claim 文本、Expected Evidence 文本和 Candidate Context Registry 的语义审计；七类语义字段均非 null，取值限定为 `true`、`false`、`not_applicable` 或 `ambiguous_needs_human_review`。

Citation Failure：`case_count=2`，分类为 `only_wrong_citations=2`。没有把“存在一个正确 Chunk 但附带额外引用”错误归为全错；这类点进入 `supported_with_overcitation`。

### A.7 I1 Supplemental dead-path 证明

```json
{
  "record_count": 36,
  "triggered": 0,
  "trigger_eligible_count": 0,
  "reason_counts": {
    "missing_trace_field:coverage_before_blocks_missing_gap_predicate": 36
  },
  "runtime_predicate": "coverage_before missing in persisted trace prevents evaluating missing-gap gate; coverage_after missing prevents parent_adjacent_resolved gate",
  "validation_run": false,
  "holdout_used": false
}
```

因此没有把 `trigger_eligible=true, triggered=false` 当作有效诊断，也没有为了产生触发而放宽策略。I1 仍不 accepted，不运行 Validation。

### A.8 交付与安全证明

```json
{
  "confirmed_secret_count": 0,
  "validation_used": false,
  "holdout_used": false,
  "candidate_activated": false,
  "phase10c_allowed": false,
  "production_deployment_performed": false,
  "pytest": "738 passed, 12 skipped, 2 warnings",
  "tests_collected": 750,
  "ruff": "All checks passed!"
}
```

两条测试 warning 为 Starlette/httpx 弃用提示和 Phase 9 测试线程退出时的 aiosqlite Event Loop 警告；没有失败或错误测试。
