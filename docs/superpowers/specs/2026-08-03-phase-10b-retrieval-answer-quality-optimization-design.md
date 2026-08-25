# Phase 10B：Retrieval and Answer Quality Optimization 设计

日期：2026-08-03  
前置验收：Phase 10A 已人工验收通过；基线提交 `23fa900`  
阶段边界：只优化检索、排序、证据选择、拒答与引用质量，不进入 Phase 10C/10D。

## 目标

在冻结的 64 题黄金集和 Phase 10A Trace 基础上，解释并逐步改善 Page Recall、Chunk Recall、MRR、graded nDCG、False Rejection、Unsupported Answer 和引用质量。Development 用于实验与调参，Validation 用于配置选择，Holdout 只在最终配置完全冻结后运行一次。

## 不变约束

- 黄金集内容、SHA-256、证据标注和指标口径不变；
- Task 1 完成前不修改 Chunking、TopK、检索模式、检索权重、Rerank、Prompt、证据选择或拒答策略；
- 不把 `lightrag_mix_unspecified` 推断为 Dense、Keyword 或 Graph 子检索来源；
- 普通问答接口契约不增加内部 Score、rank 或策略切换参数；
- 不读取 Holdout 逐题结果进行分析、调参或选择；
- 不覆盖 Active Generation；切块实验如必要必须使用 Candidate Generation 和 sidecar 映射；
- 不开发反馈后端、不修改 Streamlit、不引入 LangGraph、不创建 Tag、不打包 RC、不部署生产。

## 分阶段设计

### Task 1：Development + Validation 失败矩阵

复用 Phase 10A 的真实 `baseline_results.jsonl`、`baseline_diagnosis.jsonl` 和 Trace schema，仅筛选 `development` 36 题与 `validation` 16 题。不得读取 Holdout 12 题的逐题结果。

每题输出 `question_id`、split、题型、难度、完整 expected evidence/answer points、initial rank、expected evidence recalled/selected count、final citations、answer status、failure layer、failure category、failure reason。Failure layer 固定为 Retrieval、Ranking、Evidence Selection、Generation、Refusal、Citation；category 至少覆盖 wrong_document、page_not_recalled、chunk_not_recalled、correct_chunk_rank_too_low、evidence_not_selected、table_parse_failure、cross_page_context_missing、query_term_mismatch、metadata_filter_failure、evidence_threshold_too_high、generation_extraction_failure、citation_binding_failure。

汇总按 question_type、difficulty、document、split、failure_layer 分组，保存 numerator、denominator、value；不把“最终答案错误”笼统归因为模型问题。

### Task 2：确定性 Query Normalization 单变量实验

新增纯函数标准化器，不调用 LLM 自由改写。记录 original_query、normalized_query、detected_model、detected_component、detected_parameter、added_aliases。只改变标准化开关，其他查询配置、Generation、模型和缓存状态保持不变。先跑 development，再用 validation 选择是否保留。

### Task 3：检索配置消融

为每个独立配置启动隔离评测实例或受控配置文件，普通 API 不暴露策略切换 query 参数。逐项比较当前 LightRAG mix、dense、keyword、hybrid、top_k、chunk_top_k、可用 metadata filter 和 Parent Context。每次只改变一个明确变量或一个事先定义的组合，记录 dev/validation 指标与 p50/p95 延迟。

### Task 4：Rerank 对比

在最佳初始召回配置冻结后，比较无 Rerank、轻量 Rerank 和环境支持时的效果优先 Rerank。记录真实 rerank score、前后 rank、MRR、nDCG、Any/Complete Evidence Recall、最终证据和 rerank 延迟。Rerank 失败必须显式记录，不能静默标记成功。

### Task 5：证据选择与拒答校准

仅分析冻结检索/Rerank 配置下的 False Rejection，区分证据未召回、排名过低、未被选择、多证据不完整、安全阻断和生成未使用证据。输出 success、partial_answer、insufficient_evidence、safety_blocked 等可解释状态；不简单降低全局阈值。负样本拒答率不得下降，Unsupported Answer Rate 不得恶化，fabricated citation 必须为 0。

### Task 6：引用绑定

检查答案点与 evidence、文档、页码、Chunk、Generation 和多证据覆盖关系，输出 question-level citation accuracy、answer-point coverage、unsupported point count、wrong-page/wrong-chunk count、incomplete multi-evidence count。claim-level 自动判定不可靠时保持不可用，不编造准确率。

### Task 7：必要时的 Candidate Chunking 实验与最终 Holdout

只有失败矩阵证明 Top20 缺失、表格拆分、跨页缺失、步骤截断、标题关联丢失或型号混淆是主要瓶颈时，才比较 P0、标题感知、表格感知、Parent-Child 和必要跨页方案。每个方案使用独立 Candidate Generation，并基于冻结黄金的 document/page/evidence_text/hash 生成 `golden_evidence_mapping_<scheme>.json`。检索、Rerank、证据、拒答、引用和切块配置全部冻结后生成 `final_config_manifest.json`，Holdout 只运行一次并停止调参。

## 质量门禁

Development/Validation 目标为：Document Recall@5=100%；Page Recall@5≥90%；Chunk Recall@5≥78%；Chunk Recall@20≥88%；Any Evidence Recall@5≥90%；Complete Evidence Recall@20≥88%；MRR≥0.78；graded nDCG@10≥0.78；False Rejection Rate≤12%；Unsupported Answer Rate≤5%；Question-level Citation Accuracy≥95%；Negative Rejection Rate=100%；Citation Trace Completeness=100%；fabricated citation=0；非预期 5xx=0。未达标必须如实报告最佳配置与剩余失败原因。

## 产物与提交边界

阶段产物包括失败矩阵、失败汇总、标准化结果、检索消融、Rerank、证据选择、拒答、引用绑定、必要时的切块映射、development/validation 结果、最终配置 manifest、唯一 Holdout 结果、最终指标和阶段报告。每个实验独立提交并可追踪到 Git commit、数据集 SHA、Generation、配置、模型、缓存状态、运行时间、指标和延迟。
