# Phase 4D 报告：固定 PyMuPDF 索引下的 Rerank 消融（frozen-candidate）

**日期**: 2026-08-01
**分支**: `codex/knowledge-qa-platform-design`

---

## 1. 阶段结论

- **Phase 4D blocked by missing rerank model configuration**（`RERANK_MODEL` 未配置，仓库无既有 Reranker/模型配置）。
- 已完成：frozen index 验证、候选池冻结与 manifest、Reranker 审计、provider-neutral 接口与测试、R0 离线基线。
- 未运行：R1 离线 Rerank、阶段二完整答案（按规则不自动选模型）。
- `rerank_enabled=false` 生产默认不变；`replacement_approved=false`。

## 2. Git commit

- Phase 4C 基线：`0f6bee4` / `566eddc` / `42b72dc`
- 本阶段提交：`0194d26`（fix 4C 收尾）+ `98690a2`（feat rerank）+ `4c2faef`（docs rerank）。

## 3. Phase 4C 收尾修正

- `final_parent_expansion.json` 决策字段语义分离：`evaluation_completed=true`、`parent_expansion="none"`、`replacement_approved=false`、`replacement_gates_passed=false`。
- **Canonical MRR**：MRR@5（rank 1-5，48 题分母，p0 evidence mapping exact+fuzzy，N001/N002 排除）。Phase 4C frozen pool 重算 MRR@5=**0.6201**（四组一致）；Phase 3A 官方 0.6167 来自其自身检索实例，差异 0.0034 为两次独立检索实例所致，历史指标未修改。定义见 `evaluation/experiments/phase4/metrics_definition.json`。
- **49 次答案调用审计**：每组 total=50、answer_llm_calls=49、deterministic_refusals=1（N001，无证据、无调用）、cache_hits=0（最终运行）、failures=0、missing_results=0；N002 通过证据策略被调用，模型返回拒答文本。
- **skip 审计**：12 项 skip 全部为外部 opt-in（2 真实 DashScope+Qdrant E2E + 9 真实 Qdrant 集成 + 1 真实 MinerU API）；无产物依赖跳过。

## 4. PyMuPDF 固定声明

`parser_pipeline=pymupdf_standard_adapter`、`query_mode=mix`、`top_k=12`、`chunk_top_k=20`、`parent_expansion=none`；唯一变量为 `rerank_enabled`。

## 5. frozen index 验证

| 项 | 值 |
|---|---|
| KB / generation | `8fce4626859d44abb70a9ae5b0372cea` / `g5162e7fb4208635103ff4ebb` |
| Collections | `ira_p3ar_4ac7a596_…_{chunks,entities,relationships}`（存在，未修改） |
| points | 453 / 1,012 / 1,061 |
| 文档状态 | 2/2 processed，无 processing/failed/partial |
| 标记 | `phase4_frozen_index`（index_manifest.json），实验索引，不重建 |

## 6. candidate pool 验证

- 文件：`parent_expansion/frozen_child_results.jsonl`
- SHA256：`fc731efc904d9d9dca639fecf181a01e022c162ac91b67f6432d18b7619bf6a0` ✅
- 50 题 / 998 候选 / 每题 20（S001 等 48 题）+ N001/N002 无候选
- manifest：`rerank/manifests/candidate_pool_manifest.json`（candidate_k=20、final_k=12）

## 7. Reranker 审计

`rerank/reranker_audit.json`：

- existing_interface: **false**（仓库仅 `QueryOptions.enable_rerank` 透传，无 provider/模型/配置）
- provider / configured_model / endpoint / batch / score_range：null
- fallback_behavior：disabled（`RERANK_FALLBACK_ENABLED=false` 强制）
- readiness：false；blocked_reason：`RERANK_MODEL` unset

## 8. 模型与配置

按规则未自动选择模型。接口要求：

- `RERANK_MODEL` 必须为 exact model name（拒绝 latest/auto 别名）；
- `RERANK_FALLBACK_ENABLED=false`；
- `candidate_k=20`、`final_k=12`；
- 预检：1 条中文 query + 20 候选，返回 20 唯一候选、无增删、score 可解析、无模型切换。

已实现 provider-neutral `RerankerProvider`（`reranker.py`）与 `BlockedReranker`（未配置时显式失败）。

## 9-10. 离线 Rerank 结果 / Rank movement

R0 离线基线（frozen 顺序 top-12，`results/offline/baseline_metrics.json`）：

| 指标 | R0 |
|---|---|
| Recall@1 / @3 / @5 / @12 | 0.5625 / 0.6875 / 0.7500 / 0.7917 |
| MRR@5（canonical） | 0.6201 |
| Gold Document Recall | 1.0000 |
| Gold Page Recall | 0.8542 |
| Gold Evidence Recall | 0.7917 |
| Evidence Precision@5 / @12 | 0.2000 / 0.1024 |
| Top-1 Document Accuracy | 1.0000 |
| Top-5 Page Coverage | 0.7917 |

R1：未运行（模型未配置）。`results/offline/reranked.jsonl`、`rank_movements.jsonl` 不生成；候选门禁不评估。

## 11. 完整答案结果或未进入原因

未进入阶段二：R1 未运行，候选门禁无法通过，按规则不生成完整答案。

## 12-16. 分类/配对/Token/提升/退化

不适用（R1 未运行）；不得编造。

## 17. 最终决策

```json
{
  "rerank_enabled": false,
  "replacement_approved": false,
  "selection_reason": "Rerank did not run: RERANK_MODEL is not configured; exact model required"
}
```

## 18. 是否替换默认

否。生产默认 `RERANK_ENABLED=false` 不变；未修改任何生产环境变量。

## 19. 测试与 Ruff

```text
python -m pytest --collect-only -q   -> 443 collected
python -m pytest -q                  -> 431 passed, 12 skipped, 0 failed
python -m ruff check .               -> All checks passed
```

新增 `tests/test_rerank.py`（11 项：候选池 SHA256、50 题完整、exact model 门禁、latest 拒绝、fallback 关闭、未配置阻塞、缓存键精确、original rank/score 保留、R0 canonical MRR 等）。

## 20. 已知限制

- R1/Rerank 结果缺省（模型未配置），Phase 4D 未验收；
- Rerank 缓存正文不入库；费用/usage 未产生（N/A）；
- 若后续配置 `RERANK_MODEL`，需重新执行预检 → 离线 R0/R1 → 门禁 → 阶段二。

## 21. 下一阶段是否允许

Phase 4D 当前 blocked；配置 exact `RERANK_MODEL` 后可续跑。未自动进入 Phase 5。
