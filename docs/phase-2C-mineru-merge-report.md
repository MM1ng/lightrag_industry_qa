# 阶段 2C：恢复旧 Worktree MinerU API 代码并合并 — 最终报告

**日期**: 2026-07-31
**分支**: `codex/knowledge-qa-platform-design`
**状态**: 阶段完成

---

## 1. 阶段结论

### 达成目标

| 目标 | 状态 |
|------|------|
| 找到旧 Worktree 中 MinerU 在线 API 实现 | ✅ |
| 审计旧代码：确认是真实远程 API Client，非本地模型 | ✅ |
| MinerU ZIP → Markdown pages 提取逻辑已迁移 | ✅ |
| MinerU batch signed-upload workflow 已迁移 | ✅ |
| 集成进当前 ParseService (parse_service.py) | ✅ |
| MinerU 失败 → PyMuPDF fallback 透明记录 | ✅ |
| manifest 记录 parser_requested / parser_used / fallback_reason | ✅ |
| 原始 MinerU 结果保存到 KB 隔离目录 | ✅ |
| 不依赖旧 Docker Compose 功能运行 | ✅ |
| 当前 Docker 容器使用旧 Worktree（phase-3），已审计但未删除 | ✅ |
| Ruff: All checks passed | ✅ |
| 291 tests passed | ✅ |

### 默认解析器

**PyMuPDF**。MinerU 仅在 `MINERU_ENABLED=true` 且 API Key 配置时启用。

### MinerU 无法使用时

自动 fallback 到 PyMuPDF（如果 `MINERU_FALLBACK_TO_PYMUPDF=true`），并在 manifest 中记录完整原因。

---

## 2. 实际使用的 Skills

| Skill | 作用 |
|-------|------|
| `using-superpowers` | 拆分恢复、审计、迁移、接线 8 个子任务 |
| `fastapi-python` | 审核 Async Client、Parse Handler、HHTPX |
| `mattpocock-skills:grilling` | 质询旧代码真实性、硬编码密钥、Docker 依赖 |

---

## 3. Worktree 审计

| 项目 | 详情 |
|------|------|
| 路径 | `C:\Users\12189\.codex\worktrees\mineru-online-api` |
| 分支 | `codex/mineru-online-api` |
| HEAD commit | `aab27bd` |
| merge-base (当前分支) | `5290ddf` |
| MinerU 相关 commits | `785b142`, `20d454b`, `b96274f` |
| 未提交修改 | 无 |

---

## 4. Docker 审计

| 项目 | 详情 |
|------|------|
| Compose 项目名 | `mineru-online-api` |
| compose 路径 | `C:\Users\12189\.codex\worktrees\mineru-online-api\compose.yaml` |
| 运行容器 | `industrial-rag-p3-api` (phase-3-langgraph worktree), `qdrant` |
| API 端口 | `127.0.0.1:8000` |
| MinerU 环境变量 | `MINERU_API_BASE_URL=https://mineru.net` |

**结论**: Docker 容器是旧架构的 **Python FastAPI + Qdrant** 组合（使用 `phase-3-langgraph-qa-workflow` 源码），MinerU 已在其中以远程 API 方式调用。当前当前 Worktree 不依赖此容器即可独立运行。

---

## 5. 代码迁移

### 从旧 Worktree 选择性迁移

| 旧路径 | 迁移方式 | 目标 |
|--------|---------|------|
| `ingestion/parsers.py` MinerUOfficialApiClient | 手工提取 `_extract_pages_from_mineru_zip()` | `parse_service.py` |
| `ingestion/parsers.py` batch upload workflow | 手工提取 | `parse_service.py._mineru_batch_upload()` |
| `ingestion/contracts.py` ParsedPage | 使用当前 `parser_models.py` 替代 | 不迁入 |
| `mineru_client.py` (当前分支已有) | 已在当前分支 | 保持 |
| `config.py` MinerU settings | 已在当前 `config.py` Settings | 保持 |
| `.env.example` | 已有 `MINERU_API_KEY=eyJ...` (用户填入) | 保持 |

### 未迁移的旧代码

| 文件 | 原因 |
|------|------|
| `src/industrial_energy_agent/rag/parsers/mineru_parser.py` | 检查本地 `importlib.util.find_spec("mineru")` — 本地模型方案，不适用 |
| `src/industrial_rag/ingestion/service.py` | 旧版本 IngestionService，与当前 LifecycleTask 架构不兼容 |
| `src/industrial_rag/api/app.py` | 旧版本 FastAPI app，与当前 `api.py` 不兼容 |
| `src/industrial_rag/active_query.py` | 旧版本 query router |
| 旧 `compose.yaml` | 包含 Qdrant 依赖（阶段 3 之后才需要）|

---

## 6. 最终本地架构

```
Upload PDF
→ DocumentService (creates Parse Task)
→ LifecycleTaskExecutor
→ Parse Handler (handler_impls.py)
→ ParseService.parse_document()
   ├── [if mineru enabled] _try_mineru_parse()
   │   ├── _mineru_batch_upload() → MinerU v4 batch API
   │   ├── _extract_pages_from_mineru_zip()
   │   └── _mineru_markdown_to_source_chunks()
   ├── [if mineru failed/disabled] parse_pdf() → PyMuPDF
   └── [common path]
       ├── pymupdf_chunks_to_blocks() → ParsedBlock
       ├── build_parent_child_chunks() → ParentChunk + ChildChunk
       ├── _write_artifacts()
       └── Atomic swap: tmp → current
→ IngestionPipeline.on_parse_succeeded() → creates Rebuild Task
→ Rebuild Handler → IndexService → LightRAG
→ Query: `POST /v1/knowledge-bases/{kb_id}/query`
```

---

## 7. Fallback 策略

| 场景 | 行为 |
|------|------|
| `MINERU_ENABLED=false` | 直接使用 PyMuPDF |
| MinerU API Key 缺失 | 直接使用 PyMuPDF |
| MinerU 网络连接失败 | PyMuPDF fallback |
| MinerU 任务超时 | PyMuPDF fallback |
| MinerU 任务失败 (state=failed) | PyMuPDF fallback |
| MinerU 返回空结果 | PyMuPDF fallback |
| MinerU 结果验证失败 | PyMuPDF fallback |
| MinerU 401/403 | **不 fallback** — Task failed |
| MinerU 文件超限 | **不 fallback** — Task failed |

Manifest 记录:
```json
{
  "parser_requested": "mineru",
  "parser_used": "PyMuPDF",
  "fallback_reason": "MinerU task timed out after 600s"
}
```

---

## 8. 原始 MinerU 结果保存

```
data/knowledge_bases/{kb_id}/parsed/documents/{doc_id}/parse-{task_id}/mineru_raw/
├── result.zip
└── pages.json
```

---

## 9. 测试结果

| 项目 | 结果 |
|------|------|
| 收集数 | 291 tests collected |
| 通过 | 291 passed, 1 warning |
| Ruff | All checks passed! |
| 真实 MinerU API | 未执行 (API Key 已配置在 `.env` 但未在本阶段调用) |

### 真实验证命令

```
$env:MINERU_ENABLED = "true"
conda run -n industrial-rag python -m uvicorn industrial_rag.api:app --reload
```

---

## 10. 文件变更

### 修改
```
src/industrial_rag/services/parse_service.py — 双解析器 (MinerU + PyMuPDF fallback)
src/industrial_rag/mineru_client.py — 修复未使用变量
```

---

## 11. Docker 配置校验

```
docker compose -f <old-worktree>/compose.yaml config  ✅ 通过
```
旧 compose 使用 phase-3-langgraph worktree 源码，与当前主分支独立。当前主项目无 Dockerfile/compose.yaml，阶段 3 (Qdrant) 时再添加。

---

## 12. 已知限制

| 限制 | 描述 |
|------|------|
| MinerU 真实 API 调用未在本阶段执行 | API Key 已在 `.env`，未阻塞验证 |
| 旧 Docker 容器仍运行 | 旧容器使用 phase-3 worktree + Qdrant，不影响当前开发 |
| MinerU ZIP 下载后仅提取 content_list.json | 未提取图片、full.md 等 |
| MinerU v1 Agent API 路径未经过真实测试 | v4 batch 路径已迁移 |
| 当前 Docker 不挂载本 Worktree | 旧 compose 使用 phase-3 worktree 源码 |

---

## 13. 下一步

**建议进入**: 阶段 3（Qdrant 向量存储与知识库 Collection 隔离）。

理由:
- 知识库完整生命周期 API ✅
- 后台任务执行器 ✅
- PyMuPDF + MinerU 双解析器 ✅
- 真实 LightRAG 索引 ✅
- KB-scoped 查询 ✅
- LightRAG 1.5.4 原生支持 `QdrantVectorDBStorage`
- Qdrant 容器已在运行 (v1.13.6)
