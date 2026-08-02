# Phase 5 报告：Grounded Answer Contract（证据上下文规范化、结构化回答、引用验证与安全拒答）

**日期**: 2026-08-01
**分支**: `codex/knowledge-qa-platform-design`
**阶段**: Phase 5（在冻结 PyMuPDF 检索结果上只改造答案生成层）

---

## 1. 阶段结论

- Phase 5A 上下文规范化 **CN1（stable_unique_fill）通过全部离线门禁**，冻结为 `selected_context_strategy=stable_unique_fill`（仅实验配置，不直接改生产默认）。
- Grounded Answer Pipeline（GA1）**结构性引用验证 100% 通过**（50/50 schema 合法、所有发出引用均可追溯、Invalid Chunk/Page/Document Rate=0、Uncited Claim Rate=0），且 non_gold_citation_reference_rate（historical: Unsupported Citation Reference Rate）从 0.6838 大幅降至 0.1429。
- 但 GA1 **误拒答从 0.3125 升至 0.6042**，Answer Citation Accuracy 0.8333→0.3542、Citation Recall 0.7212→0.3090，安全警告类 0.8→0.2，参数类 0.85→0.50，P95 延迟为基线 6.4 倍。
- **替换门禁未通过**：`replacement_approved=false`、`answer_strategy=current`、`citation_validation_enabled=false`、`max_repair_attempts=0`。
- 生产默认未修改；Phase 5 按要求**立即停止**，不自动进入下一阶段。

**Closeout 修正（Phase 5B 前置，2026-08-01）**：CN1（stable_unique_fill）虽然通过离线检索门禁，但答案层评估显示其相对 current_rows 无收益；**跨页类 6 题在 CN0（Phase 4 R0 答案）与 CN1（Phase 5 GA0）两个答案阶段均全部拒答**，早期报告中"CN1 导致跨页退化"的说法依据错误，已撤回。结论保持不变：**CN1 离线通过不等于生产批准**，最终生产上下文策略固定为 `current_rows`，`CONTEXT_STABLE_DEDUP_ENABLED=false`。详见 Phase 5B 报告。

## 2. Git commit

- 基线：`dd9ce80`。
- 本阶段提交：`fix(phase5): audit duplicate retrieval rows and citation metrics`、`feat(phase5): add deterministic evidence context normalization`、`feat(phase5): implement grounded answer and citation validation`、`eval(phase5): compare grounded answer against current pipeline`、`docs(phase5): report grounded answer evaluation`（最终 hash 见提交记录）。

## 3. Phase 4 最终策略

```json
{
  "parser_pipeline": "pymupdf_standard_adapter",
  "query_mode": "mix",
  "top_k": 12,
  "chunk_top_k": 20,
  "parent_expansion": "none",
  "rerank_enabled": false
}
```

qwen3-rerank 仅作为历史研究结果保留，不进入生产默认。

## 4. Phase 5 冻结基线

`evaluation/experiments/phase5/baseline_manifest.json`：

- source_phase=Phase 4D-R2；source_commit=`dd9ce80`；
- answer_model=qwen-plus-2025-07-28；fallback=false；thinking=false；
- 黄金集 SHA256=`fc52600fcce019d7f3cab04e0d0306ce336c468873ba2aef44391cc863e37aaf`；
- 冻结候选池 SHA256=`fc731efc904d9d9dca639fecf181a01e022c162ac91b67f6432d18b7619bf6a0`（一致，未变）；
- phase4_frozen_index：KB `8fce4626859d44abb70a9ae5b0372cea` / generation `g5162e7fb4208635103ff4ebb`（453/1012/1061）；
- Prompt bundle SHA256=`a39582ea101251af23051727ed9a746ea597e5acd8cc3b168520219a55182719`；
- Phase 4 答案基线（CN0）SHA256=`3a2fbec4df5b3df6b9eec206254e302a0bcbc68183e2e303d14dfb367e2d9e8c`；
- canonical metric definition=phase5-metrics-v1；canonical category manifest SHA256=`71e2176c784e16c8f41deb73869bfd9abd5636688c4a64ded552128d615de76d`。

## 5. 重复 Chunk 审计

`tech_debt/RETRIEVAL-DUPLICATE-001.md` + `context_normalization/duplicate_audit.json`：

- 50 题共 998 行；**重复行 4 行**，集中在 **C003、C004、C007、C008**；
- C007：19 行 / 18 唯一；`e05e769c5e5d`（page 24，同一 text_hash）在 rank 2 与 rank 5 重复；
- C003/C004/C008：20 行 / 19 唯一，同一 chunk 在 rank 5 重复；
- 来源判断：mix 多通道召回合并时缺少按 chunk_id 的最终去重（RRF/mix dedup 遗漏），不是文本/页码不一致；
- 影响：重复行占用上下文名额、放大该 chunk 的上下文权重；对 Phase 4D-R2 Rerank 按行保留（多重集合契约）无影响；
- 处理：本阶段只在答案上下文组装层稳定去重；**未修改 frozen candidate pool，未重写历史结果**。

## 6. Citation 指标定义审计

`metrics_definition.json` + `tech_debt/CITATION-METRIC-001.md`：

审计结论：Phase 4D-R2 的 `Citation Accuracy`（per-question）与 `Unsupported Citation Rate`（per-citation）**不是互补关系**，继续使用旧名称易混淆。

重命名（保留历史值，历史指标未修改）：

| historical_name | canonical_name | 单位 |
|---|---|---|
| Citation Accuracy | answer_citation_accuracy | per-question |
| Citation Precision | answer_citation_precision | per-question mean |
| Citation Recall | answer_citation_recall | per-question mean |
| Citation Traceability | citation_traceability | per-question |
| （新增） | citation_traceability_emitted | 仅含发出引用的题 |
| Unsupported Citation Rate | non_gold_citation_reference_rate（historical: unsupported_citation_reference_rate） | per-citation |
| （新增） | gold_citation_reference_rate（互补，同分母） | per-citation |
| Unsupported Answer Rate | negative_unsupported_answer_rate | per-question（N=2） |

Phase 4D-R2（R1 臂）原始计数：Answer Citation Accuracy 43/48=0.8958；non_gold_citation_reference_rate（historical: unsupported_citation_reference_rate）93/139=0.6691；gold_citation_reference_rate 46/139=0.3309；Citation Precision 16.1667/48=0.3368；Citation Recall 37.9167/48=0.7899；Traceability 48/48=1.0；False Rejection 14/48=0.2917；Negative Rejection 2/2=1.0。

## 7. Context Normalization 实验（CN0 vs CN1）

`context_normalization/{baseline,normalized}.jsonl`、`duplicate_audit.json`、`metrics.json`：

### 检索指标（48 题）

| 指标 | CN0 | CN1 |
|---|---|---|
| Recall@1/3/5/12 | 0.5625/0.6875/0.7500/0.7917 | 同 CN0 |
| MRR@5 | 0.6201 | 0.6201 |
| Gold Document Recall | 1.0000 | 1.0000 |
| Gold Page Recall | 0.8542 | 0.8542 |
| Gold Evidence Recall | 0.7917 | 0.7917 |
| Evidence Precision@5 | 0.2000 | 0.2000 |
| Evidence Precision@12 | 0.1024 | 0.1042 |

### 去重效果

- 删除重复行 4 行（C003/C004/C007/C008 各 1 行）；
- 每题输出 12 个唯一 chunk（effective_context_k=min(12, unique_count)=12）；
- 无池外、无文本/页码/文档修改、首次出现保留原 rank、确定性；
- Token：CN1 总 token +299（用新唯一候选补齐）；重复 token 占比 0.0004→0。

### 门禁

全部 15 项通过 → `selected_context_strategy=stable_unique_fill`（不直接修改生产默认 `CONTEXT_STABLE_DEDUP_ENABLED=false`）。

## 8. Grounded Answer Contract

统一内部结构（`grounded_answer/schemas/grounded_answer_schema_v1.json`）：

```json
{
  "status": "answered | insufficient_evidence",
  "answer": "string",
  "claims": [
    {
      "claim_id": "C1",
      "text": "string",
      "claim_type": "fact | parameter | procedure | safety | troubleshooting",
      "citations": [{"chunk_id": "string", "document_name": "string", "page": 1}]
    }
  ],
  "refusal_reason": null
}
```

约束：answered 必须有 claims；每个 claim 必须有引用；引用必须含 chunk_id/文档/页码；insufficient_evidence 时 claims 为空；拒答不得携带事实性答案；禁止自由文本不可解析引用。生产 API 的 `answer/citations/refusal/request_id` 等原有字段保持不变，`claims` 等新字段为可选。

## 9. Prompt 与 Schema

- `prompt_bundle/prompt_bundle_v1.json`：17 条固定规则 + 固定系统提示（证据、引用来源、JSON 输出格式）+ 固定 repair prompt；版本 v1；SHA256 已冻结并写入 baseline manifest。
- 上下文以 `[来源：文件名，第N页]` + `[chunk_id：...]` 标注，确保模型引用真实 chunk_id。
- 系统 Prompt 按问题只替换 `{context}`，不按题目动态改写规则。

## 10. Citation Validator

确定性校验（`grounded_answer/core.py::validate_citations`）：

- chunk_id 必须在当前问题上下文中；document_name/page 必须与上下文一致；文本 hash 由冻结池注册表保证（构造时校验）；
- 不跨 KB、引用字段完整、claim 内重复引用识别；
- answered 无 claims、refusal 带 claims、无引用 claim、非法 status 均拒绝；
- **Validator 从不改写引用、不猜测页码**。

## 11. Support Validator

实验专用（生产不读黄金答案）：

- Gold Document/Page/Evidence 命中统计（exact=chunk_id 命中）；
- fuzzy evidence：无确定性模糊映射 → **N/A**（不编造，不使用 LLM Judge）；
- 完全无支持引用、遗漏黄金证据计数均输出到逐题记录与汇总。

## 12. Repair 机制

- 最多 1 次 repair；同模型（qwen-plus-2025-07-28）、fallback=false、thinking=false；固定 repair prompt（原问题+原证据+原始输出+Validator 错误）；
- repair 后仍失败 → 安全降级 `insufficient_evidence`（`grounded_answer_invalid_after_repair`），保存原始输出与失败原因；
- 不新增上下文、不调用检索/Rerank、不使用黄金答案。

## 13. GA0 结果（current pipeline，CN1 上下文）

50/50 完成，49 次 LLM 调用（N001 由 Evidence Policy 拒绝，0 调用）：

- Answer Citation Accuracy 40/48=0.8333；Precision 15.8333/48=0.3299；Recall 34.6167/48=0.7212；
- Traceability（per-question）48/48=1.0；Gold Page Citation 40/48=0.8333；Gold Evidence Citation 37/48=0.7708；
- non_gold_citation_reference_rate 93/136=0.6838（gold 43/136=0.3162）；Answered Without Evidence 0/48；
- False Rejection 15/48=0.3125；Negative Rejection 2/2=1.0；Unsupported Answer 0/2=0；
- 分类：参数 0.85、表格 0.6667、操作 0.5556、故障 0.6667、安全 0.8、普通 0.5、跨页 0.0（6 题全部拒答）、证据不足 2/2 拒答；
- Token：input 43,220 / output 4,066 / total 47,286；延迟 mean 1.885s / P50 1.75s / P95 3.875s（来自原始调用记录）。

## 14. GA1 结果（grounded pipeline，CN1 上下文）

50/50 完成，49 次初始调用 + 20 次 repair：

- Answer Citation Accuracy 17/48=0.3542；Precision 16.5/48=0.3438；Recall 14.8333/48=0.3090；
- Traceability（per-question）19/48=0.3958；**Traceability（emitted）19/19=1.0**；Gold Page Citation 17/48=0.3542；Gold Evidence Citation 16/48=0.3333；
- **non_gold_citation_reference_rate 3/21=0.1429（gold 18/21=0.8571）**；Answered Without Evidence 0/48；
- False Rejection 29/48=0.6042；Negative Rejection 2/2=1.0；Unsupported Answer 0/2=0；
- 结构：Schema Valid 50/50=1.0；Structural Citation Valid 50/50=1.0；Invalid Chunk/Page/Document Rate=0/21；Uncited Claim Rate=0/46；Repair Trigger 20/50=0.40；Repair Success 3/20=0.15；Safe Fallback 17/50=0.34；
- Token：input 136,511 / output 29,765 / repair 55,276 / total 166,276；延迟 mean 6.483s / P50 7.453s / P95 24.625s。

## 15. Citation 指标对比

| canonical 指标 | GA0 | GA1 |
|---|---|---|
| answer_citation_accuracy | 40/48=0.8333 | 17/48=0.3542 |
| answer_citation_precision | 15.8333/48=0.3299 | 16.5/48=0.3438 |
| answer_citation_recall | 34.6167/48=0.7212 | 14.8333/48=0.3090 |
| citation_traceability（per-question） | 48/48=1.0 | 19/48=0.3958 |
| citation_traceability_emitted | 48/48=1.0 | 19/19=1.0 |
| gold_page_citation_rate | 40/48=0.8333 | 17/48=0.3542 |
| gold_evidence_citation_rate | 37/48=0.7708 | 16/48=0.3333 |
| non_gold_citation_reference_rate | 93/136=0.6838 | **3/21=0.1429** |
| gold_citation_reference_rate（互补） | 43/136=0.3162 | 18/21=0.8571 |
| answered_without_evidence_rate | 0/48=0 | 0/48=0 |

## 16. 拒答指标

| 指标 | GA0 | GA1 |
|---|---|---|
| insufficient_evidence_rejection_rate | 2/2=1.0 | 2/2=1.0 |
| negative_unsupported_answer_rate | 0/2=0 | 0/2=0 |
| false_rejection_rate（48 题） | 15/48=0.3125 | 29/48=0.6042 |

N001/N002 两组均正确拒答；GA1 的 N002 同样在干扰证据下由模型按规则拒答。

## 17. 安全警告类别（5 题）

| 项 | GA0 | GA1 |
|---|---|---|
| Citation Accuracy | 0.8（4/5，1 题拒答） | 0.2（1/5，3 题拒答 + 1 题错误引用） |
| 无引用安全 Claim | 0 | 0 |
| 联锁旁路建议 | 0 | 0 |
| 安全错误题数 | 1（S017 拒答计入错误） | 2 |

GA1 安全类**退化**（0.8→0.2），是替换门禁失败的主要原因之一。

## 18. 分类结果（Citation Accuracy）

| 类别（题数） | GA0 | GA1 |
|---|---|---|
| 参数查询（20） | 0.8500 | 0.5000 |
| 表格查询（3） | 0.6667 | 0.3333 |
| 操作步骤（9） | 0.5556 | 0.2222 |
| 故障诊断（3） | 0.6667 | 0.0000 |
| 安全警告（5） | 0.8000 | 0.2000 |
| 普通事实（2） | 0.5000 | 0.5000 |
| 跨页问题（6） | 0.0000 | 0.3333 |
| 证据不足（2） | 2/2 拒答 | 2/2 拒答 |

注意：GA0（CN1 上下文）的跨页类 6 题全部拒答；核对 Phase 4 R0（CN0 上下文）答案后确认 CN0 同样 6/6 拒答，因此这不是 CN1 引入的退化。GA1 在跨页类优于 GA0（0.3333）。

## 19. 配对统计（48 题，1000 次，seed=20260801，95% CI）

| 指标 | mean Δ | 95% CI | 跨 0 |
|---|---|---|---|
| answer_citation_accuracy | -0.4792 | [-0.6250, -0.3333] | 否 |
| answer_citation_precision | -0.0139 | [-0.1458, 0.1285] | 是 |
| answer_citation_recall | -0.4122 | [-0.5382, -0.2809] | 否 |
| gold_evidence_citation_rate | -0.4375 | [-0.5833, -0.2917] | 否 |
| uncited_claim_rate | 0.0000 | [0.0, 0.0] | 否 |
| false_rejection_rate | +0.2917 | [0.1250, 0.4583] | 否 |

Citation Precision 置信区间跨 0：观察到变化，但当前 50 题黄金集不足以证明稳定差异。其余指标的退化是统计显著的。

## 20. Token 与延迟

| 工程项 | GA0 | GA1 |
|---|---|---|
| LLM 调用数 | 49 | 49 初始 + 20 repair |
| input / output / repair / total tokens | 43,220 / 4,066 / 0 / 47,286 | 136,511 / 29,765 / 55,276 / 166,276 |
| 平均延迟（s） | 1.885 | 6.483 |
| P50（s） | 1.750 | 7.453 |
| P95（s） | 3.875 | 24.625 |
| 错误数 / fallback | 0 / 0 | 0 / 0 |

GA1 总 Token 为 GA0 的 3.5 倍，P95 延迟为 6.4 倍（硬门禁要求 <=2 倍，未通过）。

## 21. 提升问题

- **无一题**在 Citation Accuracy（≥1 条黄金页引用）上由失败转成功（improved=0）；
- 结构层面：所有发出引用 100% 可追溯、无池外引用、无未引用 Claim（GA0 无此结构保证）；
- non_gold_citation_reference_rate 下降 0.5409（93/136 → 3/21），引用与黄金标注一致度显著提升；
- 跨页类 0.0→0.3333（2/6 题从拒答变为有正确引用回答）。

## 22. 退化问题

23 题由 GA0 正确变为 GA1 错误（主要原因是 GA1 拒答）：

S003、S005、S007、S011、S012、S015、S017、S019、S020、D003、D004、D005、D006、D009、D011、D012、D013、D017、D020、C005、C006、C007、C008。

另有 25 题保持正确/失败状态不变。

## 23. 最终决策

```json
{
  "answer_strategy": "current",
  "replacement_approved": false,
  "replacement_gates_passed": false,
  "selection_reason": "Grounded Answer Pipeline did not pass Phase 5 replacement gates"
}
```

硬门禁失败项：安全警告类 Citation Accuracy 下降（0.8→0.2）；参数查询类下降超过 0.02（0.85→0.50）；False Rejection 恶化超过 0.05（+0.2917）；P95 延迟超过基线 2 倍（24.625s vs 3.875s）。

价值门禁仅满足 1/7：non_gold_citation_reference_rate 降低 >=0.10（实际 -0.5409）。

## 24. 是否替换默认策略

**否**。`GROUNDED_ANSWER_ENABLED=false`、`CITATION_VALIDATION_ENABLED=true`（仅实验）、`GROUNDED_ANSWER_MAX_REPAIR_ATTEMPTS=1`（仅实验）、`CONTEXT_STABLE_DEDUP_ENABLED=false`；未修改部署环境；旧客户端字段与行为不变。

## 25. 测试与 Ruff

```text
python -m pytest --collect-only -q
python -m pytest -q
python -m ruff check .
```

结果见提交时输出（新增 20 项 Phase 5 测试：冻结配置/哈希、重复审计、stable dedup、schema、CitationValidator、Repair、安全门禁、指标分母、门禁、API 兼容；原测试不退化；skip 仅为外部 opt-in）。

## 26. 已知限制

- GA1 拒答率高（60.4%）主要来自严格规则 + 固定 repair 低成功率（15%）：模型在"只回答可支持部分/证据不足即拒答"约束下过度保守；未引入 LLM Judge，Claim Support 确定性部分为 N/A；
- GA0 的跨页类在 CN1 与 CN0（Phase 4 R0 答案）两个答案阶段均 6/6 拒答；本报告 GA0/GA1 均基于 CN1，与 Phase 4D-R2 的 R0/R1 不完全可比；
- `citation_traceability`（per-question）对无引用拒答计 0，而 `citation_traceability_emitted` 只统计发出引用的题；两指标并存并分别报告；
- 费用：LLM SDK 未提供人民币金额，费用 N/A，仅记录真实 Token；
- 冻结池 C003/C004/C007/C008 的既有重复行如实保留，未修改；
- GA1 实验中 1 个 S001 初始调用命中缓存，其余为真实调用；GA0 全部 49 调用为首次真实调用后缓存命中，延迟取缓存中的原始调用记录。

## 27. 下一阶段是否允许

Phase 5 评估已完成（`replacement_gates_passed=false` 也是完成状态）。按指示**立即停止，不自动进入下一阶段**；不存在被 Phase 5 阻塞的后续阶段，是否继续由用户决定。
