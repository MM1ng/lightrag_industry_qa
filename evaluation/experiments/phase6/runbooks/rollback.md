# Rollback Runbook

## 1. 普通 rollback

通过 API：`POST /v1/knowledge-bases/{kb_id}/vector-backend`（nano↔qdrant）或指定历史 generation；仅允许回滚到指纹一致的 generation。

## 2. stale generation 拒绝

child chunks 指纹与 generation 记录不一致时，rollback 返回 `nano_generation_stale` 错误，且不切换 active generation。

## 3. 重启恢复

- 进程重启后 runtime manager 为空：下一次查询按 active generation 自动重建 runtime（已由 E2E restart 场景验证）。
- DB/Qdrant 已持久化；无需手工恢复集合。

## 4. 数据一致性检查

1. `GET /ready` 全绿；
2. active generation 的 collections 存在且 point 数匹配 manifest；
3. 一条黄金集问题返回 citations，shadow audit 无 invalid。
