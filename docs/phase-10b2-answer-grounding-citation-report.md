# Phase 10B-2 Answer Grounding & Citation Reliability

状态：实现完成，但人工验收门禁未全部通过；不得进入 Phase 10C。

## 冻结边界

保留 Phase 10B 最终检索配置：标准化开启、`naive`、TopK=12、chunk_top_k=20、Rerank=false、Phase 10A Chunking 和 Generation 不变。未重新运行 holdout；仅分析已保存的 12 条历史 holdout 结果。

## 已实现

- `AnswerPoint`/Evidence ID 绑定模型；Evidence ID 只能来自真实 selected chunk。
- 确定性答案点校验：实体、数值、单位、条件、证据词重叠。
- unsupported 答案点删除；全部不支持时降级 `insufficient_evidence`，部分支持时返回 `partial_answer`。
- 问题类型证据政策：parameter、procedure、safety、troubleshooting、maintenance、component、condition_limit、multi_evidence。
- 引用的 document/page/chunk 检查及 Trace 中 `answer_plan` 保存。
- grounding Prompt 最小改动实验；未修改检索 Prompt 或检索配置。
- partial-evidence fallback 仅在 grounding 开启时启用，未放宽普通未启用链路。

## Development + Validation 结果（52题）

- Unsupported Answer Rate：1/40 = 2.50%，通过 ≤5%。
- 已答题 Question-level Citation Accuracy：39/40 = 97.50%，通过 ≥95%。
- Answer-point Evidence Coverage：261/368 = 70.92%，未通过 ≥95%。
- Unsupported Answer Point Rate：107/368 = 29.08%，未通过 ≤5%。
- False Rejection Rate：10/50 = 20.00%，未通过 ≤12%。
- Negative Rejection Rate：2/2 = 100%，通过。
- Wrong-page Citation Rate：0%；Wrong-chunk Citation Rate：2.50%。
- Multi-evidence Complete Coverage：34/40 = 85.00%，未达到完整覆盖目标。
- Citation Trace Completeness：52/52 = 100%，通过。

## 结论

答案过滤已显著降低最终 Unsupported Answer，且已答题引用准确率达到目标；但 validation 仍存在较高的证据缺失/误拒、答案点覆盖不足和多证据不完整问题。因此 Phase 10B-2 当前不满足完成条件，不能进入 Phase 10C。失败原因已保留在失败矩阵中，未伪造门禁通过。

## 产物

- [答案可靠性失败矩阵](../evaluation/phase10/answer_grounding_failure_matrix.jsonl)
- [失败汇总](../evaluation/phase10/answer_grounding_failure_summary.json)
- [答案点绑定 Schema](../evaluation/phase10/answer_point_binding_schema.json)
- [问题类型证据政策](../evaluation/phase10/question_type_evidence_policy.json)
- [Prompt 消融](../evaluation/phase10/prompt_ablation_results.json)
- [Partial Answer 结果](../evaluation/phase10/partial_answer_results.json)
- [Citation 校验结果](../evaluation/phase10/citation_validation_results.json)
- [最终 Grounding 指标](../evaluation/phase10/final_grounding_metrics.json)

验证：Ruff 通过；相关测试 100 passed；Secret 扫描 `confirmed_secret_count=0`。未创建 Tag、未打包 RC、未部署生产、未重新运行 Holdout。
