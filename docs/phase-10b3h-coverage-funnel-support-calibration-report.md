# Phase 10B-3H：Coverage Funnel Recovery & Support Calibration

## 结论

本阶段完成 52 题答案点漏斗审计、Support Validator、结构化 AnswerPoint 校验和受控 Supplemental Retrieval 策略，并完成一次新的 Development 36 + Validation 16 运行。Completion 传播链和负例安全性保持，但 H1/H2 没有改善质量门禁，H3 在本次运行中没有出现可合法触发的 Supplemental Gap。因此 Candidate 不激活，不进入 Phase 10C。

## 版本与数据

| 字段 | 值 |
|---|---|
| coordination_base_commit | `3f7ede0` |
| agent_a_commit | `c0f46bb37ae50dbc8ee404f8112625fb389cf005` |
| agent_b_commit | `052db1039fc05c4eb9974228ca11521003f95ed1` + `6b55a4e` |
| agent_c_commit | `4d862db5c1d703c00024d9a9db5c75265193b8a7` |
| integration_commit | 待最终集成提交 |
| code_under_test_commit | 待最终集成提交 |
| evaluation_run_commit | 待最终集成提交 |
| report_commit | 待最终报告提交 |
| final_delivery_commit | 待最终报告提交 |
| evaluation_run_id | `phase10b3h-final-52` |
| config_sha256 | `49fe065d88ee95e8dfe6457b1dd505d9b99ffcb8feda68ac1f9647a718dfcd22` |
| dataset_sha256 | `22ae671b6579fa04e270e913c648fe359c622ccbd93cfefeb76334f6668c9fa3` |
| candidate_generation_id | `5bca792c08fcf2f7b08cbaed09b6d525` |
| candidate_generation_name | `g10b3c20260803` |
| old_active_generation_id | `a2d1c77ce08b414495e9d845cc42f799` |

Development=36、Validation=16、positive=50、negative=2；Holdout 未读取。

## 漏斗审计

72 个黄金答案点的失败阶段统计：

- retrieval_missing：9
- recalled_not_selected：12
- generation_omitted：1
- generation_refusal：10
- unknown：40

外部 Unsupported/Citation disagreement 为 S006、S020、A001。审计区分了 mapping_correct_support_wrong、numeric_mismatch、condition_mismatch、object_mismatch、page_or_chunk_mismatch 和 lexical_false_positive；当前分类是离线诊断假设，不替代人工语义门禁。

## H1/H2/H3 结果

- H1 Support Validator：拒绝了部分词重叠假阳性，但整体 Citation Accuracy 和 Unsupported Answer 门禁恶化，未接受。
- H2 Structured Generation：保留了合法点、删除非法 Evidence ID，未改善 Expected Answer-point Coverage，未接受。
- H3 Supplemental Retrieval：本次 52 题没有满足“Parent/Adjacent 仍无法解决 gap”的合法触发条件，触发率为 0；策略保留为未接入主链的独立模块。

## 最终指标

- Initial Chunk Recall@20：63/72 = 87.50%
- Initial MRR：约 0.6994
- Initial Page Recall@20：50/50 = 100%
- Effective Evidence Recall：64/72 = 88.89%
- Parent Trigger：0/50
- Adjacent Trigger：9/50
- Completion Contribution：10/10 = 100%
- Completion Precision：10/10 = 100%
- False Rejection：8/50 = 16%，未达 ≤12%
- Negative Rejection：2/2 = 100%
- Unsupported Answer：9/42 = 21.43%，未达 ≤5%
- Citation Accuracy：33/42 = 78.57%，未达 ≥95%
- Expected Answer-point Coverage：36/72 = 50%，未达 ≥90%
- Claim-Citation Exact Mapping：167/168 = 99.40%，未达 100%
- Evidence Panel Completeness：42/42 = 100%
- Trace Completeness：52/52 = 100%
- Supplemental Evidence Precision：本次无合法触发，分母为 0，值为 null
- Wrong-document：0；Wrong-generation：0；fabricated Evidence ID：0

H1/H2 的结果说明仅靠更严格的支持校验和结构化解析，不能在当前证据召回/答案点覆盖不足时达到门禁；不得把这些回退结果拼接到 H0 或 10B-3G 指标中。

## 验证与门禁

- Agent A 测试：2 passed；Agent B：5 passed；Agent C：9 passed。
- 集成相关测试：43 passed。
- `confirmed_secret_count=0`。
- `final_metrics_valid=true`。
- `phase10b3h_approved=false`。
- `candidate_activated=false`。
- `phase10c_allowed=false`。
- `production_deployment_performed=false`。

产物：

- `evaluation/phase10b3h/coverage_funnel_matrix.jsonl`
- `evaluation/phase10b3h/support_disagreement_cases.jsonl`
- `evaluation/phase10b3h/supplemental_retrieval_results.jsonl`
- `evaluation/phase10b3h/structured_generation_results.jsonl`
- `evaluation/phase10b3h/final_metrics.json`
- `evaluation/phase10b3h/secret_scan.json`

本阶段完成后立即停止，等待人工验收。未创建 Tag、未打包 RC、未部署生产、未进入 Phase 10C。
