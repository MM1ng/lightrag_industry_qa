# Knowledge Base Rebuild Runbook

## 1. 何时允许重建

- 仅当冻结索引被确认损坏/丢失，且经人工批准；
- 必须使用 PyMuPDF 默认链路；
- 必须执行 shadow rebuild（新 generation，不覆盖 active）。

## 2. 流程

1. 预检：`frozen_strategy.json` 哈希、候选池 SHA256、golden set SHA256、prompt bundle SHA256。
2. Token 费用预检：按 Phase 3A/4 实际用量估算（每份手册索引约 1.1M tokens），确认余额。
3. paid gate：`IRA_PHASE3A_PAID_RUN=1`、`LLM_MODEL=qwen-plus-2025-07-28`、`MODEL_FALLBACK_ENABLED=false`。
4. 创建 KB → 上传 PDF → parse（PyMuPDF，不自动调用 MinerU）→ index（新 generation，shadow 状态）。
5. verify：chunk/entity/relation point 数与 child chunks 一致。
6. promote：仅 verify 通过后激活新 generation。
7. 失败时：不 promote，标记 failed，精确清理该 generation 的临时 collection。

## 3. MinerU 手动模式

MinerU 仅作为用户手动解析选项：显式设置 `MINERU_ENABLED=true` 并选择对应解析任务；默认链路不使用 MinerU。

## 4. 失败不 promote

任何 verify 失败/索引任务失败 → 保持旧 active generation 不变；新 generation 置为 failed；不得自动回退到旧数据。
