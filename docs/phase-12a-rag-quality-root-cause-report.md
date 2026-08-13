# Phase 12A：RAG Quality Root Cause Analysis

## 结论

本阶段只读分析 Development 数据，没有修改问答代码、检索参数、Prompt、拒答阈值、引用策略或冻结数据。

在当前 36 道 Development、39 个期望答案点中，最大瓶颈按主要根因计数为：

1. `citation_failure`：19 道问题，主要表现为 supporting citation 之外的过度引用；Citation Precision 仅 0.35507246376811585。
2. `answer_generation_failure`：6 道问题，证据已经召回并提供，但期望答案点没有进入原始答案。
3. `context_failure` / `grounding_failure`：分别为 3 / 4 道主要问题；正确证据已召回，但在 Context 选择或 grounding 后处理阶段丢失。

同时，3 道可判定为 `false_refusal`，而不是 Retrieval 缺失。当前数据没有确认的 `retrieval_failure`、`rerank_failure`、`knowledge_gap` 或 `parser_or_ingestion_issue`。

## 1. 数据范围与隔离

- Development：`evaluation/phase10b3i/i0_development_results.jsonl`，36 道，含原始答案和 Retrieval Trace。
- Funnel：`evaluation/phase10b3i_r2/coverage_funnel_matrix.jsonl`，39 个期望答案点。
- Metrics：`evaluation/phase10b3i_r2/i0_development_metrics.json`。
- J1/J1S 原始评测目录：当前工作区未发现可读取的原始 artifact，未用其他冻结集替代。
- Validation / Holdout：未读取，输入与产物均标记 `validation_used=false`、`holdout_used=false`。

数据隔离检查：`True`。

## 2. Failure Taxonomy

`grounding_failure` 是为避免把“原始答案正确但 grounding 后处理移除”误报为 `answer_generation_failure` 而增加的实现层子类；它不代表新增业务逻辑。

| Root Cause | Count | Rate |
| --- | --- | --- |
| retrieval_failure | 0 | 0.0% |
| rerank_failure | 0 | 0.0% |
| context_failure | 3 | 8.3% |
| grounding_failure | 4 | 11.1% |
| citation_failure | 19 | 52.8% |
| false_refusal | 3 | 8.3% |
| unsupported_answer | 0 | 0.0% |
| answer_generation_failure | 6 | 16.7% |
| knowledge_gap | 0 | 0.0% |
| parser_or_ingestion_issue | 0 | 0.0% |
| unknown | 0 | 0.0% |
| not_a_failure | 1 | 2.8% |

## 3. Failure Funnel

### 点级 Funnel（39 个期望答案点）

| Stage | Numerator | Denominator | Rate |
| --- | --- | --- | --- |
| initial_retrieval | 39 | 39 | 1.0 |
| selected_for_context | 36 | 39 | 0.9230769230769231 |
| available_to_provider | 36 | 39 | 0.9230769230769231 |
| raw_answer_point_present | 28 | 39 | 0.717948717948718 |
| grounding_retained | 29 | 39 | 0.7435897435897436 |
| final_emitted | 23 | 39 | 0.5897435897435898 |
| expected_coverage_exact_or_overcitation | 21 | 39 | 0.5384615384615384 |

### Rerank 阶段

| Stage | Numerator | Denominator | Rate / Status |
| --- | --- | --- | --- |
| reranked_retained | None | 39 | missing |

Rerank 阶段为 `missing/not_applicable`，不是 0%；当前 Development Trace 均为 `rerank_enabled=false` 且 `reranked_results=[]`。

### 问题级质量阶段

| Stage | Numerator | Denominator | Rate |
| --- | --- | --- | --- |
| answered_or_partial | 33 | 36 | 0.9166666666666666 |
| false_refusal | 3 | 36 | 0.08333333333333333 |
| question_semantic_support | 21 | 33 | 0.6363636363636364 |
| question_citation_accuracy | 31 | 33 | 0.9393939393939394 |

点级与问题级分母不同，不能合并为一个伪造的单一漏斗百分比。

## 4. 核心指标与根因

| Metric | Value / numerator-denominator |
| --- | --- |
| Recall@1 | {'numerator': 23, 'denominator': 39, 'value': 0.5897435897435898} |
| Recall@3 | {'numerator': 31, 'denominator': 39, 'value': 0.7948717948717948} |
| Recall@5 | {'numerator': 34, 'denominator': 39, 'value': 0.8717948717948718} |
| Recall@10 | {'numerator': 39, 'denominator': 39, 'value': 1.0} |
| Recall@20 | {'numerator': 39, 'denominator': 39, 'value': 1.0} |
| MRR@20 | {'numerator': 28.234523809523807, 'denominator': 39, 'value': 0.7239621489621489} |
| Supporting Recall | {'numerator': 21, 'denominator': 23, 'value': 0.9130434782608695, 'included_statuses': ['final emitted points'], 'excluded_statuses': ['not final emitted'], 'definition_version': 'phase10b3d-metric-policy-v1', 'split': 'development'} |
| Expected Coverage | {'numerator': 21, 'denominator': 39, 'value': 0.5384615384615384, 'included_statuses': ['covered_exact_citation', 'covered_with_overcitation'], 'excluded_statuses': ['all other funnel stages'], 'definition_version': 'phase10b3d-metric-policy-v1', 'split': 'development'} |
| Citation Precision | {'numerator': 8.166666666666664, 'denominator': 23, 'value': 0.35507246376811585, 'included_statuses': ['final emitted points'], 'excluded_statuses': ['not final emitted'], 'definition_version': 'phase10b3d-metric-policy-v1', 'split': 'development'} |
| Question Citation Accuracy | {'numerator': 31, 'denominator': 33, 'value': 0.9393939393939394, 'included_statuses': ['success', 'partial_answer'], 'excluded_statuses': ['insufficient_evidence', 'safety_blocked'], 'definition_version': 'phase10b3d-metric-policy-v1', 'split': 'development'} |
| False Rejection Rate | {'numerator': 3, 'denominator': 36, 'value': 0.08333333333333333, 'included_statuses': ['insufficient_evidence', 'safety_blocked'], 'excluded_statuses': ['success', 'partial_answer'], 'definition_version': 'phase10b3d-metric-policy-v1', 'split': 'development'} |
| Unsupported Answer Rate | {'numerator': 12, 'denominator': 33, 'value': 0.36363636363636365, 'included_statuses': ['success', 'partial_answer'], 'excluded_statuses': ['insufficient_evidence', 'safety_blocked'], 'definition_version': 'phase10b3d-metric-policy-v1', 'split': 'development'} |

- Supporting Recall 低于 100% 的主要原因不是 Initial Retrieval：39/39 期望点进入 Initial TopK；损失主要发生在 Context 选择、grounding false negative、生成遗漏和拒答。
- Expected Coverage 只有 21/39，主要由 generation omission 6、grounding false negative 5、generation refusal 3、recalled-not-selected 3 造成。
- False Rejection 为 3/36；这些样本已有初始证据和可用证据，问题位于拒答/生成判定，而不是 Retrieval Recall。
- Citation Precision 为 8.166/23；高频问题是 supporting citation 存在但同时挂载了无关 citation，说明 citation selection/mapping 比召回更突出。
- `question_level_unsupported_answer_rate=12/33` 的历史口径把“没有全部最终支持的答案点”也纳入分子，不能直接等同于 12 道纯 unsupported answer；当前结构化证据只确认 2 道样本需要人工语义复核。

## 5. 维度分布

| Dimension | Value | Primary root-cause count |
| --- | --- | --- |
| question_type | component_description | {'citation_failure': 2} |
| question_type | condition_limit | {'answer_generation_failure': 2, 'citation_failure': 2, 'grounding_failure': 1} |
| question_type | cross_page | {'context_failure': 2, 'answer_generation_failure': 1} |
| question_type | maintenance_interval | {'citation_failure': 4} |
| question_type | parameter | {'answer_generation_failure': 1, 'citation_failure': 2, 'not_a_failure': 1} |
| question_type | procedure | {'citation_failure': 5, 'context_failure': 1, 'grounding_failure': 1} |
| question_type | safety_warning | {'citation_failure': 3, 'false_refusal': 1} |
| question_type | table | {'false_refusal': 2} |
| question_type | terminology | {'answer_generation_failure': 1} |
| question_type | troubleshooting | {'grounding_failure': 2} |
| question_type | unit_expression | {'citation_failure': 1, 'answer_generation_failure': 1} |
| difficulty | hard | {'context_failure': 2, 'answer_generation_failure': 1} |
| difficulty | medium | {'citation_failure': 19, 'answer_generation_failure': 5, 'false_refusal': 3, 'grounding_failure': 4, 'context_failure': 1, 'not_a_failure': 1} |
| knowledge_base_id | 8fce4626859d44abb70a9ae5b0372cea | {'citation_failure': 19, 'answer_generation_failure': 6, 'false_refusal': 3, 'context_failure': 3, 'grounding_failure': 4, 'not_a_failure': 1} |
| document | 2196-ANSI-Manual-Chinese.pdf | {'citation_failure': 10, 'answer_generation_failure': 3, 'false_refusal': 2, 'context_failure': 3, 'grounding_failure': 2} |
| document | t1739cn.pdf | {'citation_failure': 9, 'answer_generation_failure': 3, 'false_refusal': 1, 'grounding_failure': 2, 'not_a_failure': 1} |
| answer_status | insufficient_evidence | {'false_refusal': 3} |
| answer_status | partial_answer | {'citation_failure': 16, 'answer_generation_failure': 6, 'context_failure': 3, 'grounding_failure': 4, 'not_a_failure': 1} |
| answer_status | success | {'citation_failure': 3} |

## 6. Top 10 代表性失败案例

| ID | Type | Primary Root Cause | Funnel Stage | Initial Rank | Status |
| --- | --- | --- | --- | --- | --- |
| S011 | cross_page | context_failure | grounding_false_negative:1, recalled_not_selected:1 | 1,4 | partial_answer |
| S017 | cross_page | context_failure | covered_with_overcitation:1, recalled_not_selected:1 | 6,8 | partial_answer |
| S020 | procedure | context_failure | recalled_not_selected:1 | 5 | partial_answer |
| S007 | table | false_refusal | generation_refusal:1 | 1 | insufficient_evidence |
| S013 | table | false_refusal | generation_refusal:1 | 1 | insufficient_evidence |
| S015 | troubleshooting | grounding_failure | grounding_false_negative:1 | 1 | partial_answer |
| D012 | procedure | grounding_failure | grounding_false_negative:1 | 2 | partial_answer |
| S004 | parameter | answer_generation_failure | generation_omitted:1 | 1 | partial_answer |
| D015 | cross_page | answer_generation_failure | covered_with_overcitation:1, generation_omitted:1 | 1,2 | partial_answer |
| S008 | maintenance_interval | citation_failure | emitted_without_supporting_citation:1 | 1 | partial_answer |

逐题完整诊断见 `evaluation/phase12a/diagnosis_matrix.jsonl`，包含 expected evidence、initial/reranked/final evidence、citation status、secondary root cause、诊断和建议。

## 7. Phase 12B 推荐实验（仅设计，不执行）

| Priority | Current problem | Affected samples / metrics | Single variable | Success standard |
| --- | --- | --- | --- | --- |
| P0 | Citation over-selection and wrong mapping | 19 primary questions or citation-affected samples; Citation Precision 8.166/23 | Only citation selection/mapping policy | Citation Precision materially improves without lowering Supporting Recall below 21/23 |
| P1 | Evidence selected but not retained in final Context | 3 questions; Context stage 3/39 points | Only final evidence selection/context assembly | Recalled-not-selected falls from 3/39 to 0 while Initial Recall and answer quality do not regress |
| P1 | Grounding false negative | 5 points across 5 questions | Only grounding semantic support decision | Grounding false negative falls from 5/39 without increasing unsupported emitted points |
| P2 | Generation omission | 6 points across 6 questions | Only answer completeness/structured generation behavior | Generation omission falls from 6/39 and Expected Coverage improves beyond 21/39 |
| P2 | False refusal | 3/36 questions | Only refusal evidence condition/threshold | False Rejection falls below 3/36 without increasing Unsupported Answer Rate |

这些实验不在 Phase 12A 执行，也不修改冻结数据。

## 8. 仍无法判断的问题与缺失数据

- 没有 J1/J1S 原始评测目录或可直接关联的 J1S Trace artifact，无法把 J1S 结果与本 Development 分布做逐题交叉验证。
- 当前 Trace 的 `rerank_enabled=false` 且 `reranked_results=[]`，因此无法确认真实 rerank_failure；`recalled_not_selected` 被保守归为 Context/selection failure。
- `S008`、`S011` 的 semantic support 结果为 `ambiguous_needs_human_review`，不能强行判成 parser、knowledge gap 或纯 generation failure。
- 当前数据没有足够的原始 PDF 对照证据来确认 knowledge_gap 或 parser_or_ingestion_issue；由于 expected chunk 全部进入 Initial TopK，这两类暂记为 0 confirmed，而非证明它们不存在。

## 9. 交付边界

本阶段已停止。没有执行 Phase 12B 实验，没有修改检索、rerank、Prompt、拒答、引用或 Generation，也没有读取 Validation/Holdout 数据。
