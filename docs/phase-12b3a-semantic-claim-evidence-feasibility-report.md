# Phase 12B-3A Semantic Claim-Evidence Feasibility Report

## 结论

**Phase 12B-3A：FAIL（实验输入不足，安全阻断）**

本阶段没有得到可用于判断 Semantic Judge 效果的有效实验结果。原因不是 Semantic Judge 已经证明无效，而是当前保存的 Runtime artifact 没有保存候选 evidence 文本：

- 36 条 Development answer 全部被检查；
- 33 条有 Runtime candidate evidence，但 `response.evidence[*].excerpt` 为空；
- 3 条没有完整的 Runtime claim/evidence candidate matrix；
- LLM 调用数为 `0`；
- 没有使用 Golden、expected evidence、supporting labels、Validation 或 Holdout 信息作为 Judge 输入。

在没有 Claim 和 Evidence 文本的情况下继续调用 LLM，或者从 Golden / 原始 PDF 回填文本，都会违反本阶段的实验边界。因此 Replay 选择 fail-closed，所有判断标为 `uncertain`，并停止在离线 feasibility 阶段。

## 数据范围与冻结边界

使用的数据：

- `evaluation/phase10b3i/i0_development_results.jsonl`：36 条 Development answer、claim、Runtime evidence 和已保存 trace 元数据；
- `evaluation/phase10b3i_r2/coverage_funnel_matrix.jsonl`：仅在 Judge 完成后用于离线评分；
- Phase 12B-1 / 12B-2 指标和 diff：仅用于 Baseline、Runtime Lexical、Oracle 对照。

未使用：

- Validation / Holdout；
- Golden expected evidence；
- `supporting_actual_chunk_ids` 作为 Judge 输入；
- 原始 PDF 回读或重新 Retrieval；
- 重新 Generation、Rerank、Context assembly。

## 实现内容

新增了独立的 Runtime-only Semantic Judge 基础模块和离线 Replay：

- 允许输入：final claim、claim text、Runtime candidate evidence、evidence text、chunk metadata；
- 禁止输入：Golden、expected、supporting、oracle、evaluation label；
- 支持 `supported`、`partially_supported`、`not_supported`、`uncertain`；
- 只将 `supported` 作为第一轮 citation 候选；
- `partially_supported` 不自动推断为完整支持；
- 非法 JSON 安全降级为 `uncertain`；
- evidence 文本缺失时在 LLM 调用前阻断；
- 空 candidate matrix 不调用 LLM。

没有修改线上 Runtime API、Citation Selector、Retrieval、Context、Generation、Grounding、Refusal、Rerank、Prompt 主体或评测数据。

## 四组结果

| Version | Citation Precision | 状态 |
|---|---:|---|
| Baseline | 35.51% | 已冻结 |
| Runtime Lexical | 35.51% | 已冻结 |
| Semantic Judge | 无有效结果；fail-closed 投影为 0/23 | 输入阻断，不能解释为模型性能 |
| Oracle Upper Bound | 91.30% | Phase 12B-1 标签审计上界 |

Baseline → Runtime Lexical 提升为 `0` 个百分点。

由于 Semantic Judge 没有合法输入，本阶段不能计算有效的 Semantic → Oracle gap，也不能据此评价 Semantic Judge 的真实精度。Fail-closed 投影的 guardrail 结果为失败，是为了明确阻止误用空证据结果，不是 Semantic Judge 的有效测量结果。

## Fail-closed Replay 统计

| 项目 | 结果 |
|---|---:|
| Runtime evidence 文本缺失 | 33 条 |
| Runtime candidate matrix 为空 | 3 条 |
| LLM 调用 | 0 |
| Semantic judgement = uncertain | 566 条 claim-evidence 对 |
| supported / partially_supported / not_supported | 0 / 0 / 0 |
| 有效 Semantic deletion cases | 0 |
| 有效错误/uncertain review cases | 仅记录阻断样本 |

这 566 条 `uncertain` 不能作为 Semantic 与 Oracle 的误判率，只能说明当前 artifact 无法完成判断。

## Top Case Review

本阶段要求的 5 个“正确删除 over-citation”案例无法合法产生，因为没有任何一条进入有效 Semantic Judge 调用。

已记录 5 个阻断样本，集中表现为：

- Claim 文本存在；
- evidence ID、文档、页码、chunk ID 存在；
- evidence excerpt 为空；
- 判断结果只能是 `uncertain`。

因此无法可靠分析数值条件、单位、否定、表格、cross-page、procedure 或 safety warning 等语义错误类型。

## 关键产物

- [Semantic Judge 实现](../src/industrial_rag/semantic_judge.py)
- [Phase 12B-3A Replay](../scripts/phase12b3a_run_replay.py)
- [Semantic Judge diff](../evaluation/phase12b3a/semantic_judge_diff.jsonl)
- [Question citation summary](../evaluation/phase12b3a/question_citation_summary.jsonl)
- [Semantic Judge metrics](../evaluation/phase12b3a/semantic_judge_metrics.json)
- [Top Case review](../evaluation/phase12b3a/top_case_review.json)

## 测试结果

- Phase 12B-3A 定向测试：`11 passed`；
- Python compile：通过；
- Oracle leakage 和 evaluation dependency 检查：通过；
- 全量测试：`803 passed, 12 skipped, 1 warning`；
- warning 为现有 Starlette/httpx deprecation warning。

## 后续边界

本阶段不接入线上 API，也不继续增加模型、第二套算法或修改 Prompt。

如果未来要重新执行 Phase 12B-3A，必须先在不携带评测真值的前提下，重新产生包含 Runtime candidate evidence 文本的离线 artifact；仅靠当前 trace 中的 ID、页码和 chunk metadata 不足以做 Semantic Claim-Evidence 判断。当前阶段到此停止。
