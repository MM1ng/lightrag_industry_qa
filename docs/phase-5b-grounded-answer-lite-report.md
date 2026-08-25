# Phase 5B 报告：Grounded Answer Lite（自然语言 + 轻量引用约束 + 局部修复 + Claim 级安全控制）

**日期**: 2026-08-01
**分支**: `codex/knowledge-qa-platform-design`
**阶段**: Phase 5B（在冻结 PyMuPDF 检索与 current_rows 上下文上只改造答案生成层）

---

## 1. 阶段结论

- **GL1（inline citation）通过进入 GL2 的全部门禁**：marker 解析 126/126=1.0、无效标记 0、emitted traceability 1.0、误拒答不升、安全类不降超 0.10。
- **GL2（citation-only repair）运行完成**：14 题触发 repair、13 题成功（92.86%），答案正文 hash 前后一致。
- **GL2 → GL3 门禁未通过**，按规则停止，GL3 未运行：
  - `no_repair_errors` 失败：C008 的 repair 输出是 JSON + 尾随文本（"Extra data"），程序判定 repair 失败；
  - `p95_latency_leq_2x` 失败：GL2 P95=9.406s vs GL0 P95=4.109s（2.29 倍）。
- 最终候选为 **GL2**，但因未到 GL3（claim-level guard 未评估），**替换门禁不通过**：`answer_strategy=current`、`citation_mode=current`、`replacement_approved=false`。
- 生产默认未修改；按要求立即停止，不自动进入下一阶段。

## 2. Git commit

- 基线：`2ff697a9d356d99ea8431e74311f5c78229982a5`。
- 本阶段提交：`fix(phase5): reject stable dedup at answer level and rename citation metric`、`feat(phase5b): add inline chunk citation pipeline`、`feat(phase5b): add citation-only repair and claim-level guard`、`eval(phase5b): compare grounded answer lite against current pipeline`、`docs(phase5b): report lightweight grounding evaluation`。

## 3. Phase 5 Closeout

完成两项修正（离线，未调用模型）：

1. **CN1 状态修正**：`offline_gates_passed=true`、`answer_level_evaluation_completed=true`、`answer_level_approved=false`、`production_enabled=false`；生产上下文固定 `current_rows`。
2. **Citation 指标改名**：`unsupported_citation_reference_rate` → `non_gold_citation_reference_rate`（保留 historical_name），新增互补指标 `gold_citation_reference_rate`（同分母下二者之和=1.0）；历史值未修改。

## 4. CN1 生产拒绝结论

```json
{
  "offline_gates_passed": true,
  "answer_level_evaluation_completed": true,
  "answer_level_approved": false,
  "production_enabled": false,
  "selection_reason": "Stable dedup preserved retrieval metrics, but answer-level evaluation showed no benefit over current_rows; cross-page questions were refused 6/6 in BOTH the CN0 and CN1 answer stages (Phase 4 R0 answers), so the earlier claim of CN1-caused cross-page regression was retracted. Production stays current_rows."
}
```

重要事实修正：核对 Phase 4 R0（CN0）答案文件后确认，**跨页 6 题在 CN0 与 CN1 两个答案阶段均全部拒答**，Phase 5 报告中"CN1 导致跨页退化"的表述已撤回；决策（不使用 CN1）不变。

## 5. Citation 指标命名修正

见 `evaluation/experiments/phase5/tech_debt/CITATION-SUPPORT-002.md`：

- canonical：`non_gold_citation_reference_rate`（发出的引用中未命中黄金标注的比例）；
- historical：`unsupported_citation_reference_rate`；
- 互补：`gold_citation_reference_rate = gold_matching / emitted`；同分母下 `gold + non_gold = 1.0`；
- 明确声明：non-gold ≠ unsupported；黄金证据可能非穷尽；无 Claim Support Judge 时不得声称引用不支持答案。

Phase 4D-R2 R1 原始值：non_gold=93/139=0.6691，gold=46/139=0.3309。

## 6. 冻结基线

`evaluation/experiments/phase5b/baseline_manifest.json`：

- source_commit / head_commit = `2ff697a9d356d99ea8431e74311f5c78229982a5`；
- parser=pymupdf_standard_adapter；query_mode=mix；top_k=12；chunk_top_k=20；parent_expansion=none；rerank=false；**context_strategy=current_rows**；answer_model=qwen-plus-2025-07-28；fallback=false；thinking=false；
- 候选池 SHA256=`fc731efc904d9d9dca639fecf181a01e022c162ac91b67f6432d18b7619bf6a0`（一致）；
- 黄金集 SHA256=`fc52600fcce019d7f3cab04e0d0306ce336c468873ba2aef44391cc863e37aaf`；
- prompt SHA256 见 `prompts/prompt_manifest.json`（inline citation / citation repair / frozen config）。

## 7. Phase 5 失败原因分类

`diagnostics/phase5_failure_taxonomy.json`（只使用已保存的 GA0/GA1 输出字段分类，23 道退化题）：

| primary_failure_type | 数量 |
|---|---|
| repair_failed_structure（repair 失败后安全降级拒答） | 16 |
| model_overcautious_refusal（模型/repair 后仍拒答） | 5 |
| other（结构合法但黄金引用未命中） | 2 |

另有 `refusal_analysis.json`（31 条 GA1 拒答）与 `repair_analysis.json`（20 次 repair）供审计。Phase 5 GA1 高拒答主因是：严格 JSON schema 下 repair 成功率仅 15%，大量题目在 repair 失败后被强制降级拒答。

## 8. Grounded Answer Lite 设计

- 自然语言回答 + 句子尾引用标记 `[引用:E1,E2]`（最多 2 个）；
- 上下文证据块含 `引用别名: E1`、`chunk_id`、`来源`、`页码`、`正文`；白名单列出别名与完整 chunk_id；
- 模型只输出别名/完整 id，**不输出页码与文档名**；程序按注册表确定性补全 `{chunk_id, document_name, page}`；
- KeyClaimDetector 固定规则识别参数/单位/型号/步骤/安全/故障句；GL2 只修复"句子→引用"映射，答案正文逐字符不变；GL3 按句剪除无引用关键 Claim（本阶段未到 GL3）。

## 9. GL0 结果（current_rows 基线，复用 Phase 4 R0）

- 复用条件全部满足：current_rows、qwen-plus-2025-07-28、fallback=false、thinking=false、同一 Answer Prompt（按 Phase 4 答案缓存键逐题验证）、同一候选池、top12 顺序一致、同一 Evidence Policy；
- 48 题：Answer Citation Accuracy 40/48=0.8333；Recall 34.6167/48=0.7212；Gold Evidence Citation 37/48=0.7708；
- non_gold=93/136=0.6838；gold=43/136=0.3162；
- False Rejection 15/48=0.3125；N001/N002 拒答 2/2、Unsupported Answer 0/2；
- 分类：参数 0.85、表格 0.6667、操作 0.5556、故障 0.6667、安全 0.8、普通 0.5、跨页 0.0（6/6 拒答）；
- 工程：49 次调用、47,446 tokens、P50=1.484s、P95=4.109s（延迟取自 Phase 4 答案缓存中的原始调用记录）。

## 10. GL1 结果（inline citation）

- 50 题完成、49 次调用（N001 由策略拒绝）；126 个 marker，解析率 126/126=1.0，无效 marker 0；
- Key Claim 覆盖：总 115/138=0.8333；参数 32/33=0.9697；步骤 62/76=0.8158；安全 23/28=0.8214（5 个无引用安全 Claim）；故障 24/36=0.6667；
- Answer Citation Accuracy 34/48=0.7083；Recall 31.0/48=0.6458；Gold Evidence 32/48=0.6667；
- non_gold=20/56=0.3571（较 GL0 下降 0.3267）；gold=36/56=0.6429；
- False Rejection 8/48=0.1667（较 GL0 下降 0.1458）；N 题拒答 2/2；
- 拒答 10 题：C001/C002/C003/C005/C006（跨页 5/6）、D016、S007（表格）、S020、N001/N002；
- 工程：75,143 tokens；P50=1.968s、P95=3.891s。

## 11. GL2 结果（citation-only repair）

- 触发 14 题（28%）：C004、C008、D003、D005、D006、D013、D014、D018、D019、D020、S011、S012、S015、S017；
- 成功 13/14=92.86%；唯一失败 C008：repair 输出为 JSON + 尾随文本（"Extra data"），判定失败；
- 答案正文 hash before/after 全部一致（逐字符未改）；
- Key Claim 覆盖提升到 132/138=0.9565：参数 33/33=1.0、步骤 72/76=0.9474、安全 26/28=0.9286（剩余 2 个无引用安全 Claim）、故障 33/36=0.9167；
- Citation Accuracy 34/48=0.7083（与 GL1 相同）；non_gold=22/58=0.3793；
- 工程：repair tokens 27,132；总 tokens 102,275；P50=2.078s、P95=9.406s。

## 12. GL3 结果或停止原因

**未运行**。GL2 → GL3 门禁：

| 门禁 | 结果 |
|---|---|
| answer_text_hash_unchanged | 通过 |
| no_repair_errors | **失败**（C008 repair JSON 尾随文本） |
| repair_success_rate >= 0.50 | 通过（0.9286） |
| p95_latency <= 2×GL0 | **失败**（9.406 vs 8.218） |
| false_rejection 不升超 0.05 | 通过 |

按规范停止后续实验，如实报告；Claim-level Guard 未在完整 50 题上评估（单元测试已覆盖其确定性行为）。

## 13. Marker 合法性

| 指标 | GL1 | GL2 |
|---|---|---|
| marker_parse_valid_rate | 126/126=1.0 | 126/126=1.0 |
| invalid_chunk_marker_rate | 0/126=0 | 0/126=0 |
| emitted_citation_traceability | 40/40=1.0 | 40/40=1.0 |
| 单句最多 2 引用 / 重复去重 / 元数据补全 | 通过 | 通过 |

开发集阶段发现并修复：模型会截断含中文前缀的长 chunk_id（如只输出 `c99ee01e2d93-000-...`），因此引入确定性短别名 `E1/E2/...`；截断 id 仍被拒绝（无模糊匹配）。

## 14. Key Claim 覆盖

| 类型 | GL0 | GL1 | GL2 |
|---|---|---|---|
| 总关键句 | 0/173=0 | 115/138=0.8333 | 132/138=0.9565 |
| 参数 | 0/39 | 32/33=0.9697 | 33/33=1.0 |
| 步骤 | 0/80 | 62/76=0.8158 | 72/76=0.9474 |
| 安全 | 0/24 | 23/28=0.8214 | 26/28=0.9286 |
| 故障 | 0/36 | 24/36=0.6667 | 33/36=0.9167 |

GL0 无引用标记机制，覆盖率为 0（基线口径）。

## 15. Citation-only Repair

- 触发率 14/50=28%；成功率 13/14=92.86%；
- 修复只输出 `{"sentence_citations": [...]}` 映射，无新答案/新事实/拒答文本；
- before/after answer hash 全部一致；池外/别名无效引用被拒绝；
- C008 为唯一 repair 失败（输出含尾随文本），失败后保留原答案正文、删除无效引用、不整题拒答。

## 16. Claim 级 Guard

GL3 未在完整 50 题运行；设计为按句剪除：普通/参数/步骤/故障/安全无引用关键句分别删除，全部删除才转 insufficient_evidence，不因单句失败拒答整题。确定性单元测试通过（无引用句删除、全删拒答、保留合法句）。

## 17. 分类结果（Citation Accuracy）

| 类别（题数） | GL0 | GL1 | GL2 |
|---|---|---|---|
| 参数查询（20） | 0.8500 | 0.9000 | 0.9000 |
| 表格查询（3） | 0.6667 | 0.6667 | 0.6667 |
| 操作步骤（9） | 0.5556 | 0.6667 | 0.6667 |
| 故障诊断（3） | 0.6667 | 0.6667 | 0.6667 |
| 安全警告（5） | 0.8000 | 0.8000 | 0.8000 |
| 普通事实（2） | 0.5000 | 0.5000 | 0.5000 |
| 跨页问题（6） | 0.0000 | 0.1667 | 0.1667 |
| 证据不足（2） | 2/2 拒答 | 2/2 拒答 | 2/2 拒答 |

参数类从 0.85 提升到 0.90（无引用数值句经 repair 全覆盖）；跨页类 5/6 拒答（C001 回答正确 1 题），未重现 Phase 5 CN1 的 6/6，但仍偏高。

## 18. 安全警告结果（5 题）

| 项 | GL0 | GL1 | GL2 |
|---|---|---|---|
| Citation Accuracy | 0.8000 | 0.8000 | 0.8000 |
| 无引用安全 Claim 数 | 24 | 5 | 2 |
| safety_claim_citation_coverage | 0/24 | 23/28=0.8214 | 26/28=0.9286 |
| 联锁旁路建议 | 0 | 0 | 0 |

安全准确率未下降（硬门禁通过）；C007 在 GL1/GL2 为错误引用（GL0 为正确引用但同属 4/5 正确）。

## 19. 负样本结果

| 题 | GL0 | GL1 | GL2 |
|---|---|---|---|
| N001 | 拒答（evidence_policy_rejected，0 调用） | 同左 | 同左 |
| N002 | 拒答（模型自行拒答） | 拒答（model_refused） | 同左 |

Insufficient Evidence Rejection Rate=2/2=1.0；Unsupported Answer Rate=0/2=0（三组一致）。

## 20. 配对统计（48 题，1000 次，seed=20260801，95% CI；GL0 vs GL2）

| 指标 | mean Δ | 95% CI | 跨 0 |
|---|---|---|---|
| answer_citation_accuracy | -0.1250 | [-0.2292, -0.0417] | 否 |
| answer_citation_recall | -0.0753 | [-0.1476, -0.0215] | 否 |
| gold_evidence_citation_rate | -0.1042 | [-0.1875, -0.0208] | 否 |
| false_rejection_rate | **-0.1458** | [-0.2500, -0.0417] | 否 |
| safety_claim_citation_coverage | +0.2865 | [0.1667, 0.4115] | 否 |
| key_claim_citation_coverage | +0.8128 | [0.7010, 0.9057] | 否 |

说明：按 canonical 口径，拒答（无引用）计入 accuracy/recall 失败；GL1/GL2 拒答减少但集中在跨页/表格类，因此整体 accuracy 下降（统计显著）。覆盖类指标显著提升。

## 21. Token 与延迟

| 工程项 | GL0 | GL1 | GL2 |
|---|---|---|---|
| LLM 调用 | 49 | 49 | 49 + 14 repair |
| input / output tokens | 43,250 / 4,196 | 70,792 / 4,351 | 70,792 / 4,351 |
| repair tokens | 0 | 0 | 27,132 |
| 总 tokens | 47,446 | 75,143 | 102,275 |
| P50 / P95（s） | 1.484 / 4.109 | 1.968 / 3.891 | 2.078 / 9.406 |
| 错误 / fallback | 0 / 0 | 0 / 0 | 0 / 0 |

GL2 P95 因 repair 调用（约 4-8s/次）超过 GL0 的 2 倍，是 GL2→GL3 门禁失败的原因之一。

## 22. 提升问题

- 无题目在 Citation Accuracy 上由失败转成功（improved=0）；
- 结构性/覆盖性提升：False Rejection 15→8（7 题由拒答转为有引用回答）；参数句引用覆盖 0→100%；关键句覆盖 0→95.7%；non_gold 引用率 0.6838→0.3793（GL2）；N 题拒答保持 2/2。

## 23. 退化问题

按 canonical 口径（引用是否命中黄金页），GL0→GL2 退化 6 题：

C003、C005、C006、C007、S007、S020。

原因：这些题在 GL0 中拒答但仍携带策略引用（计入准确率），GL1/GL2 拒答时无引用，按口径记为失败；其中 S007（表格）、C003/C005/C006（跨页）在 GL1 中仍拒答。

## 24. 最终决策

```json
{
  "context_strategy": "current_rows",
  "answer_strategy": "current",
  "citation_mode": "current",
  "replacement_approved": false,
  "replacement_gates_passed": false,
  "selection_reason": "Grounded Answer Lite did not pass Phase 5B replacement gates"
}
```

实际原因：GL2→GL3 门禁失败（repair 错误 + P95 延迟超 2 倍），GL3 未运行，claim-level guard 未进入完整评估；GL2 自身也不满足"最终候选必须包含 claim-level guard"的通过条件。

## 25. 是否替换默认

**否**。生产默认未修改：`GROUNDED_ANSWER_LITE_ENABLED=false`、`INLINE_CHUNK_CITATION_ENABLED=false`、`CITATION_ONLY_REPAIR_ENABLED=false`、`CLAIM_LEVEL_GUARD_ENABLED=false`；`GROUNDED_ANSWER_ENABLED=false`、`CONTEXT_STABLE_DEDUP_ENABLED=false`、`RERANK_ENABLED=false` 保持不变；API 原字段（answer/citations/refusal/request_id/trace_id）未删除，新字段均为可选。

## 26. 测试与 Ruff

初始基线：474 collected / 462 passed / 12 skipped / 0 failed。
最终结果：见提交时输出（新增 22 项 Phase 5B 测试：Closeout、冻结、marker、KeyClaim、repair、claim guard、指标、门禁、兼容性；原测试不退化；skip 仅为外部 opt-in）。

## 27. 已知限制

- GL1/GL2 拒答按 canonical 口径不计引用，导致 accuracy/recall 统计下降；GL0 的"拒答仍带策略引用"口径与之不一致（历史口径保留，未修改）；
- 跨页类 5/6 拒答（GL1/GL2），虽然优于 Phase 5 CN1 的 6/6，但仍显著高于理想水平；
- 表格类 S007 在 GL1/GL2 拒答（GL0 拒答但带正确引用）；
- C008 repair 输出含尾随文本导致失败；repair prompt 对"只输出 JSON"的约束不总是被遵循；
- GL2 P95 延迟 9.4s（含 repair），未满足 2 倍门禁；如未来优化 repair 提示或异步化可再评估；
- Claim-level Guard（GL3）只经单元测试验证，未在完整 50 题上评估；
- 费用：SDK 未提供人民币金额，费用 N/A，仅记录真实 Token；
- 冻结池 C003/C004/C007/C008 既有重复行保留未改；GL0 延迟取自 Phase 4 缓存原始调用记录。

## 28. 下一阶段是否允许

Phase 5B 评估已完成（未批准替换也是完成状态）。按指示**立即停止，不自动进入下一阶段**；无 Phase 5B 阻塞的后续阶段，是否继续由用户决定。
