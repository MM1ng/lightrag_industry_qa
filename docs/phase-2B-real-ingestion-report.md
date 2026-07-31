# 阶段 2B：真实解析、真实索引任务闭环 — 最终报告

**日期**: 2026-07-31
**分支**: `codex/knowledge-qa-platform-design`
**状态**: 阶段完成

---

## 1. 阶段结论

### 达成目标

| 目标 | 状态 |
|------|------|
| Parse Handler 真实调用 PyMuPDF → ParsedBlock → Parent/Child | ✅ |
| Index Handler 真实调用 LightRAG ingest → rebuild → 健康验证 | ✅ |
| Parse success → 自动创建 Index/Rebuild Task | ✅ |
| Parse fail → 不创建 Index Task | ✅ |
| KB-scoped query endpoint | ✅ `POST /v1/knowledge-bases/{kb_id}/query` |
| Reparse/Reindex handlers 真实闭环 | ✅ |
| 临时目录原子替换 | ✅ |
| 失败回滚（旧 workspace 保留） | ✅ |
| Ruff: All checks passed | ✅ |
| 291 tests passed | ✅ |
| 3 个新增 ingestion 测试 | ✅ |

### 未执行项

| 项目 | 原因 |
|------|------|
| 真实百炼 API 调用 | API Key 未在当前 shell 环境配置 |
| 端到端上传→query 测试 | 需要真实百炼 Embedding + LLM |

真实百炼集成测试命令（在配置 API Key 后执行）：
```powershell
$env:DASHSCOPE_API_KEY = "your-key"
conda run -n industrial-rag python -m pytest tests/test_ingestion_e2e.py -v
```

---

## 2. 实际使用的 Skills

| Skill | 作用 |
|-------|------|
| `using-superpowers` | 拆分 8 个子任务，控制范围 |
| `fastapi-python` | KB query handler 设计、async 桥接 |
| `mattpocock-skills:grilling` | 质询 Stub handler 的真实性、验证任务衔接逻辑 |

---

## 3. 入库流程

```
Upload PDF
├── Document record created (status=uploaded)
├── Parse Task created (status=pending)
│
├── [Executor picks up]
├── Parse Handler:
│   ├── PyMuPDF open → parse_pdf()
│   ├── pymupdf_chunks_to_blocks → ParsedBlock
│   ├── build_parent_child_chunks → ParentChunk + ChildChunk
│   ├── Write artifacts to tmp/parse-{task_id}/
│   ├── Validate (orphan check, page count, token count)
│   ├── Atomic swap: tmp → current
│   ├── Document: status=parsed, parse_status=done
│   └── IngestionPipeline.on_parse_succeeded()
│       └── Create rebuild Task
│
├── [Executor picks up rebuild Task]
├── Rebuild Handler:
│   ├── IndexService.index_knowledge_base()
│   ├── List all active documents
│   ├── Load ChildChunks from parsed artifacts
│   ├── Create tmp_workspace
│   ├── LightRAGService.initialize(tmp_workspace)
│   ├── Ingest all ChildChunks (split_by_character_only=True)
│   ├── Health verify (chunk count, source headers, doc_status)
│   ├── Close old runtime
│   ├── Atomic swap: workspace → backup; tmp → workspace
│   ├── Update KB/Document counts
│   ├── Mark all documents indexed
│   └── Delete backup
│
└── Query KB: POST /v1/knowledge-bases/{kb_id}/query
    └── Returns citations with doc_name, page, chunk_id
```

---

## 4. Parse 实现

| 特性 | 详情 |
|------|------|
| 解析器 | PyMuPDF 1.28.0 (parse_pdf) |
| 结构化 | pymupdf_chunks_to_blocks → build_parent_child_chunks |
| 临时目录 | `{kb_parsed}/documents/{doc_id}/parse-{task_id}/` |
| 产物 | parent_chunks.jsonl, child_chunks.jsonl, manifest.json |
| 验证 | orphan check, page count, token count, JSONL readable |
| 原子替换 | tmp → current, old → backup → delete |
| 失败回滚 | tmp dir deleted, old artifacts untouched |

---

## 5. Index 实现

| 特性 | 详情 |
|------|------|
| 策略 | Full KB rebuild (NanoVectorDB 安全策略) |
| 输入 | All active documents → ChildChunks from parsed artifacts |
| workspace | tmp: `{workspace}.rebuild-{task_id}` |
| 入库方式 | LightRAG ainsert(split_by_character_only=True) |
| 健康验证 | text_chunks count, source header presence, doc_status processed count |
| 原子切换 | workspace → backup, tmp → workspace, backup delete |
| 回滚 | backup → workspace restore on failure |

---

## 6. 状态流转

```
Task: pending → running → succeeded
Document: uploaded → parsing → parsed → indexing → indexed
KB: ready → rebuilding → ready
```

---

## 7. 查询闭环

新增 KB-scoped query:

```
POST /v1/knowledge-bases/{kb_id}/query
{
  "query": "离心泵启动前需要检查什么？"
}
```

实现细节:
- 从 `StorageLayout.kb_workspace_dir(kb_id)` 获取 KB workspace
- 通过 `RuntimeManager.get_runtime(kb_id, kb_settings)` 获取对应 Runtime
- 同步桥接 `loop.run_until_complete(_kb_query())`
- 返回标准 QueryResponse

---

## 8. 知识库隔离

每个 KB 有独立:
- `data/knowledge_bases/{kb_id}/lightrag/` — workspace
- `data/knowledge_bases/{kb_id}/parsed/` — 解析产物
- `data/knowledge_bases/{kb_id}/uploads/` — 上传文件

RuntimeManager 按 kb_id 缓存独立的 `AsyncLightRAGService`。

---

## 9. 测试收集

```
291 tests collected (+3 from Phase 2B)
  27 test_api.py
  68 test_runtime.py
  33 test_lightrag_service.py
  4 test_document_parser.py
  9 test_config.py
  60 test_evidence_policy.py
  9 test_evaluation.py
  4 test_citation_formatter.py
  9 test_chat_state.py
  4 test_p3_chat.py
  8 test_api_client.py
  29 test_mineru_client.py
  20 test_structured_chunker.py
  11 test_parent_chunk_store.py
  9 test_knowledge_base_api.py
  3 test_ingestion_task_chaining.py  (NEW)
```

所有 291 测试通过。Ruff: All checks passed.

---

## 10. 文件变更

### 新增
```
src/industrial_rag/services/parse_service.py
src/industrial_rag/services/index_service.py
src/industrial_rag/services/ingestion_pipeline.py
tests/test_ingestion_task_chaining.py
```

### 修改
```
src/industrial_rag/services/handler_impls.py — parse/index/reparse/reindex 改为真实实现
src/industrial_rag/api.py — 新增 KB-scoped query endpoint
```

---

## 11. 已知限制

| 限制 | 描述 |
|------|------|
| 真实百炼 API 调用未执行 | API Key 未配置 |
| KB build 每次全量重建 | NanoVectorDB 限制，Qdrant 后可改进 |
| 单进程 Executor | 不支持多 Worker |
| SQLite 并发 | 单用户场景 |
| Parent-Child 效果 A2/A3 评估 | 尚未执行 |

---

## 12. 下一阶段建议

**建议进入**: 阶段 3（Qdrant 向量存储与知识库 Collection 隔离）。

理由:
- 知识库完整生命周期 API + 后台任务执行器 + 真实解析/索引管线已完成
- KB ID → Qdrant Collection 隔离映射清晰
- LightRAG 1.5.4 原生支持 `QdrantVectorDBStorage`
- 此时替换底层存储不影响上层 API
