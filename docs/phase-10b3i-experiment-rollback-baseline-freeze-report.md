# Phase 10B-3I：Experiment Rollback, Baseline Freeze & Supplemental Wiring Repair

## 结论

本阶段已完成被拒绝实验的 fail-closed 隔离、可信基线 I0 Development 运行和 I1 Supplemental Wiring Development 运行。I1 触发数为 0，未满足进入 Validation 的门禁，因此本阶段在 Development 阶段停止；没有运行新的 Validation，没有拼接旧结果，Candidate 保持未激活。

## 版本与运行清单

- `code_under_test_commit`：`fb77693`
- `evaluation_run_id`：`phase10b3i-i0-i1-development`
- `config_sha256`：`27d86a040d7724b71703212c9671cb681e6ed2f9bfa048aa9446a4dc205e232e`
- `dataset_sha256`：`22ae671b6579fa04e270e913c648fe359c622ccbd93cfefeb76334f6668c9fa3`
- Candidate：`5bca792c08fcf2f7b08cbaed09b6d525` / `g10b3c20260803`
- old Active Generation：`a2d1c77ce08b414495e9d845cc42f799`
- Holdout：未读取

最终提交链：Agent A=`b537b7e`，Agent B=`00a7619`，主接线=`fb77693`；评测清单提交为 `fe21431`。报告提交和交付提交不在报告自身内容中自引用，见最终交付清单和终端汇报。

## 实验回退

三个实验 Flag 均显式默认关闭：

- `QA_SUPPORT_VALIDATOR_V2_ENABLED=false`
- `QA_STRUCTURED_GENERATION_ENABLED=false`
- `QA_SUPPLEMENTAL_RETRIEVAL_ENABLED=false`

H1 关闭时不调用 `validate_answer_points()`；H2 关闭时不运行结构化二次校验；H3 关闭时不执行第二次 Qdrant 查询。Flag 状态进入 Settings、`/version`、Retrieval Trace `retrieval_config` 和 config SHA；不进入普通 QueryResponse。

## I0 安全基线

I0 使用 Development 36 题，所有 10B-3I 实验 Flag 为 false；36/36 请求完成。状态分布：success=3、partial_answer=30、insufficient_evidence=3。Trace 中记录的三项 Flag 均为 false，Supplemental 二次检索次数为 0。Candidate、Active 指针、Initial TopK、Embedding、模型和 Grounding Audit 保持不变。

## I1 Supplemental Wiring

I1 仅使用 Development 36 题，H1=false、H2=false、H3=true，36/36 请求完成，但 Supplemental Trigger=0/36。由于没有满足确定性 Coverage Gap，无法证明真实 Supplemental Query 已发送；因此：

- 不宣称 H3 有效；
- 不运行新的 Validation；
- 不运行最终 52 题；
- H3 回退为当前产品基线中的默认关闭状态。

代码已修正为实际使用 `supplemental_query.question`，Supplemental Query SHA 只计算查询字符串，并单独记录候选、接受和拒绝结果；但本次无触发，真实 backend query wiring 没有被运行时观测验证。

## Funnel 与 Support 审计

当前仅有 Development 36 题的新运行数据，因此漏斗审计产生 39 个可映射答案点，不能伪造为 72 个。`coverage_funnel_invariants.json` 明确记录：`point_count_72=false`、`final_funnel_valid=false`、`unknown_count=0`。由于 I1 Trigger=0，按照门禁停止，未读取 Validation 逐题数据来填补剩余 33 个答案点。

这不是质量通过：完整 72-point Funnel 门禁未满足，所有后续质量实验被阻断。

## 阶段状态

```json
{
  "phase10b3i_approved": false,
  "final_funnel_valid": false,
  "i1_accepted": false,
  "candidate_activated": false,
  "phase10c_allowed": false,
  "production_deployment_performed": false,
  "confirmed_secret_count": 0,
  "holdout_used": false
}
```

## 验证

- I0 Development：36/36 completed。
- I1 Development：36/36 completed。
- Agent A/B 聚焦测试：分别通过。
- 集成相关测试：40 passed，1 warning。
- Secret scan：`confirmed_secret_count=0`。
- 全量 pytest：`726 passed, 12 skipped, 1 warning`（738 tests collected）。
- Ruff：`All checks passed!`。
- 跳过项均为显式 opt-in 的 MinerU、DashScope/Qdrant 真实集成测试；未因此宣称真实外部 E2E 通过。

产物：

- `evaluation/phase10b3i/baseline_commit_analysis.json`
- `evaluation/phase10b3i/feature_flag_proof.json`
- `evaluation/phase10b3i/i0_baseline_results.json`
- `evaluation/phase10b3i/i1_supplemental_results.json`
- `evaluation/phase10b3i/coverage_funnel_matrix.jsonl`
- `evaluation/phase10b3i/coverage_funnel_summary.json`
- `evaluation/phase10b3i/coverage_funnel_invariants.json`
- `evaluation/phase10b3i/support_failure_cases.jsonl`
- `evaluation/phase10b3i/citation_failure_cases.jsonl`
- `evaluation/phase10b3i/evaluation_manifest.json`
- `evaluation/phase10b3i/secret_scan.json`

本阶段结束后立即停止，等待人工验收。未创建 Tag、未打包 RC、未部署生产、未进入 Phase 10C。
