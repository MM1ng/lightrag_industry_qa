# Phase 4C 报告：固定 PyMuPDF 下的 Parent Expansion 消融实验

**日期**: 2026-08-01
**分支**: `codex/knowledge-qa-platform-design`

---

## 1. 阶段结论

- **Phase 4C complete**；最终 `parent_expansion = "none"`。
- 四组策略（none / top_1_parent / top_3_parents / adaptive）在冻结 Child 检索上完成离线评估；阶段二对 PE0 与最佳候选（top_1_parent）各运行 50 题完整回答。
- **Parent Expansion 未通过替换门禁**（硬门禁通过、价值门禁 0 项满足）：扩展 Parent 未提升 Expanded Gold Evidence/Page Coverage、Citation Recall，仅增加 Token 与延迟。
- 原始 Child 排名/分数在四组间完全一致（唯一变量有效）；不替换当前 none 默认。
- 未实施 Rerank；未进入下一子阶段。

## 2. Git commit

- 实验基线：`c7bcf59`
- 本阶段代码与结果提交：`0f6bee4`（feat）+ `566eddc`（docs）+ `0194d26`（Closeout 收尾）。

## 3. PyMuPDF 固定声明

- `parser_pipeline = pymupdf_standard_adapter`（唯一允许变量之外全部固定）。
- MinerU 未调用；未重新解析/切块；Parent/Child/ID/黄金集/问题分类均未修改。

## 4. 基线验证

- `evaluation/experiments/phase4/baseline_manifest.json`：`source_phase=Phase 3A-R-Paid`、`default_parser_pipeline=pymupdf_standard_adapter` 校验通过。
- 黄金集 SHA256 `fc52600f…`、P0 逐题结果 SHA256、Prompt bundle SHA256 均与 manifest 一致。
- 正式基线（P0）：Recall@1/3/5=0.5625/0.6875/0.7500，MRR=0.6167，Gold Page Recall=0.7917，Gold Evidence Recall=0.9167，Citation Accuracy=0.8958，False Rejection=0.2917。

## 5. 索引完整性（phase4_frozen_index）

| 项 | 值 |
|---|---|
| KB / generation | `8fce4626859d44abb70a9ae5b0372cea` / `g5162e7fb4208635103ff4ebb` |
| chunks / entities / relationships | 453 / 1,012 / 1,061 |
| 文档状态 | 2/2 processed，无 processing/failed/partial |
| 索引 LLM | 498 次调用，1,132,484 token，0 mismatch/error |
| 索引标记 | `phase4_frozen_index`（实验索引，保留供 Rerank；后续不重建） |

## 6. frozen Child 检索结果

- 文件：`evaluation/experiments/phase4/parent_expansion/frozen_child_results.jsonl`
- 998 行 / 50 题；SHA256：`fc731efc904d9d9dca639fecf181a01e022c162ac91b67f6432d18b7619bf6a0`
- 每行含 question_id、question、primary_category、child_chunk_id、parent_id、document_id、page、rank、retrieval_score、child_text_hash、query_mode/top_k/chunk_top_k。
- 四组策略全部读取该文件；无每组重新检索。

## 7. Parent Expansion 实现

- `parent_loader.py`：冻结 ParentChunk 加载，强制同文档、无跨 KB。
- `expander.py`：ExpandedEvidence（child 行与 parent 行分离；原始 Child 永远保留，rank/score 不变）。
- `context_builder.py`：Section A（Child）+ Section B（Parent），parent 去重、预算 6000 token、重复 token 统计、确定性排序、不摘要不改写。
- `provenance.py`：source_parent_id / supporting_child_id / actual_evidence_page / context_page_range / citation_page（引用始终落在 Child 页）。

## 8-11. 四组离线结果（阶段一）

| 指标 | none | top_1_parent | top_3_parents | adaptive |
|---|---|---|---|---|
| Child Recall@1/3/5 | 0.5625/0.6875/0.75 | 同左 | 同左 | 同左 |
| Child MRR@5（canonical） | 0.6201 | 同左 | 同左 | 同左 |
| Expanded Gold Evidence Coverage | 0.8333 | 0.8333 | 0.8333 | 0.8333 |
| Expanded Gold Page Coverage | 0.8958 | 0.8958 | 0.8958 | 0.8958 |
| Context Evidence Density | 0.4796 | 0.4652 | 0.4625 | 0.4625 |
| Context Token mean / P50 / P95 / max | 897 / 963 / 1312 / 1495 | 1208 / 1303 / 1788 / 2096 | 1793 / 1925 / 2721 / 2979 | 同左 |
| Parent 数 mean / P95 | 0 / 0 | 1.0 / 1 | 2.94 / 3 | 同左 |
| 重复 Context 比例 | 0 | 0.055 | 0.107 | 同左 |
| 超预算题数 | 0 | 0 | 0 | 0 |
| 跨页 Parent 数 | 0 | 9 | 26 | 同左 |

Child Recall/MRR 四组完全一致 → 唯一变量成立。Parent 扩展未带来任何 Coverage 提升（Child 已覆盖黄金证据），只增加 Token 与重复。

## 12. 上下文覆盖对比

- top_1/top_3/adaptive 的 Expanded Coverage 与 PE0 完全相同（0.8333/0.8958）——父上下文没有新增黄金证据命中。
- Density 随上下文膨胀单调下降（0.4796 → 0.4652 → 0.4625）。
- 结论：当前黄金集下 Parent 上下文是纯成本增量，无召回收益。

## 13. Token 对比

| 组 | 答案 LLM 调用 | input/output/total tokens | 平均延迟 | P50 | P95 |
|---|---|---|---|---|---|
| PE0 none | 49 | 49,633 / 421 / 50,054 | 2,176 ms | 1,797 ms | 5,078 ms |
| top_1_parent | 49 | 64,265 / 469 / 64,734 | 2,079 ms | 1,750 ms | 5,485 ms |

top_1 相对 PE0：Token +29.3%，P95 延迟 +8.0%。top_3/adaptive 离线估算 Token 约为 PE0 的 2 倍（未进入阶段二）。

## 14. 分类结果（阶段二，PE0 vs top_1）

| 分类 | PE0 Citation Acc | top_1 Citation Acc |
|---|---|---|
| 参数查询 (20) | 0.9500 | 0.9500 |
| 表格查询 (3) | 1.0000 | 1.0000 |
| 操作步骤 (9) | 0.7778 | 0.7778 |
| 故障诊断 (3) | 1.0000 | 1.0000 |
| 安全警告 (5) | 0.8000 | 0.8000 |
| 普通事实 (2) | 1.0000 | 1.0000 |
| 跨页问题 (6) | 0.8333 | 0.8333 |
| 证据不足 (2) | 拒答 ✅ | 拒答 ✅ |

分类无差异；Parent 扩展未改变任何类别的引用表现。

## 15. 完整答案对比

| 指标 | PE0 none | top_1_parent |
|---|---|---|
| Expanded Gold Evidence Coverage | 0.8333 | 0.8333 |
| Expanded Gold Page Coverage | 0.8958 | 0.8958 |
| Context Evidence Density | 0.4796 | 0.4652 |

逐题答案、引用、延迟、Token 见 `results/none/answers.jsonl` 与 `results/top_1_parent/answers.jsonl`。

## 16. 引用与拒答

| 指标 | PE0 | top_1 |
|---|---|---|
| Citation Accuracy | 0.8958 | 0.8958 |
| Citation Precision | 0.3576 | 0.3576 |
| Citation Recall | 0.7837 | 0.7837 |
| Citation Traceability | 1.0 | 1.0 |
| Unsupported Citation Rate | 0 | 0 |
| Insufficient Evidence Rejection | 1.0 | 1.0 |
| False Rejection Rate | 0.2708 | 0.2500 |
| Unsupported Answer Rate | 0 | 0 |

D008 在 PE0 被拒答、top_1 正常回答（引用一致）：top_1 的 False Rejection 降低 2.1pp，但低于 5pp 价值门槛。

## 17. 配对统计（48 题，1000 次 bootstrap，seed=20260801）

| 指标 | mean_diff | 95% CI | 跨 0 |
|---|---|---|---|
| Evidence Coverage | 0.0000 | [0.0000, 0.0000] | 否 |
| Citation Accuracy | 0.0000 | [0.0000, 0.0000] | 否 |
| Citation Recall | 0.0000 | [0.0000, 0.0000] | 否 |
| False Rejection | -0.0208 | [-0.0625, 0.0000] | 否（上界触 0） |

证据覆盖、引用准确率与引用召回完全无变化；False Rejection 仅 1 题改善，置信区间触及 0——**不足以证明稳定优势**，如实报告不夸大。

## 18-19. 提升/退化问题

- 提升：D008（PE0 拒答 → top_1 回答；引用一致）；无其他 per-question 指标差异。
- 退化：无（Citation/Rejection 逐题一致）。
- 其余 47 题两组完全一致。

## 20. 最终选择

```json
{
  "evaluation_completed": true,
  "parent_expansion": "none",
  "replacement_approved": false,
  "replacement_gates_passed": false,
  "selection_reason": "No parent expansion strategy passed replacement gates",
}
```

> Closeout 修正：`replacement_gates_passed` 表示候选是否通过替换门禁（false）；`replacement_approved` 表示是否批准替换（false）；`evaluation_completed` 表示实验流程完整（true）。三者语义分离。

**MRR 口径审计（Closeout）**：Phase 4C frozen pool 的 MRR 统一为 canonical MRR@5（rank 1-5、48 题、p0 evidence mapping、N001/N002 排除）= **0.6201**（四组一致）。Phase 3A 官方 MRR 0.6167 来自其自身检索实例；0.0034 差异源于两次独立检索实例，非指标 bug；历史指标未修改。定义见 `evaluation/experiments/phase4/metrics_definition.json`。

**答案调用审计（Closeout）**：每组 total=50、answer_llm_calls=49、deterministic_refusals=1（N001 无证据、零调用）、cache_hits=0（最终运行）、failures=0、missing_results=0；50 题结果完整、每题有 status。

## 21. 是否替换默认配置

**否。** 默认 `parent_expansion=none` 保持不变；未修改生产默认配置（`PARENT_EXPANSION_ENABLED=false`）。

## 22. 测试与 Ruff

```text
python -m pytest --collect-only -q   -> 432 collected
python -m pytest -q                  -> 420 passed, 12 skipped, 0 failed
python -m ruff check .               -> All checks passed
```

skip 审计（Closeout）：12 项全部为外部 opt-in——2 真实 DashScope+Qdrant E2E（`IRA_QDRANT_E2E`）+ 9 真实 Qdrant 集成（`IRA_QDRANT_INTEGRATION`）+ 1 真实 MinerU API（`IRA_MINERU_REAL`）；无产物依赖跳过。
新增 `tests/test_parent_expansion.py`（15 项：冻结隔离、Parent 映射、四策略、上下文、provenance、指标、bootstrap 种子）。

## 23. 已知限制

- Answer Correctness / Faithfulness：N/A（未运行 LLM Judge）。
- Parent 加载/上下文构建耗时未单独计时（已并入查询延迟）。
- frozen Child 检索的 retrieval_score 为 None（LightRAG mix 最终 payload 不暴露分数；rank 序有效）。
- 预检/答案缓存正文不入库（本地保留）。

## 24. 下一阶段是否允许

**是（Phase 4C complete；Rerank 消融可作为下一子阶段）。** 本任务不自动实施 Rerank；如启动需用户另行发起。phase4_frozen_index 已保留并标记为实验索引，供后续 Rerank 复用。
