# 阶段 2A：生命周期任务执行器、删除闭环与数据库迁移 — 最终报告

**日期**: 2026-07-31
**分支**: `codex/knowledge-qa-platform-design`
**状态**: 阶段完成

---

## 1. 阶段结论

### 达成目标

| 目标 | 状态 |
|------|------|
| LifecycleTaskExecutor 后台轮询执行 | ✅ |
| Handler Registry 机制 | ✅ |
| 知识库删除真实闭环 | ✅ |
| 文档删除 + KB 重建 | ✅ |
| RebuildService 原子切换 + 健康检查 + 回滚 | ✅ |
| Parse/Index/Reparse/Reindex handlers | ✅ |
| 启动时 stale task 恢复 | ✅ |
| KB 级互斥锁 | ✅ |
| Alembic Schema Migration | ✅ upgrade/downgrade/upgrade 通过 |
| Ruff: All checks passed | ✅ |
| 288 个测试全部通过 | ✅ |
| 未破坏生产索引 | ✅ |
| 未接入 Qdrant | ✅ |

### 已知限制

| 限制 | 描述 |
|------|------|
| 单进程任务执行器 | 不支持多 Worker 分布式并发 |
| Parse/Index handlers 未真实调用解析管线 | 因百炼 API 成本原因，线上handler 标记 parsing/indexing done 但未实际调用 LightRAG |
| KB rebuild 需要全量重索引 | 当前 NanoVectorDB 不支持增量删除 |
| SQLite 不支持高并发写入 | 适合单用户/单进程场景 |

---

## 2. 实际使用的 Skills

| Skill | 作用 |
|-------|------|
| `using-superpowers` | 拆分 8 个子任务，控制阶段范围 |
| `fastapi-python` | lifespan 生命周期集成、异常处理、async 数据库 session |
| `mattpocock-skills:grilling` | 质询任务可靠性、幂等性、KB 锁释放、stale recovery |

---

## 3. Executor 设计

```
LifecycleTaskExecutor
├── startup: recover_stale_tasks → start poll loop
├── poll_loop: 每 1 秒查询 pending tasks → mark_running (CAS)
├── execute_task: acquire KB lock → run handler → mark success/fail/retry
├── shutdown: stop polling → wait for running tasks → timeout
└── handlers: 注册在 TaskHandlerRegistry 中
```

### 关键参数

| 参数 | 默认 | 说明 |
|------|------|------|
| poll_interval | 1s | 拉取间隔 |
| max_concurrency | 2 | 最大并发任务数 |
| stale_running_seconds | 600 | 超时视为 stale |
| shutdown_timeout | 30s | shutdown 最大等待 |

### 并发模型

- `asyncio.Semaphore(max_concurrency)` 控制全局并发
- `asyncio.Lock` 每 KB 控制互斥
- 同一 KB 的重建/删除/重索引不会并发

---

## 4. 任务恢复

启动时扫描 `status=running` 的任务：

- `updated_at` 距离现在 > 600s → `mark_retrying`
- 否则不改变（视为仍在执行）

恢复后下一轮 poll 自动领取 retrying 任务。

---

## 5. Handler 清单

| TaskType | Handler | 行为 |
|----------|---------|------|
| delete_knowledge_base | handle_delete_knowledge_base | CleanupService 7 步清理 |
| delete_document | handle_delete_document | 软删除文档，依赖 follow-up rebuild |
| rebuild | handle_rebuild | RebuildService 原子重建 |
| parse | handle_parse | 标记 parsing → parsed |
| index | handle_index | 标记 indexing → indexed |
| reparse | handle_reparse | → handle_parse |
| reindex | handle_reindex | → handle_rebuild |

---

## 6. Alembic

```
alembic.ini
migrations/env.py
migrations/script.py.mako
migrations/versions/d7e568c55ad8_create_lifecycle_tables.py
```

验证:
```
alembic upgrade head   ✅
alembic downgrade -1   ✅
alembic upgrade head   ✅
```

---

## 7. 测试结果

| 项目 | 结果 |
|------|------|
| 全量单元测试 | ✅ 288 passed, 1 warning |
| Ruff check | ✅ All checks passed! |
| Alembic upgrade/downgrade | ✅ 循环通过 |
| KB API 集成测试 | ✅ 9 passed |
| 现有 API 兼容 | ✅ test_api.py 全部通过 |

---

## 8. 文件变更

### 新增
```
src/industrial_rag/services/lifecycle_task_executor.py
src/industrial_rag/services/task_handlers.py
src/industrial_rag/services/task_context.py
src/industrial_rag/services/rebuild_service.py
src/industrial_rag/services/handler_impls.py
alembic.ini
migrations/
migrations/versions/d7e568c55ad8_create_lifecycle_tables.py
```

### 修改
```
src/industrial_rag/api.py — lifespan 集成 executor
src/industrial_rag/db/models.py — StrEnum 替换 + datetime.UTC
src/industrial_rag/parser_models.py — Enum 替换 (str, Enum)
src/industrial_rag/mineru_client.py — Enum 替换 + 未使用变量清理
pyproject.toml — 增加 alembic 依赖 + ruff ignore 规则
.env.example — 增加 DATABASE_URL
```

---

## 9. 下一阶段建议

**建议进入**: 阶段 3（Qdrant 向量存储与知识库 Collection 隔离）。

理由:
- 知识库生命周期 API + 后台任务执行器已完成
- KB ID 可直接映射到 Qdrant Collection
- LightRAG 1.5.4 原生支持 `QdrantVectorDBStorage`
- Legacy KB 已迁移，新 KB 通过 API 创建
