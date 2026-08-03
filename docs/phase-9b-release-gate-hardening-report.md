# Phase 9B Release Gate Closure & Multi-instance Operational Hardening Report

报告日期：2026-08-03（Asia/Shanghai）

## 结论

Phase 9B 的双静态角色鉴权、不可绕过 Validation Gate、双实例 Active 一致性、持久化 Lease/Fencing、两阶段 GC、Qdrant 兼容性与 Secret 安全均已实现并通过自动化或真实 `local_staging` 验收。

Worker 强制终止演练确认了另一实例能把过期任务标记并重新领取，但 LightRAG 在输出 `Successfully finalized 12 storages` 后没有从 Candidate 构建调用返回，任务未能持久化为 `ready`。本轮已修复 `building` 状态未纳入恢复扫描、为 LightRAG insert/close 增加有界等待及持久化校验，但真实恢复终态仍未闭合。因此本报告不批准关闭 Release Gate，也不允许进入 Phase 10。

## Git 与质量门

- 验收代码 HEAD：`6935d7f`（`fix(phase9b): bound finalized candidate insertion`）。
- 分支：`codex/knowledge-qa-platform-design`。
- 相对 `origin/codex/knowledge-qa-platform-design`：ahead 22，behind 0。
- Phase 9B 提交（从新到旧）：
  - `6935d7f` bound finalized candidate insertion
  - `e71aea0` bound LightRAG shutdown during recovery
  - `dacb9d4` recover expired building jobs after crashes
  - `7c56f39` start staging instances sequentially
  - `7aa85f9` bind validation evidence and GC plans to content
  - `c10122a` evaluate canonical locations and quarantine failed docs
  - `a7a64cb` enforce canonical non-regression baseline
  - `994de63` validate Qdrant independently of legacy runtime
  - `40555ec` close validation and operational safety gates
  - `8390ebc` resolve generation per query across instances
  - `6fbc917` add fenced leases and durable job claims
  - `393a187` add operational hardening persistence
  - `300549b` enforce deterministic admin authorization
  - `a29f6f6` Phase 9B implementation plan
  - `616b185` Phase 9B design
- 修改前测试：552 passed，12 skipped。
- 修改后最终测试：595 passed，12 skipped，1 个第三方弃用警告；耗时 80.62 秒。
- Ruff：`ruff check .` 全通过。
- 未创建或推送 Tag；未重新打包 RC；未部署生产。
- 用户保留文件 `phase3-uncommitted-backup.patch` 未修改。

## 数据模型与迁移

- Alembic head：`b9c4e7f2a6d1`。
- 新增持久化结构：`validation_runs`、`kb_operation_leases`、`gc_plans`，并扩展 `update_jobs`、`knowledge_bases`、`vector_index_generations`。
- Validation Run 固化：Golden set 版本与 SHA-256、runner 版本、代码提交、模型与策略指纹、Generation manifest、Qdrant 全内容指纹、Document Registry 指纹、content epoch、actor、request/trace ID、过期时间、JSONL 路径与 SHA-256。
- Lease 使用数据库唯一 KB 行、lease token、单调 fencing token、owner、operation 和 expires_at；过期 worker 的 fenced 写入会被拒绝。
- Job claim、heartbeat、checkpoint、attempt、lease expiry 与 startup recovery 均持久化。

## 鉴权与 actor

- `SERVICE_API_KEY` 识别为 `service`；`ADMIN_API_KEY` 识别为 `admin`；统一使用 Bearer Header。
- 普通接口允许 service/admin；管理写接口只允许 admin。
- 实测：管理接口缺 Header 401、错误 Token 401、service Token 403 且 code=`ADMIN_PERMISSION_REQUIRED`、admin Token 200；普通接口 service/admin 均 200。
- 错误信封包含 request_id、trace_id、稳定 code 和清洗消息。
- Promote、Rollback、GC Execute 均忽略请求中的伪造 `approved_by`，实际审计 actor 为后端稳定摘要 actor；工件未保存原始 Token或完整 Hash。
- 相同 SERVICE/ADMIN Key、日志与响应泄漏、普通问答回归等自动化测试通过。

证据：[auth_matrix.json](../evaluation/experiments/phase9b/auth_matrix.json)、[post_promote_multi_instance.json](../evaluation/experiments/phase9b/post_promote_multi_instance.json)、[rollback_multi_instance.json](../evaluation/experiments/phase9b/rollback_multi_instance.json)。

## Canonical Validation 与失效门

- Golden set：固定 20 题，ID 为 S001、S002、S003、S004、S005、S007、S009、S011、S012、S014、S015、S016、S017、D003、D005、C001、C002、C003、N001、N002。
- Runner：`phase9b-fastapi-candidate-v1`；Candidate HTTP 查询显式禁用 LLM cache。
- 最终通过 Validation Run：`79e2ca04a7da4a969e64c568e9fa92f7`。
- 验收代码指纹：`7c56f39ce2f7be52978b6a94a2296a832cc59aa3`（该次 Promote 的精确运行代码）。
- JSONL：20 行；SHA-256 `56fa5e94b4797a0ef232aaa852408dba01ec3b91aeae4fe1e804c885bcbe3d44`；每行均有 request_id、trace_id、HTTP 状态、answer status、safety result、failure reason、latency、answer hash、citation trace 和 actual generation。
- JSONL 必需字段缺失 0；request_id 缺失 0；trace_id 缺失 0；已发出引用但追踪不完整 0。
- Candidate：20/20 HTTP 200；正例命中 15/18（83.33%）；负例 2/2；已发出引用的 trace completeness 100%；5xx 0；fabricated citation 0；secret leak 0；false rejection 3/18（16.67%）。
- Active：20/20 HTTP 200；正例命中 14/18（77.78%）；负例 2/2；已发出引用的 trace completeness 100%；request/trace ID 缄失均为 0。
- Candidate 相对 Active 没有恶化，正例定位增加 1 题。
- 首个失败 Validation Run `2783cd75c49d404fa12802042b16d444` 未通过；对其 Promote 实测 409，Active 指针保持不变。
- Golden SHA、runner version、代码提交、策略、manifest、Qdrant 内容、Document Registry 或 content epoch 任一改变，旧 Validation Run 均失效；伪造 artifact、篡改 JSONL 和缺失 Validation 的 Promote 自动化测试均被拒绝。
- Promote 不提供 force/bypass 参数。

证据：[candidate_validation_final.json](../evaluation/experiments/phase9b/candidate_validation_final.json)、[candidate_canonical_20.jsonl](../evaluation/experiments/phase9b/candidate_canonical_20.jsonl)、[active_canonical_20.jsonl](../evaluation/experiments/phase9b/active_canonical_20.jsonl)、[active_canonical_20_summary.json](../evaluation/experiments/phase9b/active_canonical_20_summary.json)。

## Promote、Rollback 与多实例一致性

- API A：127.0.0.1:8111；API B：127.0.0.1:8112；共享 SQLite、隔离 Qdrant 及 KB 数据根目录。
- Candidate 查询：service 403、admin 200；Candidate 引用链完整；Active 在 Promote 前不变。
- A/B 同时 Promote 合格 Candidate：一个 200，一个 `concurrent_promote` 409；只有一个 writer 完成原子切换。
- Promote 后 A/B 无重启均立即读取新 Active `a55adad410e644ceb82de96022321633`。
- Rollback 到旧 Active 后 A/B 无重启均立即读取 `a2d1c77ce08b414495e9d845cc42f799`。
- 再回滚到有 Job 的 Candidate 后，approved_by 为后端 admin actor，伪造 actor 未出现；最终再次回滚到旧 Active。
- 单元/集成测试覆盖同 KB writer 冲突、不同 KB 并行、stale fencing token 拒绝和跨实例 generation epoch/cache key。

证据：[promote_concurrency.json](../evaluation/experiments/phase9b/promote_concurrency.json)、[ordinary_query_roles.json](../evaluation/experiments/phase9b/ordinary_query_roles.json)、[rollback_multi_instance.json](../evaluation/experiments/phase9b/rollback_multi_instance.json)。

## Worker 崩溃恢复

- A 独占领取 Job `3ee91e90b5f64003b71d8c5714089d5a`，在 `building_candidate` 阶段被强制终止；进程停止已确认，数据库仍保持 building。
- B 启动扫描把过期租约任务重新领取，worker 为 `sync:system:startup-recovery`，attempt 递增，原半成品 Generation 标记 failed 并创建确定性重建 Generation。
- 演练发现并修复：building 状态遗漏于过期扫描；LightRAG close 无界等待；LightRAG finalized insert 无界等待。
- 真实重试均完成了 Qdrant 集合写入并输出 storage finalized，但没有返回到 Job 层写入 ready。为避免遗留写锁，隔离演练最终通过 admin Cancel 标记 cancelled；Active 始终未改变。
- 结论：跨实例 reclaim 通过，恢复到 ready 未通过，Worker Crash Recovery Gate 未关闭。

证据：[crash_kill.json](../evaluation/experiments/phase9b/crash_kill.json)、[crash_recovery_failed.json](../evaluation/experiments/phase9b/crash_recovery_failed.json)。

## Retention 与 GC

- GC Plan/Execute 为 admin-only，两阶段持久化；Plan 有 TTL、manifest hash、明确 generation ID、明确 collection 全名、workspace 精确路径、content epoch 与 Qdrant 内容指纹。
- 策略保护 Active、last rollback target、保留的 archived、`protect_from_delete`、`audit_frozen` 和 retention window。
- 计划后 Qdrant 内容变化的自动化测试返回 partial_failed，不执行删除。
- 暂存 Plan：5 个失败/中断 Candidate；Active 未列入；已验证 Candidate 未列入；全部 5 项含 Qdrant 内容指纹。
- Execute：completed；5 个 deleted；0 failed；approved_by 为后端 admin actor。
- GC 前后 Active 查询均 success，Active ID 不变，引用链完整。
- 删除只使用计划中的精确 collection 名与通过 KB 路径安全校验的 workspace，不使用前缀模糊删除。

证据：[gc_plan.json](../evaluation/experiments/phase9b/gc_plan.json)、[gc_execute.json](../evaluation/experiments/phase9b/gc_execute.json)。

## Qdrant 与 Point 数量

- Python client：1.13.3。
- 隔离 staging server：1.13.6；期望 minor：1.13。
- 原冻结来源 server：1.13.6；未对来源容器或 volume 执行升级。
- 兼容警告数量：0。
- 初始冻结 Active：chunks 453、entities 1012、relationships 1061。
- GC 后 Active：chunks 453、entities 1012、relationships 1061，完全一致。
- GC 后保留的已验证 Candidate：chunks 454、entities 1019、relationships 1065。

证据：[qdrant_point_counts.json](../evaluation/experiments/phase9b/qdrant_point_counts.json)。

## Frozen KB 与正式资源未修改证明

- 所有写操作指向 `D:\industrial_energy_agent_phase9b_staging`、独立 SQLite 和容器 `ira-phase9b-qdrant-staging`（17333/17334）。
- 初始数据通过只读 scroll 从 16333 克隆；准备 manifest 记录 `source_resources_modified=false`、`llm_cache_copied=false`。
- 冻结两份 PDF 的 SHA-256 分别保持为 `e0f80874dd923d03ea15584f4fe25046ba184675062d6d16e1decafa2a6c8700` 与 `77fd7ebf86ef6c574de11eac446dc321de04fd0773cd7d844287da7fa4d6c4ae`。
- GC 后冻结 Active point 数与初始值逐项一致。
- 未调用正式数据库写接口，未删除或修改正式 Qdrant collection，未清理冻结 KB。

## Streamlit 与 Secret 安全

- Streamlit staging：127.0.0.1:8512，HTTP 200；普通问答使用 SERVICE_API_KEY，管理调用使用 ADMIN_API_KEY，均只在服务端进程读取。
- 对源码与 staging 共 18,259 个文件执行两个真实 Key 的字节级扫描；唯一合法 Secret 文件 `runtime/staging.env` 被排除。
- `confirmed_secret_count=0`；API/UI response secret count=0；包含 Bearer 的日志文件数=0。
- 两个 Key 未出现在报告、JSONL、SQLite、Qdrant 工件、API/UI 响应或日志中。
- config manifest 只保存 configured 布尔值，不保存 Secret。

证据：[secret_scan.json](../evaluation/experiments/phase9b/secret_scan.json)。

## 已知限制与最终门状态

- Worker 被强制终止后的跨实例重新领取已实现，但真实 LightRAG 重建没有返回至 ready；这是当前唯一阻止 Phase 9B 完成的验收缺口。
- SQLite 适合本项目 local_staging/MVP 的确定性控制；更高吞吐的生产多实例部署仍应评估服务型数据库。
- 本报告没有授权生产部署或 Phase 10 打包。

最终状态：

- `release_gate_closed=false`
- `multi_instance_consistency_approved=false`（Active 一致性通过，但整体多实例运行门被 crash recovery 阻塞）
- `retention_gc_approved=true`
- `production_deployment_performed=false`
- `next_phase_allowed=false`

下一步必须先解决并重新实测 Worker Crash Recovery 到 `ready`，再由人工审查本报告决定是否进入 Phase 10。
