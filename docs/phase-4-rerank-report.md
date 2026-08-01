# Phase 4D-R 报告：frozen-candidate qwen3-rerank Rerank 消融

**日期**: 2026-08-01
**分支**: `codex/knowledge-qa-platform-design`

---

## 1. 阶段结论

- **Phase 4D-R blocked by R1 candidate completeness gate（冻结候选池契约偏差）**。
- qwen3-rerank **真实预检通过**（20/20 结果、request_id、score 可解析、usage 存在）。
- R1 实际运行 48 题：**47/48 成功（每题 20 输入/20 输出）**；C007 失败。
- 失败根因：**冻结候选池本身违反契约**——C007 只有 19 个候选（应 20）；N001/N002 分别有 20/19 行候选（应为 0）。qwen3-rerank 对 C007 返回 19/19（无超出池的丢失），但"输入=20/输出=20、无丢失"的完整性门禁无法满足 → R1 无效。
- 按规范：不进入阶段二、不批准替换；`rerank_enabled=false`、`replacement_approved=false`。

## 2. Git commit

- 基线：`655c592`
- 本阶段提交：`0288aa5`（feat qwen3 reranker）+ `d578ebb`（eval 消融）+ `631270e`（docs）。

## 3. qwen3-rerank 模型身份

```json
{
  "provider": "aliyun_model_studio",
  "requested_model": "qwen3-rerank",
  "model_id": "qwen3-rerank",
  "model_identity_type": "official_mainline_model_id",
  "dated_snapshot_available": false,
  "fallback_enabled": false
}
```

## 4. 无日期快照限制

qwen3-rerank 无日期快照 ID；未伪造日期模型名。模型 allowlist 显式只含 `qwen3-rerank`（拒绝 latest/auto/未知模型）。模型身份记录于 `manifests/model_run_manifest.json`；`actual_model_version=null`（API 未提供，不编造）。

## 5. frozen index 验证

KB `8fce4626859d44abb70a9ae5b0372cea` / generation `g5162e7fb4208635103ff4ebb`；points 453/1,012/1,061；2/2 processed；collection 存在；index_manifest 标记 `phase4_frozen_index`。✅

## 6. candidate pool 验证

文件 SHA256 `fc731efc904d9d9dca639fecf181a01e022c162ac91b67f6432d18b7619bf6a0` ✅（未变）。

**契约偏差（真实统计）**：

| 题 | 实际候选数 | 契约要求 |
|---|---|---|
| S001-S020、D001-D020、C001-C006、C008 | 20（47 题） | 20 |
| C007 | **19** | 20 |
| N001 | **20** | 0 |
| N002 | **19** | 0 |

manifest（`manifests/candidate_pool_manifest.json`）如实记录上述数量。此偏差是 Phase 4C 检索冻结阶段遗留（N001/N002 也被执行了检索并写入候选；C007 经 identity dedup 后为 19 行）。

## 7. 真实预检

`preflight.json`（S001 + 20 冻结候选，qwen3-rerank，top_n=20）：

- HTTP 200；返回 20 项；index 0-19；无重复/丢失/池外；score 有限；request_id 非空；无 fallback；文本未变 ✅
- schema 摘要：result_index_field=`index`、score_field=`relevance_score`、usage_present=true、model_metadata_present=true、authorization_not_stored=true
- 延迟约 0.33-0.75s

请求体（经真实 API 验证）：`{model, input:{query,documents}, parameters:{top_n:20, return_documents:false}}`。

## 8. 输入长度验证

query ≤4000、document ≤4000、单请求 ≤120000 token 门禁在 provider 内强制；48 题全部通过（无截断、无改写）。

## 9-10. R0/R1 离线指标

### R0（frozen 顺序 top-12，canonical）

Recall@1/3/5/12=0.5625/0.6875/0.7500/0.7917；MRR@5=0.6201；Gold Doc=1.0000；Gold Page=0.8542；Gold Evidence=0.7917；EvP@5=0.2000；EvP@12=0.1024；Top-1 Doc=1.0000；Top-5 Page=0.7917。

### R1

**未完整（47/48）**，指标不计算（不编造）。47 题结果与 rank movement 已保存（`results/offline/reranked.jsonl`、`rank_movements.jsonl`），仅作审计，不用于结论。

## 11. Rank Movement（47 题部分）

- mean abs rank movement 6.004；median 5；P95 14
- relevant promoted 31 / relevant demoted 14
- irrelevant promoted 416 / irrelevant demoted 402
- top-1 changed 0

## 12. 候选完整性

`completeness.passed=false`：

- request_count=48；success=47；error=1（C007）
- C007：input=19（池内仅 19 行）→ 输出 19；"输入 20/输出 20、丢失 0" 无法满足
- pool_out=0、duplicate=0（输出内）

## 13. 离线门禁

未评估（R1 不完整，先决条件不满足）。`results/offline/gates.json` 记录 stage2_allowed=false。

## 14. 是否进入完整答案阶段

**否**（完整性门禁失败）。

## 15-16. 完整答案指标 / 分类

未运行（N/A），不编造。

## 17. 配对统计

未运行（R1 不完整）。

## 18. Rerank Token、延迟和费用

- 预检 1 次 + R1 47 题成功 + 1 题失败重试：约 49 次真实调用（缓存清理后首跑全 miss）
- 单次延迟约 0.3-1s；Rerank API 不返回 Token/费用 → N/A
- 精确缓存已实现（键含 provider/model/query/候选顺序/文本 hash/候选数/top_n/地域/配置 hash/commit）；缓存正文不入库

## 19-20. 提升/退化问题

未评估（R1 不完整）；不编造。

## 21. 最终决策

```json
{
  "evaluation_completed": true,
  "rerank_enabled": false,
  "replacement_approved": false,
  "replacement_gates_passed": false,
  "rerank_model": "qwen3-rerank",
  "selection_reason": "qwen3-rerank preflight passed; R1 completeness gate failed due to frozen candidate pool contract mismatch (C007=19; N001/N002 have rows)"
}
```

## 22. 是否替换默认

否。生产默认 `RERANK_ENABLED=false` 不变；未修改生产环境变量。

## 23. 测试与 Ruff

```text
python -m pytest --collect-only -q   -> 444 collected
python -m pytest -q                  -> 432 passed, 12 skipped, 0 failed
python -m ruff check .               -> All checks passed
```

新增/更新：allowlist（qwen3-rerank 通过、未知/latest 拒绝）、请求体 schema、缓存键、候选池契约偏差如实断言、门禁/阻塞测试。skip 12 项均为外部 opt-in。

## 24. 已知限制

- R1 指标与阶段二缺省（候选池契约偏差阻塞）；
- 冻结池的 N001/N002 候选行与 C007 缺行属于 Phase 4C 检索冻结阶段的遗留问题；修复需重新生成候选池（本阶段禁止，需用户另行发起）；
- API 不返回模型版本与费用 → actual_model_version=null、费用 N/A；
- 若重新生成合规候选池（48×20、N 题 0 行），qwen3-rerank 的 R1 可直接续跑（provider 与缓存已就绪）。

## 25. 是否允许进入 Phase 5

**否**。Phase 4D-R 未完成（R1 完整性阻塞）；修复候选池并重跑后方可重新判定。
