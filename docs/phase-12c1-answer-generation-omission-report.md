# Phase 12C-1 Answer Generation Omission Analysis

最终状态：**ROOT_CAUSE_RECLASSIFIED**

## 数据范围与边界

本报告只读取 Phase 12A Development canonical artifacts 与 Phase 12B-3A-R1 hydrated Runtime evidence；未读取 Validation/Holdout，未调用模型，未重跑检索，未修改业务问答代码。

Phase 12A 的 `generation_omitted=6/39` 是原始 funnel 标签。本阶段对其中 S004、S006、S018、D003、D011、D015 做语义级逐题复核。

## Gate A 结论

6 条样本中确认属于 generation omission：**0/6**。因此 Gate A 未通过，最终状态为 **ROOT_CAUSE_RECLASSIFIED**，不执行 Experiment A。

原因是 Phase 12A 的 expected answer point 多数是整段 evidence block，而不是可判定的语义答案点；`final_emitted=false` 不能直接等同于 raw answer 漏答。

## 逐题审计

| ID | 问题类型 | Runtime evidence | 语义答案点覆盖 | 主要结论 |
|---|---|---:|---:|---|
| D003 | terminology | 3/3 | 3/3 | evaluation_point_artifact：原始答案已覆盖问题的语义答案点；Phase 12A 的 giant evidence-block expected point 与语义点粒度不一致。 |
| D011 | unit_expression | 4/4 | 2/2 | evaluation_point_artifact：原始答案已覆盖问题的语义答案点；Phase 12A 的 giant evidence-block expected point 与语义点粒度不一致。 |
| D015 | cross_page | 3/3 | 1/1 | knowledge_gap：公式已回答；Hs 的具体建议值不在精确 Runtime evidence 中，不能归因于 generation omission。 |
| S004 | parameter | 2/2 | 2/2 | evaluation_point_artifact：原始答案已覆盖问题的语义答案点；Phase 12A 的 giant evidence-block expected point 与语义点粒度不一致。 |
| S006 | condition_limit | 1/1 | 2/2 | evaluation_point_artifact：原始答案已覆盖问题的语义答案点；Phase 12A 的 giant evidence-block expected point 与语义点粒度不一致。 |
| S018 | condition_limit | 2/2 | 2/2 | evaluation_point_artifact：原始答案已覆盖问题的语义答案点；Phase 12A 的 giant evidence-block expected point 与语义点粒度不一致。 |

所有六条样本的 Runtime evidence 均已 hydration，缺失 0，截断 0；canonical Phase 12A trace 没有 provider context token estimate，因此输入长度只能记为 missing，不能伪造精确预算证明。

### 关键重分类

- S004、S006、S018、D003、D011：raw answer 已包含问题要求的数值、条件、术语或单位，属于评测 expected point 粒度/标注问题，不是 generation omission。
- D015：公式已正确回答；Hs 具体建议值不在精确 Runtime evidence 中，属于 knowledge gap / 不可回答子点，不是 generation omission。

另外，逐字复核 canonical i0 raw answer 与 Phase 12A funnel 的 `expected_point_present_in_raw_answer` 标记，发现 4 个冲突（D011/D011-p1, D015/D015-p2, S004/S004-p1, S018/S018-p1）。这进一步说明原始 generation omission 标签不能脱离答案点定义和原始答案文本单独解释。

## Omission Taxonomy

本次复核没有确认的真实 omission，因此 `multi_point_omission`、`condition_omission`、`numeric_omission`、`unit_omission`、`procedure_step_omission`、`cross_evidence_synthesis_omission`、`terminology_omission`、`over_conservative_answer`、`evidence_not_salient` 均为 0；D015 的 Hs 值按 knowledge gap 处理，不能强行归类。

## Answer Point Coverage Baseline

原始 funnel baseline：generation_omitted **6/39**；本次语义复核子集：可回答语义点 **12/12**，覆盖率 **100.00%**。这两个分母不同，不能把子集结果改写成全量 0/39。

## Prompt 行为分析（只读）

- Prompt 明确要求只能依据检索手册内容回答。
- Prompt 强调依据不足时拒答、不得猜测/补写/编造文件名和页码。
- Grounding 附加约束要求每个可验证答案点绑定具体证据，未覆盖内容不得补充常识或推断。
- 当前 Prompt 没有明确要求覆盖所有证据支持的多个条件、步骤、参数或要求。
- 但本次六条样本的 raw answer 已覆盖审计出的语义答案点，因此不能据此证明 Prompt 导致了 generation omission。

当前 Prompt 确实缺少显式的 answer completeness instruction，但六条样本没有确认 generation omission，所以本阶段不以此为根因，也不修改 Prompt。

## Experiment A

Experiment A 仅设计、不执行。候选唯一变量是 Answer Completeness Instruction；检索、Context、模型、sampling、Citation、Grounding、Refusal 均应冻结。由于 Gate A 未通过，当前不能用一次 Prompt 实验掩盖评测点标注错误。

## Guardrails 与副作用

未执行 Experiment A，因此没有新的 Unsupported Answer Rate、False Rejection、Citation 或回答长度对比结果。Phase 12A 的历史 guardrail 数值保留为历史基线，不作为本阶段实验结果。

## 测试与变更

本阶段仅新增离线审计脚本、测试和报告；不修改 Retrieval、Rerank、Context、Citation、Grounding、Refusal、Generation 或数据集。

详细逐题 JSONL：`evaluation/phase12c1/omission_audit.jsonl`；汇总：`evaluation/phase12c1/omission_summary.json`。
