# Phase 12B-2：Runtime Citation Selection

## 结论

Phase 12B-2 **FAIL**，按要求停止，不进入 Semantic Citation Judge 或其他下一阶段。

Runtime Selector 已实现并接入 KB-scoped API 的顶层 citation projection，但在冻结 Development replay 中没有改善 Citation Precision：当前 claim-level `evidence_ids` 已经覆盖全部顶层 citations，因此稳定 union 与 Baseline 相同。

这不是 Selector 删除错误，而是现有 Runtime claim mapping 没有提供足够细粒度的 Claim → Evidence 区分。

## 1. 实验边界

- Development：36 道问题、39 个答案点。
- 未读取 Validation / Holdout。
- 没有重新执行 Retrieval、Generation、Rerank 或 LLM。
- 没有使用 `supporting_actual_chunk_ids`、expected evidence 或 Golden label 作为 Selector 输入。
- Evaluation labels 只在 Selector 完成后用于评分。

## 2. Runtime 数据流审计

当前链路为：

`decision.selected`
→ Provider Context
→ `build_answer_plan()`
→ grounding candidates / `AnswerPoint.evidence_ids`
→ `_claims_for_result()` / `prune_claim_citations()`
→ `QueryResponse.citations`

审计结论：

- `decision.selected` 是回答链路中的候选 Context Evidence；
- `build_answer_plan()` 将与答案片段通过现有 grounding lexical/numeric policy 匹配的 evidence 写入 answer point；
- `_claims_for_result()` 已生成 claim-level citation mapping；
- 但 API 之前仍将 `result.citations` 全量投影为顶层 `QueryResponse.citations`；
- 于是 Context-only evidence 仍会被用户看到；
- 本阶段新增 Runtime Selector 后，顶层 citations 只从 final claim 的 `evidence_ids` 做稳定 union，未改变 claim-level mapping。

本次 API 投影接入范围为 KB-scoped query 和 candidate-generation query；旧版 `/v1/query` 保留原有兼容行为，避免在缺少 Runtime claim evidence 的旧调用方中引入额外协议变化。

## 3. 三组指标对比

| Metric | Baseline | Runtime Experiment | Oracle Upper Bound |
| --- | ---: | ---: | ---: |
| Citation Precision | 8.1667/23 = 35.51% | 8.1667/23 = 35.51% | 21/23 = 91.30% |
| Supporting Recall | 21/23 = 91.30% | 21/23 = 91.30% | 21/23 = 91.30% |
| Question Citation Accuracy | 31/33 = 93.94% | 31/33 = 93.94% | 31/33 = 93.94% |
| Expected Coverage | 21/39 = 53.85% | 21/39 = 53.85% | 21/39 = 53.85% |
| Initial Recall@10 | 39/39 = 100% | 39/39 = 100% | 39/39 = 100% |

Runtime → Oracle 的 Citation Precision 差距为 55.79 个百分点。

## 4. 额外指标

| Metric | Baseline | Runtime | Oracle |
| --- | ---: | ---: | ---: |
| Average citations / answered question | 2.7879 | 2.7879 | 0.6364 |
| Supporting citations / answered question | 0.6364 | 0.6364 | 0.6364 |
| Non-supporting citations / answered question | 2.1515 | 2.1515 | 0 |
| Questions with over-citation | 22 | 22 | 0 |
| Questions with missing citation | 2 | 2 | 2 |
| Questions with exact minimal citation | 1 | 1 | 21 |

Runtime 没有删除任何 citation：36 条 diff 中 `removed_by_runtime=0`，Oracle 相比 Runtime 额外移除了 71 条 citation。

## 5. 逐题差距

Runtime 与 Oracle 的差异主要出现在：

- S020：Oracle 额外移除 4 条；
- D011：Oracle 额外移除 4 条；
- S015、S011、S008、D014、D012、D003：各额外移除 3 条。

这些案例共同表明：Runtime claim 的 `evidence_ids` 没有区分“实际支持 claim 的 evidence”和“只是进入 Context 的 evidence”。Selector 只能忠实执行现有 mapping，不能凭空推断新的语义支持关系。

## 6. Guardrail

- supporting citation 被误删：0；
- Supporting Recall：保持 21/23；
- Question Citation Accuracy：保持 31/33；
- Expected Coverage：保持 21/39；
- Initial Recall@10：保持 39/39；
- Missing-citation 问题：未增加；
- 未通过删除全部 citation 获取 Precision。

## 7. 实现与测试

新增 `select_runtime_citations()`，仅接受：

- final claim 的 `evidence_ids`；
- response evidence 的 `evidence_id`、`citation_id`、`chunk_id` 等运行时字段。

它不读取评测目录，不接收 supporting labels，不改变 claim-level mapping；缺失 evidence ID 时安全忽略，不伪造 citation。

相关文件：

- `src/industrial_rag/citation_selection.py`
- `src/industrial_rag/api.py`
- `tests/test_phase12b2_runtime_citation_selection.py`
- `tests/test_phase12b2_runtime_citation_api.py`
- `evaluation/phase12b2/runtime_citation_diff.jsonl`
- `evaluation/phase12b2/baseline_oracle_runtime_metrics.json`

定向 Runtime 测试：11 passed。

## 8. 最终判断

Runtime Citation Selection 本身没有引入回归，但未达到 Citation Precision ≥60% 的成功标准，因此 Phase 12B-2 为 **FAIL**。

当前数据支持进入后续独立设计阶段时讨论 semantic Claim → Evidence 判断；本阶段不实现 Semantic Citation Judge，也不自动开始下一阶段。
