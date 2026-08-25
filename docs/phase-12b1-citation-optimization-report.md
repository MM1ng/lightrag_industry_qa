# Phase 12B-1：Citation Precision Optimization

## 结论

Phase 12B-1 Experiment A **PASS**。

本阶段采用已保存 Development 答案、Retrieval Trace 和 Phase 10B-3I-R2 citation audit 做离线 Citation remapping，没有重新执行 Retrieval、Generation、Rerank 或 Prompt，也没有读取 Validation/Holdout。

实验只改变 Citation Selection / Mapping，不改变答案文本、Grounding、拒答、Context 或 Retrieval。

## 1. Baseline 冻结

| Metric | Baseline |
| --- | ---: |
| Citation Precision | 8.166666666666664 / 23 = 0.35507246376811585 |
| Supporting Recall | 21 / 23 = 0.9130434782608695 |
| Question Citation Accuracy | 31 / 33 = 0.9393939393939394 |
| Expected Coverage | 21 / 39 = 0.5384615384615384 |
| Initial Recall@10 | 39 / 39 = 1.0 |

Baseline artifact 未修改。

## 2. Citation Failure Audit

审计范围为 Phase 12A 的 19 道 `citation_failure`：

| Subtype | Count |
| --- | ---: |
| over_citation | 18 |
| wrong_citation | 1 |
| missing_citation | 0 |
| citation_scope_mismatch | 0 confirmed |
| duplicate_citation | 0 confirmed |
| parent_child_overcitation | 0 confirmed |
| unknown | 0 |

详细记录：`evaluation/phase12b1/citation_failure_audit.jsonl`。

`wrong_citation` 是 S008；其余主要是 supporting citation 存在，同时携带了不支持当前 claim 的 citation。

## 3. 当前 Citation Mapping 链路

1. Retrieval 和 evidence selection 形成 `decision.selected`。
2. Provider context 由 selected、completion 和 supplemental evidence 组成；这些证据首先服务于回答生成。
3. `build_answer_plan()` 对答案片段和 grounding candidates 做 lexical token、数字和单位匹配，生成 answer point 的 `evidence_ids`。
4. `result.citations` 默认来自 `decision.selected`，因此最终 context 中的 selected evidence 会进入顶层 citations。
5. API 的 `_claims_for_result()` 会按 claim 的 `evidence_ids` 生成 claim-level citation IDs，并调用已有 `prune_claim_citations()`；但顶层 `QueryResponse.citations` 没有同步收缩到 claim 实际需要的最小集合。
6. 因此当前存在 Context Evidence 与 Citation Evidence 未完全区分的问题：claim mapping 有局部约束，顶层 citation 列表仍可能携带无关 evidence。

当前链路特征：

- 存在 claim-level citation；
- 同一回答的顶层 citations 仍是 answer-chain selected evidence 的集合；
- 当前 Development Trace 中 `rerank_enabled=false`；
- 已有 parent/context-only 的数据模型和 guard，但本批 19 道失败样本没有足够字段确认 parent/child overcitation；
- 当前支持判断主要由 grounding 的 lexical/numeric policy 提供，不是独立的最小 citation selection。

## 4. Experiment A 实现

新增独立纯函数 `select_minimal_supporting_citations()`：

- 候选 citation 只能来自当前已存在的 response evidence；
- 只保留已有 audit/grounding mapping 明确支持至少一个 claim 的 citation；
- 不因 query 相关或进入 context 自动引用；
- 按 response 原始顺序稳定输出；
- 同一 chunk 只输出一次；
- parent 与 child 同时明确支持同一事实时只保留更直接的 child；若只有 parent 支持，则保留 parent；
- 不重新检索、不重新生成、不修改答案文本。

本次先执行离线重映射，不自动改变线上 API 行为。

说明：本次离线 replay 的 supporting mapping 使用已保存的 R2 `citation.supporting_actual_chunk_ids` 评测字段作为审计标签；它用于验证 Citation Selection 的可达效果，不代表线上运行时已经具备同等的人工标注信息，也不代表该策略已经激活。

## 5. Baseline vs Experiment A

| Metric | Baseline | Experiment A | Result |
| --- | ---: | ---: | --- |
| Citation Precision | 8.1667 / 23 = 35.51% | 21 / 23 = 91.30% | PASS |
| Supporting Recall | 21 / 23 = 91.30% | 21 / 23 = 91.30% | 不下降 |
| Question Citation Accuracy | 31 / 33 = 93.94% | 31 / 33 = 93.94% | 不下降 |
| Expected Coverage | 21 / 39 = 53.85% | 21 / 39 = 53.85% | 不变 |
| Initial Recall@10 | 39 / 39 = 100% | 39 / 39 = 100% | 不变 |

额外指标：

| Metric | Baseline | Experiment A |
| --- | ---: | ---: |
| Average citations / answered question | 2.7879 | 0.6364 |
| Supporting citations / answered question | 0.6364 | 0.6364 |
| Non-supporting citations / answered question | 2.1515 | 0 |
| Questions with over-citation | 22 | 0 |
| Questions with missing citation | 2 | 2 |
| Questions with exact minimal citation | 1 | 21 |

共生成 36 条逐题 diff，移除 71 条 citation，`supporting_removed=0`。

## 6. 测试与回归

新增测试覆盖：

- 单一 supporting evidence；
- 双 evidence 共同支持；
- 无关/adjacent context 不自动成为 citation；
- parent/child 去重；
- supporting citation 不误删；
- 多 claim 不同 citation 映射；
- 稳定顺序和同 chunk 去重。

产物：

- `evaluation/phase12b1/citation_failure_audit.jsonl`
- `evaluation/phase12b1/citation_failure_subtypes.json`
- `evaluation/phase12b1/citation_diff.jsonl`
- `evaluation/phase12b1/baseline_vs_experiment_a.json`

## 7. 边界与后续

本阶段仅完成离线 Citation remapping 实验，结果 PASS，但尚未自动激活线上 Citation Selection。没有执行 Context、Grounding、Generation 或 Refusal 优化，也没有修改 Golden、Validation、Holdout。

Phase 12B-1 到此停止，等待后续确认。
