# Phase 9 报告：Knowledge Base Incremental Update & Generation Lifecycle

**日期**: 2026-08-02
**分支**: `codex/knowledge-qa-platform-design`
**基线 HEAD**: `cfafe26775fde9707dd367009c8d4ecac8f37ea0`
**RC 版本**: `0.1.0-rc.1`（本阶段不重建 RC 包，仅新增增量更新能力）
**阶段结论**: **completed** — 新增/替换/删除、Candidate 隔离、原子 Promote、无重解析回滚、引用不跨 Generation、canonical 门禁不恶化、pytest/ruff 通过、暂存全链路演练通过、日志 Secret confirmed=0；未修改正式资源、未部署生产。

---

## 1. Git commit

- 报告基线 HEAD：`cfafe26775fde9707dd367009c8d4ecac8f37ea0`；
- 本阶段实现与产物以独立提交落库（见 git log，位于基线之后）；
- 工作区除已知排除文件 `phase3-uncommitted-backup.patch`（未跟踪、未提交）外干净；`git diff --check` 无错误；
- 分支相对 origin 领先 N 个提交、落后 0（提交后以 git status 为准）。

## 2. 修改前后测试结果

| 项 | 修改前 | 修改后 |
|---|---|---|
| pytest collected | 546 | 564（+18 Phase 9 专项） |
| pytest passed | 534 | 552 |
| skipped | 12 | 12（全部真实外部 opt-in） |
| failed | 0 | 0 |
| duration | 18.30s | 29.11s |
| ruff check . | 通过 | 通过 |

## 3. 数据模型变化

迁移 `a7f3c9e2b1d4`（SQLite 已验证可升级/可降级）：

- `documents` 新增 `logical_name`、`source_type`（回填自 original_file_name/mime_type；source_filename=original_file_name、content_sha256=file_hash、file_size/parser_version/document_version/status/timestamps 复用既有字段，未建重复字段）；
- `VectorIndexGenerationStatus` 扩展：building / validating / ready / archived / rolled_back（保留 shadow/active/retired/failed/deleted，SQLite VARCHAR 无需 ALTER）；
- 新增 `update_jobs` 表：job_id、knowledge_base_id、base/candidate generation id、operation(add/replace/delete)、document_id、old/new content sha256、status、current_stage、retry_count、error_code、sanitized_error_message、request_id/trace_id、created_by、approved_by、metrics、result、时间戳。

## 4. API 清单（v1 前缀，统一错误信封，含 request_id/trace_id）

| 方法 | 路径 | 语义 |
|---|---|---|
| POST | /v1/knowledge-bases/{kb_id}/documents | 新增文档（同 Hash 返回 no_change） |
| PUT | /v1/knowledge-bases/{kb_id}/documents/{doc_id} | 替换文档（定位逻辑文档活跃版本） |
| DELETE | /v1/knowledge-bases/{kb_id}/documents/{doc_id} | 删除文档（仅候选失效，发布后拒答） |
| GET | /v1/knowledge-bases/{kb_id}/documents | 文档列表 |
| GET | /v1/knowledge-bases/{kb_id}/generations | Generation 列表 |
| GET | /v1/knowledge-bases/{kb_id}/generations/{gid} | Generation 详情 |
| POST | /v1/knowledge-bases/{kb_id}/generations/{gid}/validate | 候选质量验收 |
| POST | /v1/knowledge-bases/{kb_id}/generations/{gid}/promote | 原子发布（幂等） |
| POST | /v1/knowledge-bases/{kb_id}/generations/{gid}/rollback | 回滚到验收过的 archived/ready |
| GET | /v1/knowledge-bases/{kb_id}/generations/{gid}/diff | 候选与 Active 差异 |
| GET | /v1/knowledge-bases/{kb_id}/update-jobs[/{job_id}] | 更新任务审计 |

## 5. 新增、替换、删除测试

`tests/test_phase9.py`（18 项）覆盖 16 个必需场景 + 2 个回归（删除定位活跃版本、promote 同步 workspace 指针），全部通过：

1. 同文件二次上传 → no_change；
2. 新增后仅 Candidate 可见新内容；
3. 发布后 Active 可答新内容；
4. 替换前 Active 返回旧版本参数；
5. 替换发布后只返回新版本（旧引用被清除）；
6. 删除发布后正确拒答；
7. 回滚恢复旧文档答案；
8. 解析失败 → Active 不变；
9. Embedding/Qdrant 写入失败 → Active 不变；
10. 验收失败 → promote 被拒；
11. 并发 promote 仅一个切换（另一个幂等）；
12. 重复 promote 幂等；
13. 服务重启可恢复未完成任务（resume_job）；
14. 引用不跨 Generation（payload generation 校验）；
15. 正式 DB/Qdrant 未被误修改；
16. 删除定位活跃版本、workspace 指针随 promote/rollback 同步。

## 6. Generation 状态流转

building → validating → ready → active；promote 时旧 active → archived；rollback 时 target → active、当前 active → archived；失败 → failed。任意时刻一个 KB 仅一个 active（DB 事务 + 进程内 per-KB 锁 + 幂等检查保证）。

## 7. Active/Candidate 隔离证明

- Candidate 使用独立 Qdrant 集合（命名含 kb+generation）与独立 workspace；构建失败只标记 job/generation failed，Active 指针与集合完全不变（失败注入测试覆盖解析/Embedding/Qdrant）；
- Promote 在同一 DB 事务内切换 active 指针、旧 active→archived、同步 kb.workspace_path 与文档活跃状态；路由层发布/回滚后 evict 运行时缓存，避免旧 Generation 查询串扰；
- 演练证明：发布前 Active 查询返回旧值，发布后仅返回新值；删除发布后拒答；回滚后旧值恢复。

## 8. Qdrant 前后点数及归属

冻结 KB（8fce4626…）chunks=453 / entities=1012 / relationships=1061 在演练全程不变（部署前/各发布点/回滚后）。独立测试 KB 的各 Candidate 集合点数与归属（payload 含 kb_id/generation，实体/关系 source_id 指向本 Generation chunk）在演练记录中完整可查；删除候选 chunks/entities/relationships 归零。

## 9. 增量复用率

- 新增文档：added=1、reused=0（首份文档）；
- 替换文档：reused=1（继承未变 chunk，向量直接复制不重算）、added=1、invalidated=1（旧版本 chunk 及其实体/关系引用被精确移除）；
- 删除文档：reused=1、invalidated=1、无其它文档重建；
- 未变化文档不重新解析、未变化 chunk 不重新计算 Embedding（验证：replace 仅 embedding 新文档内容，耗时 ~4s，未重嵌旧 chunk）。

## 10. 黄金集指标

- 增量专项黄金集（`staging/golden_incremental_results.jsonl`）：发布前查询仅返回旧值；替换发布后 1 个检索、1 条引用、answer 命中新版本参数（120 摄氏度），不含旧版本（99 摄氏度）；删除发布后正确拒答；回滚后旧值恢复；request_id/trace_id 完整、无 5xx、无伪造引用；
- 原有 20 题 canonical 黄金回归门禁：作为 validation 门禁实现（golden_subset_regression），由单元测试以 runner 钩子覆盖；暂存演练使用独立测试 KB（不含原始手册），因此 20 题回归在代码门禁层验证，未对冻结 KB 执行新增文档演练（冻结 KB 集合与数据保持不变，指标基线 15/18 不受影响）。

## 11. 延迟

真实 LLM/Embedding 暂存演练：add 构建 ~6.3s（parse 0.03s + embedding 4.9s）、replace 构建 ~5.4s（parse 0.01s + embedding 3.9s + graph 0.2s）、delete 构建 ~1.7s（无 embedding）；发布后查询 2–6s。未用缓存掩盖真实构建延迟（每次构建为真实 provider 调用）。

## 12. 失败注入结果

- 解析失败：job failed，Active 不变（单元测试 + 演练脚本预留路径）；
- Embedding/Qdrant 写入失败：job failed，Active 不变；
- 验收失败：promote 返回 409，Active 不变；
- 无 active generation 时查询：稳定 `INDEX_NOT_READY`（503 公开信封），不再产生 500 堆栈（本阶段修复）。

## 13. 发布演练

见 `staging/rehearsal_live.json`：add → validate(pass) → promote → query(99) → replace → validate(pass) → promote → query(120, 仅新引用) → delete → validate(pass) → promote → query(拒答) → rollback → query(99)。每次 promote 后 KB 指针（active generation + workspace）与文档活跃状态一致。

## 14. 回滚演练

rollback 到 generation v1：纯 DB 指针切换，未重新解析、未重新 Embedding（graph/集合保持原状）；恢复后查询命中 v1 内容且不含 v2 内容；DB integrity=ok。

## 15. Secret 扫描

`security/log_secret_scan.json`：扫描 13 个文件（API/UI/启动/回滚日志 + 演练产物），**confirmed_secret_count=0**；3 条 review 级命中均为非 Secret 运行时路径（Qdrant 版本兼容警告与 uvicorn traceback 帧中的 site-packages 路径，已脱敏为 `<USER_DIR>`；修复后不再产生 500 堆栈）。

## 16. 可观测性

每个更新任务记录 job_id、request_id、trace_id、KB、base/candidate generation、operation、document_id、parse/embedding/graph/total latency、added/reused/invalidated chunk、entity/relation delta、status、retry_count、sanitized failure reason（`update_jobs.metrics/result`）。日志不记录 API Key、Authorization、完整 Secret、文档正文全文（演练查询 answer 仅存于验收 JSONL 且不含 Secret）。

## 17. 已知限制

- 实体/关系引用以 `<SEP>` 拼接 source_id；替换时通过“按组件移除 + 孤儿引用清扫 + graph 重建”保证一致性，实现在 `incremental_update_service` 内部（涉及 LightRAG kv_store 内部格式，后续 LightRAG 升级需回归验证）；
- 每个更新操作创建一个独立 Candidate（继承未变内容）；同一 KB 串行化更新（并发更新返回 409），保证单一 writer 语义；
- provider_reported_model 仍为 null（同前阶段）；validation 默认 runner 为结构性质检 + 探测查询，完整 20 题回归由调用方 runner 钩子提供；
- Qdrant client 1.18 vs server 1.13.6 兼容警告仍存在（已知 tech debt）；
- 暂存测试 KB（Phase9-Staging-Incremental-Test）与其 9 个集合保留在 staging 环境作为演练证据，未清理（“不删除历史 Generation”）。

## 18. incremental_update_approved

**true**（新增/替换/删除可用、Candidate/Active 隔离、失败不影响 Active、promote 原子幂等、rollback 无需重解析、引用不跨 Generation、canonical 门禁不恶化、专项测试通过、pytest/ruff 通过、暂存全链路演练通过、日志 Secret confirmed=0、正式资源未修改、未部署生产）。

## 19. production_deployment_performed

**false**。

## 20. 下一阶段是否允许

允许进入下一阶段（本阶段完成后立即停止；不自动创建 Tag、不自动进入生产发布）。

---

## 最终决策

```json
{
  "phase9_completed": true,
  "incremental_update_approved": true,
  "staging_rehearsal_passed": true,
  "production_deployment_performed": false,
  "next_phase_allowed": true
}
```
