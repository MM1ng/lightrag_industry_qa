# Phase 3A-D 报告：付费运行就绪状态

**日期**: 2026-08-01
**分支**: `codex/knowledge-qa-platform-design`

---

## 1. 当前状态

- Phase 3 complete
- Phase 3A-P（MinerU 确定性 Adapter + P1-clean）complete，人工质量门禁通过
- Phase 3A-R（固定模型公平实验）**付费运行完成（Phase 3A-R-Paid）**
- **Phase 3A complete；Phase 4 allowed（不自动实施）**

## 2. 固定模型

```json
{
  "llm_model": "qwen-plus-2025-07-28",
  "index_llm_model": "qwen-plus-2025-07-28",
  "query_llm_model": "qwen-plus-2025-07-28",
  "model_fallback_enabled": false,
  "enable_thinking": false,
  "embedding_model": "text-embedding-v4",
  "embedding_dimension": 1024,
  "query_mode": "mix",
  "top_k": 12,
  "chunk_top_k": 20,
  "enable_rerank": false,
  "qdrant_distance": "COSINE"
}
```

## 3. Token 估算

- 样本：P0 20 ChildChunk、P1-clean 20 ChildChunk（真实完整 LightRAG 抽取流程）
- 查询样本：P0/P1 各 4 题（S001/S007/S011/S015）
- 53 次真实调用；0 重试；0 模型不匹配；requested_model == actual_model == `qwen-plus-2025-07-28`
- 每 chunk：P0 ≈ 2,676 token；P1-clean ≈ 2,805 token
- 估算包含 8% merge 与 20% 安全余量
- **估算总量 ≈ 2,408,642 token（估算值，非最终实际计费值）**
- 该估算足以证明 1,000,000 免费额度不足；按量付费开启后仍以实际计费为准
- **正式运行实际消耗（付费）**：P0 1,229,383 + P1 602,176 ≈ **1,831,559 token**（估算偏高约 31%，因 P1-clean 实际 chunk 少于 P1-raw 估算口径）；人民币费用 SDK 未提供，标记 N/A。

## 4. 当前免费额度

1,000,000 token（阿里云百炼免费额度）。估算需求 2,408,642 token，超出约 141%。

## 5. 是否获得付费授权

- 用户声明并授权：已开启阿里云百炼按量付费，账户有可用余额（2026-08-01）。
- 显式运行门禁 `IRA_PHASE3A_PAID_RUN=1`：**已设置并通过**；`LLM_MODEL=qwen-plus-2025-07-28`、`MODEL_FALLBACK_ENABLED=false` 均确认。
- 结论：付费运行已获授权并完成。

## 6. 付费运行门禁状态

`fixed_model_run --readiness` 输出（放行时）：

| 检查项 | 状态 |
|---|---|
| IRA_PHASE3A_PAID_RUN=1 | ✅ |
| LLM_MODEL == qwen-plus-2025-07-28 | ✅ |
| MODEL_FALLBACK_ENABLED=false | ✅（实验脚本强制） |
| enable_thinking=false | ✅ |
| 配置 hash 门禁 | ✅ |
| 冻结产物未变化（25 项） | ✅ |
| 随机测试 prefix（ira_p3ar_） | ✅（运行期生成） |
| 预检模型一致性 | ✅ |

运行期间两次（P0/P1 启动前）均通过；完成后按登记名称精确清理（Qdrant 现存 0 collection）。

## 7. 冻结产物 hash

`evaluation/experiments/parser_backend/manifests/phase3a_frozen_artifacts_manifest.json`，25 项：

- 2 份原始 PDF、黄金集
- P0（2 PDF）× blocks/parents/children
- P1-clean（2 PDF）× blocks/parents/children、cleanup_manifest、tables_clean
- MinerU 原始 result.zip、content_list.json（2 PDF）
- fixed_model/config.json、prompt_bundle.json

全部 `immutable=true`；磁盘核验 `ok=true`（path/size/sha256 一致）。

## 8. 配置 hash

八项 hash（chunk / embedding / index_llm / query_llm / prompt_bundle / retrieval / qdrant_schema / golden_set）P0=P1 完全一致；唯一独立变量 `parser_pipeline`：

- P0.parser_pipeline = `pymupdf_standard_adapter`
- P1.parser_pipeline = `mineru_online_clean_adapter`

## 9. 缓存状态

- 精确匹配缓存机制已实现（键 = model + system prompt hash + user prompt hash + 实验配置 hash + LightRAG 版本 + 实验名）；命中记录 `cache_hit` 与缓存 usage，**只允许完全匹配调用复用**。
- 预检阶段只持久化了调用日志（未保存响应体），因此预检响应暂不能作为缓存回放；全量运行将从首个调用开始写入精确缓存，断点续跑可命中。
- 缓存文件：`fixed_model/cache/*.jsonl`（不含 API Key）。

## 10. checkpoint 状态

- 两级 checkpoint（索引完成 / 查询完成）机制已实现并写入 `fixed_model/checkpoint_<group>.json`；
- 恢复路径：校验冻结产物 → 校验配置 hash → 校验门禁 → 校验 Qdrant collection 存在 → 跳过已完成阶段；
- **实际状态**：P0/P1 全量运行已完成，checkpoint 已产生并保留（`checkpoint_0.json`、`checkpoint_1.json`），查询结果已产生；
- 临时 Qdrant 资源已精确清理，因此 **checkpoint 不能再用于恢复已删除的临时 Collection**；
- checkpoint 作为**实验审计记录**保留；如需复现，应创建新的 KB/generation，而不是直接恢复已清理资源。

## 11. 测试结果

```text
python -m pytest --collect-only -q   -> 417 collected
python -m pytest -q                  -> 405 passed, 12 skipped, 0 failed
python -m ruff check .               -> All checks passed
```

12 项 skip 均为外部服务 opt-in（11 项真实 Qdrant 集成 + 1 项真实 MinerU API）。

## 12. 是否启动全量实验

**是（已执行并完成）。** 2026-08-01 门禁放行后：

- P0 全量索引（453 children）+ 50 题：594 次 LLM 调用，1,229,383 token
- P1-clean 全量索引（184 children）+ 50 题：296 次 LLM 调用，602,176 token
- 两组完整性门禁全部通过；结果与指标见 [phase-3A-mineru-vs-pymupdf-final-report.md](phase-3A-mineru-vs-pymupdf-final-report.md) §3c。

如需复现（例如配额/配置变化后重跑）：

```powershell
$env:IRA_PHASE3A_PAID_RUN='1'
$env:LLM_MODEL='qwen-plus-2025-07-28'
$env:MODEL_FALLBACK_ENABLED='false'
python -m evaluation.experiments.parser_backend.fixed_model_run --readiness   # 必须全部 ✅
python -m evaluation.experiments.parser_backend.fixed_model_run --full --group 0
python -m evaluation.experiments.parser_backend.fixed_model_run --full --group 1
```

运行完成后：验证三个 namespace → 两组 50 题 → 逐题结果 → 公平指标 → 精确清理 → 更新最终报告 → 停止（不进入 Phase 4）。
