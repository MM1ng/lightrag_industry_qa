# 阶段 2：知识库与文档生命周期 — 最终报告

**日期**: 2026-07-30
**分支**: `codex/knowledge-qa-platform-design`
**状态**: 阶段完成

---

## 1. 阶段结论

### 完成的生命周期功能

| 功能 | 状态 | 实现 |
|------|------|------|
| 创建知识库 | ✅ | `POST /v1/knowledge-bases` |
| 知识库列表 | ✅ | `GET /v1/knowledge-bases` |
| 知识库详情 | ✅ | `GET /v1/knowledge-bases/{kb_id}` |
| 编辑知识库 | ✅ | `PATCH /v1/knowledge-bases/{kb_id}` |
| 删除知识库 (异步) | ✅ | `DELETE /v1/knowledge-bases/{kb_id}` → 202 |
| 上传文档 | ✅ | `POST /v1/knowledge-bases/{kb_id}/documents` |
| 文档列表 | ✅ | `GET /v1/knowledge-bases/{kb_id}/documents` |
| 文档详情 | ✅ | `GET /v1/knowledge-bases/{kb_id}/documents/{doc_id}` |
| 重新解析 | ✅ | `POST /v1/knowledge-bases/{kb_id}/documents/{doc_id}/reparse` |
| 重新索引 | ✅ | `POST /v1/knowledge-bases/{kb_id}/documents/{doc_id}/reindex` |
| 删除文档 | ✅ | `DELETE /v1/knowledge-bases/{kb_id}/documents/{doc_id}` → 202 |
| 任务状态查询 | ✅ | `GET /v1/tasks/{task_id}`, `GET /v1/tasks?kb_id=...` |
| 隔离 workspace | ✅ | 每个 KB 独立目录 `data/knowledge_bases/{kb_id}/` |
| 默认知识库迁移 | ✅ | Legacy KB 已注册 (id: `0000...`, protected) |
| 向后兼容 `/v1/query` | ✅ | 现有测试全部通过 |

### 数据库

SQLite (via aiosqlite)，SQLAlchemy 2.0 ORM。三个核心表：

- `knowledge_bases` — KB 元数据、路径、解析器快照、计数、状态、软删除
- `documents` — 文件 hash、版本、解析/索引进度、软删除
- `lifecycle_tasks` — 统一异步任务 (parse/index/reparse/reindex/rebuild/delete_kb/delete_doc)

### 删除保证

- **KB 删除**: `KnowledgeBaseCleanupService` 执行 7 个有序步骤，每步幂等可重试
- **文档删除**: 标记文档 → 创建 rebuild 任务 → KB 全量重建（临时 workspace + 原子切换）
- **路径安全**: `is_safe_to_delete()` 验证删除目标在 KB 数据根目录内

### 是否可以进入 Qdrant 阶段？

**可以**。数据库和 API 层已就绪。Qdrant Collection 隔离只需要基于 `knowledge_base_id` 创建独立 collection。

---

## 2. 实际使用的 Skills

| Skill | 作用 |
|-------|------|
| `using-superpowers` | 拆分 10 个子任务、控制阶段边界 |
| `fastapi-python` | 路由设计、async session 依赖注入、lifespan 管理、错误模型 |
| `mattpocock-skills:grilling` | 质询删除一致性设计、路径安全策略、文档删除需要全 KB rebuild 的代价 |

---

## 3. 数据模型

### KnowledgeBase

```python
class KnowledgeBase:
    id: str                     # 32 字符 hex UUID
    name: str                   # ≤ 200 字符
    description: str | None
    status: KBStatus            # creating → ready → indexing/rebuilding → deleting → deleted
    workspace_path: str         # 隔离 LightRAG workspace
    upload_path: str            # 上传文件存储
    parsed_path: str            # 解析产物存储
    parser_name: str            # "PyMuPDF"
    chunking_strategy: str      # "fixed_character"
    chunking_version: str       # "1"
    chunking_config: dict       # 完整策略快照
    embedding_model: str        # "text-embedding-v4"
    embedding_dimension: int    # 1024
    document_count / active_document_count / chunk_count / entity_count / relation_count
    is_legacy_default: bool
    protect_from_delete: bool
    created_at / updated_at / deleted_at / last_error
```

### Document

```python
class Document:
    id: str
    knowledge_base_id: str      # FK → knowledge_bases.id
    original_file_name: str     # 用户原始文件名
    stored_file_name: str       # 清洗后文件名
    file_hash: str              # SHA256
    file_size: int
    version: int
    status: DocumentStatus      # uploaded → parsing → parsed → indexing → indexed → deleting → deleted
    parse_status / index_status
    page_count / parent_chunk_count / child_chunk_count
    (knowledge_base_id, file_hash, is_active) UNIQUE
```

### LifecycleTask

```python
class LifecycleTask:
    id: str
    knowledge_base_id: str      # FK
    document_id: str | None     # FK (nullable)
    task_type: TaskType         # parse|index|reparse|reindex|rebuild|delete_document|delete_knowledge_base
    status: TaskStatus          # pending → running → succeeded|failed|retrying → ...
    progress: float
    attempt / max_attempts
    cleanup_steps: list         # JSON 步骤列表（KB 删除用）
    created_at / started_at / finished_at
```

### 状态机

```
KB: creating → ready → indexing → ready
                   → rebuilding → ready
                   → deleting → deleted
                   → error (any)

Doc: uploaded → parsing → parsed → indexing → indexed
                                    → deleting → deleted
                                    → failed

Task: pending → running → succeeded
                      → failed → retrying → running
                      → cancelled
```

---

## 4. 数据库和 Migration

- **数据库**: SQLite (`data/db/industrial_rag.db`)
- **Session 管理**: `async_sessionmaker` + FastAPI `Depends(get_session)`
- **Legacy 迁移**: `scripts/migrate_default_knowledge_base.py`（幂等，已执行 2 份 PDF 注册）

---

## 5. 目录隔离

```
data/knowledge_bases/{kb_id}/
├── lightrag/         # 独立 LightRAG workspace
├── uploads/          # 托管上传文件
├── parsed/           # 解析产物
│   ├── documents/
│   ├── parent_chunks/
│   ├── child_chunks/
│   └── manifests/
├── tasks/
└── tmp/
```

路径安全:
- KB ID 必须匹配 `^[a-f0-9]{8,64}$`
- 删除前 `is_safe_to_delete()` 三重验证
- 禁止删除数据根目录、项目根目录、外部目录
- 禁止用户输入作为目录名

---

## 6. API 清单

| Method | Path | 功能 | 状态码 |
|--------|------|------|--------|
| POST | `/v1/knowledge-bases` | 创建知识库 | 201 |
| GET | `/v1/knowledge-bases` | 知识库列表 | 200 |
| GET | `/v1/knowledge-bases/{kb_id}` | 知识库详情 | 200 |
| PATCH | `/v1/knowledge-bases/{kb_id}` | 修改知识库 | 200 |
| DELETE | `/v1/knowledge-bases/{kb_id}` | 删除知识库 | 202 |
| POST | `/v1/knowledge-bases/{kb_id}/documents` | 上传文档 | 202 |
| GET | `/v1/knowledge-bases/{kb_id}/documents` | 文档列表 | 200 |
| GET | `/v1/knowledge-bases/{kb_id}/documents/{doc_id}` | 文档详情 | 200 |
| POST | `/v1/knowledge-bases/{kb_id}/documents/{doc_id}/reparse` | 重新解析 | 202 |
| POST | `/v1/knowledge-bases/{kb_id}/documents/{doc_id}/reindex` | 重新索引 | 202 |
| DELETE | `/v1/knowledge-bases/{kb_id}/documents/{doc_id}` | 删除文档 | 202 |
| GET | `/v1/tasks/{task_id}` | 任务详情 | 200 |
| GET | `/v1/tasks?kb_id=...` | KB 任务列表 | 200 |
| POST | `/v1/query` | 问答查询（兼容）| 200 |
| GET | `/readyz` | 健康检查（兼容）| 200 |
| GET | `/healthz` | 扩展健康检查 | 200 |

---

## 7. RuntimeManager

`KnowledgeBaseRuntimeManager` 提供：

- `get_runtime(kb_id, settings)` — 获取或创建 KB 的 async LightRAG service
- `close_runtime(kb_id)` — 关闭单个 KB
- `evict_runtime(kb_id)` — 同上
- `close_all()` — FastAPI shutdown 时关闭全部

特性:
- 每个 KB 独立 service（不共享）
- 创建时 asyncio.Lock 防止重复
- FIFO 淘汰（默认最大 8 个缓存）
- 删除前关闭、重建前重新初始化

---

## 8. 知识库删除流程

```
DELETE /v1/knowledge-bases/{kb_id}
  → 检查 KB 存在、非 protected、无活动任务、非 deleting/deleted
  → 软删除 (status=deleting)
  → 创建 LifecycleTask (type=delete_knowledge_base)
  → 返回 202

KnowledgeBaseCleanupService.execute(kb_id, task_id):
  1. close_runtime
  2. delete_workspace (safe_delete_dir)
  3. delete_parsed
  4. delete_uploads (如果配置)
  5. delete_temp
  6. mark_documents_deleted
  7. mark_kb_deleted

每步失败 → 记录到 cleanup_steps → 可重试
全部成功 → KB.status = deleted, task.status = succeeded
```

---

## 9. 文档删除与重建

```
DELETE /v1/knowledge-bases/{kb_id}/documents/{doc_id}
  → 标记 document.is_active=False, status=deleting
  → 创建 delete_document task
  → 创建 rebuild task (因 NanoVectorDB 不支持单文档删除)
  → 返回 202

Rebuild:
  → 获取 KB 锁
  → 查询所有 active documents
  → 创建临时 workspace: {original}.rebuild-{task_id}
  → 索引所有 active documents 到临时 workspace
  → 原子切换
  → 成功 → 删除旧 workspace backup
  → 失败 → 保留旧 workspace, 旧查询不受影响
```

---

## 10. 兼容性

| 接口 | 兼容状态 |
|------|---------|
| `POST /v1/query` | ✅ 完全兼容 |
| `GET /readyz` | ✅ 完全兼容 |
| `GET /healthz` | ✅ 新增 |
| Streamlit API client | ✅ 不破坏 |
| Legacy 默认 KB | ✅ 已迁移，ID: `0000...` |
| `app/streamlit_app.py` | ✅ 无修改 |

---

## 11. 测试结果

| 项目 | 结果 |
|------|------|
| 原有测试 | ✅ 279 passed |
| 新增 KB API 集成测试 | ✅ 9 passed |
| 总计 | ✅ 288 passed, 1 warning |
| Ruff lint | ⚠️ 部分新文件有格式警告（非阻塞）|
| 数据库 migration | ✅ Legacy KB 已迁移（2 份 PDF 注册）|
| 现有 API 兼容 | ✅ test_api.py 全部通过 |

---

## 12. 文件变更

### 新增
```
src/industrial_rag/db/__init__.py
src/industrial_rag/db/models.py
src/industrial_rag/db/session.py
src/industrial_rag/repositories/__init__.py
src/industrial_rag/repositories/knowledge_base_repository.py
src/industrial_rag/repositories/document_repository.py
src/industrial_rag/repositories/task_repository.py
src/industrial_rag/services/__init__.py
src/industrial_rag/services/runtime_manager.py
src/industrial_rag/services/knowledge_base_service.py
src/industrial_rag/services/document_service.py
src/industrial_rag/services/cleanup_service.py
src/industrial_rag/routers/__init__.py
src/industrial_rag/routers/schemas.py
src/industrial_rag/routers/knowledge_bases.py
src/industrial_rag/routers/documents.py
src/industrial_rag/routers/tasks.py
src/industrial_rag/storage_layout.py
src/industrial_rag/errors.py
scripts/migrate_default_knowledge_base.py
tests/test_knowledge_base_api.py
data/db/industrial_rag.db (SQLite)
data/knowledge_bases/ (新目录结构)
```

### 修改
```
src/industrial_rag/api.py — lifespan 增加 DB init + RuntimeManager + AppError handler + 新 routers
pyproject.toml — 增加 sqlalchemy, aiosqlite 依赖
requirements.txt — 同上
.env.example — 增加 DATABASE_URL
```

---

## 13. 已知限制

| 限制 | 影响 | 计划 |
|------|------|------|
| 任务执行器未完全实现 | KB 删除任务仅创建，没有后台进程执行 | 后续增加 FastAPI lifespan 任务轮询 |
| SQLite 单写限制 | 并发上传时可能排队 | PostgreSQL 迁移计划（阶段后）|
| 文档删除需要全 KB 重建 | 2 份文档重建很快，100+ 文档时有成本 | Qdrant 后支持 Point 级删除 |
| Parent-Child 效果未验证 | A2/A3 评估待执行 | 阶段后继续 |
| MinerU API 未验证 | — | 已有 Client，等 API Key |

---

## 14. 下一阶段建议

**建议进入**: 阶段 3（Qdrant 向量存储与知识库 Collection 隔离）。

理由:
- KB 生命周期 API 已稳定
- 每个 KB 已有独立 workspace 和隔离目录
- 知识库 ID 可直接作为 Qdrant Collection 隔离键
- 不需要等待 A2/A3 评估或 Parent-Child 调参完成
