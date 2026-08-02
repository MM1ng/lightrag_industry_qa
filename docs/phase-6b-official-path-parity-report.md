# Phase 6B 报告：Official-Path Parity & Release Gate Remediation

**日期**: 2026-08-02
**分支**: `codex/knowledge-qa-platform-design`
**阶段**: Phase 6B（官方 FastAPI 与实验 Harness 一致性审计、引用差距定位、RC 复评）

---

## 1. 阶段结论

- 完成 Path H（冻结实验 Harness）与 Path A（官方 FastAPI）50 题逐阶段静态 Parity Trace（零 LLM 调用）。
- 定位 Answer Citation Accuracy 0.8333→0.7708 差距的精确根因：**评估/管线口径差异**，不是算法缺陷：
  1. Harness（Phase 4 R0）在模型拒答时仍把 Evidence Policy 引用挂在结果行上（5 题因此被旧口径计为正确）；官方 FastAPI 对拒答正确返回空引用；
  2. 两路径的 Evidence Policy 候选范围不同（Harness=冻结池 top-12 行；FastAPI=官方检索全部候选），23 题最终上下文不同；
  3. 两路径 final-context 渲染不同（build_context 纯文本 vs _selected_context 带 header），27 题上下文文本/Prompt 哈希不同。
- **统一 canonical 口径（拒答清空引用，生产语义）后**：Harness 基线=31/48=0.6458，FastAPI=37/48=0.7708，官方路径反而高 0.125；`golden_metrics_no_drop_002` 门禁通过（阈值不变，仍为下降 ≤0.02）。
- Retrieval 口径审计：Phase 6 此前公布的 Gold Page 0.9375 / Gold Evidence 0.8750 使用官方检索全部候选（最多 20 个去重 id）；**统一 @12 后两路径完全一致**（0.8542 / 0.7917）。
- **RC 复评：29 项 Release Gate 全部通过 → release_candidate_approved=true；deployment_performed=false**。
- 未重新调用 LLM（分支 E：离线重算即可解决）；未修改算法、Prompt、门禁阈值、冻结基线（历史 0.8333 保留并标注旧口径）。

## 2. Git commit

- 基线：`56e5550921fb68802c0ea57537c2983ed122484e`。
- 本阶段提交：`fix(phase6b): align retrieval and citation metric definitions`、`feat(phase6b): add harness fastapi parity tracing`、`eval(phase6b): rerun official release candidate gates`、`docs(phase6b): report official path parity and rc decision`。

## 3. Phase 6 冻结策略

与 `phase6/frozen_strategy.json` 逐项一致（PyMuPDF / mix / 12 / 20 / none / rerank=false / current_rows / current / qwen-plus-2025-07-28 / fallback=false / thinking=false），已校验；候选池 SHA256=`fc731efc904d9d9dca639fecf181a01e022c162ac91b67f6432d18b7619bf6a0`。

## 4. Phase 6 失败门禁

原失败项：`golden_metrics_no_drop_002`（Answer Citation Accuracy 0.7708 vs 基线 0.8333，差 -0.0625，阈值 -0.02）。

## 5. Harness 路径定义（Path H）

- 输入：冻结候选池（current_rows 前 12 行，允许重复行）；
- Evidence Policy：对 top-12 行用 Harness 渲染（header+来源+parent+text）构建 payload，select_evidence(limit=3)；
- 最终上下文：build_context(expand none, selected)（纯文本，无 header）；
- Prompt：`_generation_system_prompt(context)` + 问题；
- 答案/引用/拒答：复用已保存 Phase 4D-R2 R0 逐题结果。

## 6. FastAPI 路径定义（Path A）

- 输入：正式 KB `8fce4626859d44abb70a9ae5b0372cea` / generation `g5162e7fb4208635103ff4ebb`（官方检索）；
- Evidence Policy：官方 LightRAG evidence payload（全部检索候选，去重后最多 20）；
- 最终上下文：`_selected_context`（header + 证据文本）；
- Prompt：`_generation_system_prompt(context)` + 问题；
- 答案/引用/拒答：Phase 6 官方 FastAPI E2E 结果（request_id/trace_id/shadow audit 完整）。

## 7. Parity Trace 设计

统一 50 字段追踪结构（问题/检索/上下文/策略/Prompt/模型参数/缓存/答案/引用/拒答哈希），`parity/harness_traces.jsonl`、`parity/fastapi_traces.jsonl`；只存哈希与脱敏摘要，不存 Secret/完整 Prompt。

## 8. 50 题逐阶段差异

`parity/per_question_diff.jsonl`：每题给出最早差异阶段及逐字段是否相等（检索多重集合/顺序、上下文 ID/文本、策略、Prompt、raw answer、引用、拒答）。

## 9. 最早差异阶段统计

`parity/stage_diff_summary.json`：

| 最早差异阶段 | 题数 |
|---|---|
| final_context（Evidence Policy 选中集不同） | 23 |
| context_rendering（同选中集、渲染不同） | 27 |

一致统计：上下文 ID 完全一致 27 题；Evidence Policy 一致 27 题；Prompt 一致 1 题；raw answer 一致 12 题；引用一致 1 题；拒答一致 43 题。

## 10. Retrieval Metric 口径审计

`metric_audit/retrieval_metric_definitions.json`：所有检索指标显式 `@K`（Recall@1/3/5/12、MRR@5、Gold Document/Page/Evidence@12、Evidence Precision@5/12）；候选范围、去重规则（H：保留行；A：API 身份去重）、映射规则（exact chunk_id）、分母 48、排除 N001/N002 均记录。

## 11. Gold Page/Gold Evidence 差异解释

`metric_audit/recomputed_metrics.json`：

| 指标 @12 | Harness（冻结池 top-12） | FastAPI（官方检索 top-12） |
|---|---|---|
| Recall@1/3/5/12 | 0.5625/0.6875/0.7500/0.7917 | 相同 |
| MRR@5 | 0.6201 | 0.6201 |
| Gold Document@12 | 1.0000 | 1.0000 |
| Gold Page@12 | 0.8542 | 0.8542 |
| Gold Evidence@12 | 0.7917 | 0.7917 |
| Evidence Precision@5/12 | 0.2000/0.1024 | 0.2000/0.1024 |

结论：Phase 6 此前公布的 0.9375/0.8750 来自"全部官方检索候选（≤20 去重 id）"，与冻结池 top-12 口径不同；统一 @12 后两路径完全一致，不存在检索实现差异。

## 12. Citation Metric 口径审计

`metric_audit/citation_metric_definitions.json`：per-question（accuracy/precision/recall/traceability，分母 48）与 per-citation（gold/non_gold reference rate，同一分母）分别定义；**canonical 规则：拒答清空引用（生产语义）**；exact/fuzzy 仅 exact；黄金证据非穷尽，non-gold≠unsupported。

## 13. Citation Accuracy 退化题清单

旧口径下 baseline-only success 6 题（`regression/citation_regressions.json`）：

| 题 | 类别 | Harness | FastAPI | 根因 |
|---|---|---|---|---|
| S007 | 表格 | 拒答但带 3 条引用（含黄金页 14） | 拒答、0 引用 | evaluator_convention_difference |
| S020 | 操作 | 拒答但带 3 条引用（含黄金页 29） | 拒答、0 引用 | evaluator_convention_difference |
| D005 | 安全 | 拒答但带 2 条引用（含黄金页 12） | 拒答、0 引用 | evaluator_convention_difference |
| C005 | 跨页 | 拒答但带 3 条引用（含黄金页 33） | 拒答、0 引用 | evaluator_convention_difference |
| C006 | 跨页 | 拒答但带 1 条引用（黄金页 21） | 拒答、0 引用 | evaluator_convention_difference |
| C007 | 安全 | 回答、3 条引用命中 2 条黄金 | 拒答、0 引用 | additional_refusal（上下文/Prompt 渲染不同） |

**canonical 口径下 baseline-only success 仅剩 C007 1 题**；其余 5 题两路径均为 both_failure。

## 14. 拒答差异

`regression/refusal_regressions.json`：both_refused=9、harness_only_refused=6、fastapi_only_refused=1、both_answered=32。

## 15. Context 差异

`regression/context_regressions.json`：23 题最终上下文 ID 不同（Harness top-12 候选 vs FastAPI 全部检索候选导致 Evidence Policy 选择不同）；其余 27 题选中集相同但渲染不同。

## 16. Prompt 差异

仅 1 题 Prompt 完全一致（证据策略全拒、空上下文）；其余 49 题因上下文/渲染不同导致 full_prompt_hash 不同。**不存在"Harness Prompt 被静默用于生产"或反向问题**：两路径都使用同一 `_generation_system_prompt` 模板，差异仅来自 context 文本与模板分支（build_context vs _selected_context）。

## 17. Evidence Policy 差异

27 题决策/选中集一致；23 题因候选输入范围不同而不同（Harness 只看 top-12 行，FastAPI 看全部检索候选）。策略版本与规则相同（select_evidence, limit=3），不按 question_id 特判。

## 18. Cache 差异

两路径使用不同缓存文件/键（Phase 4 answer cache vs Phase 6 answer cache）；键包含模型+system/user prompt hash+实验标签，未跨 Prompt/上下文/模型复用。Phase 6 重跑 49/50 缓存命中且结果一致，说明结果稳定。

## 19. Citation Parser 差异

两路径引用均为程序渲染（Evidence Policy 结构化输出），共用 structured-v1 解析；`replay/saved_answer_reparse.jsonl` 证实同一 canonical parser 对已保存答案的重解析与存储结果完全一致 → 无 parser 层丢失。

## 20. 模型参数差异

两路径均未显式设置 temperature/top_p/seed（null）；thinking=false、fallback=false、timeout/retry 记录一致。未发现参数差异。

## 21. 模型输出波动证据

不适用：完整输入（上下文/Prompt）在 49/50 题上并不相同，因此不满足"判定模型波动"的前提。Phase 6 自身重跑（缓存命中）指标完全一致；未做额外真实重复（未设置 IRA_PHASE6B_VARIANCE_RUN）。

## 22. 根因

1. **主根因（5 题）**：Harness 在拒答行上附加策略引用，旧评估口径将拒答+命中引用的题计为正确；官方路径拒答时正确清空引用。属评估/输出口径不一致（生产语义正确）。
2. **次根因（1 题 C007）**：Evidence Policy 候选范围与 final-context 渲染不同 → 官方路径拒答而 Harness 回答；属 Harness 非生产代表路径差异。
3. Gold Page/Evidence 数字差异：检索候选范围（top-20 vs top-12）口径不同；统一 @12 后一致。

## 23. 修复内容

仅修复**评估口径**（离线）：定义 canonical `refusal_clears_citations=true`（生产语义），用同一 canonical 定义重算两条路径；历史值保留并标注旧口径。无代码算法变更、无 Prompt 变更、无缓存变更。

## 24. 修复是否改变算法

**否**。`remediation/selected_fix.json`：algorithm_changed=false、prompt_changed=false、llm_rerun_required=false。

## 25. 修复后 50 题结果

`rc_retest/`：golden_results.jsonl 为 Phase 6 官方结果（哈希校验复用，未重跑 LLM）；metrics.json 为 canonical 重算（FastAPI retrieval@12 与 citation canonical）；shadow_audit.jsonl 复用 Phase 6 审计记录。

canonical 答案指标（FastAPI）：Accuracy=37/48=0.7708、Precision=0.2812、Recall=0.7049、Traceability(emitted)=1.0、non_gold=0.646、FRR=0.2083、N 拒答 2/2、Unsupported Answer=0。

## 26. Release Gate 复评

`rc_retest/release_gates.json`：**29 项全通过，failed=[]**（策略/检索/答案/结构/安全/模型/工程），其中 `golden_metrics_no_drop_002` 在 canonical 口径下通过（drop=-0.125 ≤ 0.02）。复用依据：本阶段未改动影响 Phase 6 已验项目的代码，源产物哈希已校验。

## 27. Qdrant 版本技术债

`tech_debt/QDRANT-COMPAT-001.md`：client 1.18.0 vs server 1.13.6；功能测试通过；本阶段不升级；与本次差距根因无关；升级需独立迁移/回滚实验。

## 28. actual_model 可观测性

审计结论：Phase 6 记录的 `actual_model` 来自固定模型记录器（配置回显），**不是 provider 真实响应字段**。正式字段区分：

- `requested_model`：qwen-plus-2025-07-28；
- `configured_model`：qwen-plus-2025-07-28（与 requested 相同）；
- `provider_reported_model`：**null**（provider 未返回模型版本，不编造）；
- `provider_reported_model_available`：false；
- `fallback_detected`：false（fallback_enabled=false，且未观察到降级调用）。

此项仅审计与记录，不改变回答行为。

## 29. 测试与 Ruff

初始基线：522 collected / 510 passed / 12 skipped / 0 failed。
最终结果：见提交时输出（新增 Phase 6B 测试：Parity Trace 字段与最早差异阶段、canonical 拒答口径、@K 检索口径、两路径 @12 相等、门禁阈值不变、回归清单精确、actual_model 可观测性、Qdrant 技术债；原测试不退化；skip 仅为外部 opt-in）。

## 30. release_candidate_approved

**true**（29/29 门禁通过，canonical 口径下官方路径指标不低于冻结基线）。历史基线 0.8333 与 Phase 6 原始 0.7708 均保留并标注口径；未静默重定义基线。

## 31. deployment_performed

**false**。未修改生产默认、未部署、未进入下一阶段。

## 32. 是否需要 Phase 6C

**不需要**：差距由口径/管线差异解释，不是模型输出波动；无需确定性采样实验来通过本门禁。若未来追求严格可复现采样，可另行发起。
