# Phase 10B-3B Local Staging Bring-up & Context Registry Materialization

## 状态

环境 Bring-up 部分完成，但环境门禁未通过；本阶段停止，不进入 Phase 10B-3A 的 52 题真实重跑，也不进入 Phase 10C。

代码测试提交：`fcbf6fcfbd804666899425e2fa98770d57cab533`
环境产物提交：`ba752ff6687551bc7a44e0817a006061e92e3889`

## 环境检查

- 分支：`codex/knowledge-qa-platform-design`
- Python：`C:\Users\12189\.conda\envs\industrial-rag\python.exe`，Python 3.11.15。
- 旧 8000：Docker 容器 `industrial-rag-p3-api`，映射端口 8000，OpenAPI 不包含 KB-scoped Query 或 admin Trace；未停止，因为新服务真实查询门禁尚未通过。
- 当前 FastAPI：8010，运行当前代码，OpenAPI 已包含：
  - `/v1/knowledge-bases/{kb_id}/query`
  - `/v1/admin/diagnostics/requests/{request_id}/retrieval-trace`
- 当前 Streamlit：8510，`/_stcore/health` 返回 200。
- Qdrant：`qdrant/qdrant:v1.13.6`，`127.0.0.1:17333`，连接正常。
- staging 数据库：`runtime/phase10b3b/industrial_rag_staging.db`，由项目数据库副本迁移/补齐后使用，未修改正式数据库。
- KB：`8fce4626859d44abb70a9ae5b0372cea`。
- Active Generation：`a2d1c77ce08b414495e9d845cc42f799`，Generation 名称 `g5162e7fb4208635103ff4ebb`。

详细产物：

- [environment_audit.json](../evaluation/phase10b3b/environment_audit.json)
- [api_version_check.json](../evaluation/phase10b3b/api_version_check.json)
- [environment_gate.json](../evaluation/phase10b3b/environment_gate.json)

## Registry 物化

已从 Active Generation 实际 `child_chunks.jsonl` 生成 Generation workspace 下的：

- `context_registry/manifest.json`
- `context_registry/chunks.jsonl`
- `context_registry/relationships.jsonl`

没有使用 Golden Set、没有扫描 PDF、没有重新计算 Embedding、没有重建 Qdrant、没有覆盖 Active Generation。

完整性结果见 [context_registry_integrity.json](../evaluation/phase10b3b/context_registry_integrity.json)：

- Child Chunk：453
- 唯一 Chunk：383
- 重复 Chunk ID：70，未通过门禁
- Previous/Next：451/451
- Broken Link：0
- Cross-document：0
- Cross-generation：0
- Self Link：2，未通过门禁
- Parent：0（真实 Parent 产物不存在）
- Table：0（真实结构化 Table 元数据不存在）

因此当前 Registry 不能宣称支持 Parent/Table Completion；重复 ID 和 Self Link 必须先由真实源产物问题解决，不能在 Registry 中伪造修复。

## Smoke Test

[smoke_results.jsonl](../evaluation/phase10b3b/smoke_results.jsonl) 记录了真实 8010 HTTP 检查：

- 无凭证访问 admin Trace：401
- SERVICE 访问 admin Trace：403
- ADMIN 有效认证访问不存在 Trace：404（证明已通过认证层）
- KB-scoped 普通查询：502

普通查询已真实进入 Qdrant 检索，但 DashScope 模型生成返回 `AllocationQuota.FreeTierOnly` 403，因而没有成功 request_id 可用于 admin Trace 200 验证。没有用旧 8000 API、Mock、缓存或内部函数替代真实链路。

## 门禁与后续执行

`environment_gate_passed=false`，阻塞原因：

1. Registry 完整性存在 70 个重复 Chunk ID 和 2 个 Self Link；
2. 模型供应商 Free Tier 配额耗尽，普通查询返回 502；
3. 没有成功普通查询，无法完成同一 request_id 的 Trace 200、EvidenceResponse 和 Completion 触发验收。

因此本阶段没有执行 development/validation 52 题、没有执行 Holdout、没有创建 Tag、没有打包 RC、没有部署生产。

## Secret

`.env.local_staging` 已被 `.gitignore` 忽略；报告、JSON、JSONL、日志和截图元数据不包含 Secret 值。新文件扫描结果在 `evaluation/phase10b3b/secret_scan.json`，`confirmed_secret_count=0`。

## 状态字段

```json
{
  "environment_gate_passed": false,
  "phase10b3a_approved": false,
  "phase10c_allowed": false,
  "holdout_rerun": false,
  "production_deployment_performed": false
}
```
