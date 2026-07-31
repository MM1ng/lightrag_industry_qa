# Phase 3 最终报告：Qdrant 分代存储生命周期

**报告日期**: 2026-08-01
**分支**: `codex/knowledge-qa-platform-design`
**基线提交**: `64dcee4`（Phase 1A-2D complete）

---

## 1. 阶段结论

- **Phase 3 complete（已验证）**
- Qdrant 已真实接入：真实 Qdrant Server（v1.13.6）上完成 collection 创建、point 写入、query、KB 隔离、generation 隔离、精确清理、重启恢复与故障传播验证。
- Nano 完全兼容：默认后端仍为 Nano，Nano 原有 workspace 保留，Nano 回归测试全数通过。
- 迁移/回滚已真实验证：Nano → Qdrant API 迁移成功、失败不提升、fingerprint 过期拒绝回滚、fingerprint 一致时回滚成功。
- **允许进入 Phase 3A**（MinerU/PyMuPDF 对比）；Rerank 未启动。

> 本报告只使用真实命令与真实运行结果；Fake Client 与真实 Qdrant 验证分别计数，不互相冒充。

---

## 2. 受控环境

| 项目 | 值 |
|---|---|
| Conda 环境 | `industrial-rag`（`C:\Users\12189\.conda\envs\industrial-rag`） |
| Python | 3.11.15（`C:\Users\12189\.conda\envs\industrial-rag\python.exe`） |
| LightRAG | `lightrag-hku==1.5.4`（`C:\Users\12189\.conda\envs\industrial-rag\Lib\site-packages\lightrag`） |
| qdrant-client | 1.18.0 |
| Qdrant Server | 镜像 `qdrant/qdrant:v1.13.6`，专用测试容器 `ira-phase3-qdrant-test`（`127.0.0.1:16333`） |
| pytest / pytest-asyncio | 已安装且 `asyncio_mode=strict` 生效（收集无报错） |

全部命令统一使用 `C:\Users\12189\.conda\envs\industrial-rag\python.exe -m ...`，未混用全局 pytest。

---

## 3. LightRAG 源码审计

审计对象为当前 Conda 环境中实际安装的 LightRAG 1.5.4 源码（非记忆、非旧 Worktree）。

### 3.1 Storage 接口与 namespace

- 抽象基类：`lightrag.base.BaseVectorStorage`（dataclass，含 `namespace`、`workspace`、`embedding_func`、`meta_fields`、`global_config` 字段）。
- 项目自定义 `PhysicalQdrantVectorDBStorage` 继承该基类，并实现 `initialize` / `finalize` / `query` / `upsert` / `delete` / `drop` / `index_done_callback`。
- namespace 来源：`lightrag.namespace.py` 中 `NameSpace` 常量：`VECTOR_STORE_CHUNKS = "chunks"`、`VECTOR_STORE_ENTITIES = "entities"`、`VECTOR_STORE_RELATIONSHIPS = "relationships"`，与项目白名单 `QDRANT_VECTOR_NAMESPACES = ("chunks", "entities", "relationships")` 完全一致。
- LightRAG 实例化向量存储时传入 `namespace`、`workspace`、`embedding_func` 与固定 `meta_fields`；项目 adapter 的 `meta_fields` 与之一致（chunks: `{full_doc_id, content, file_path}`；entities: `{entity_name, source_id, content, file_path}`；relationships: `{src_id, tgt_id, source_id, content, file_path}`）。

### 3.2 workspace 行为（本次关键修复）

- `lightrag/utils.py::validate_workspace`：workspace 必须是单一路径组件（拒绝 `/`、`\`、`.`、`..`）。
- `JsonKVStorage` / `JsonDocStatusStorage` / `NetworkXStorage`：workspace 非空时数据写入 `working_dir/<workspace>/`，否则写入 `working_dir/` 根目录。
- `lightrag/kg/shared_storage.py::get_final_namespace(namespace, workspace)`：workspace 为空时共享内存键退化为裸 namespace（全进程共享）。
- **根因**：此前所有 LightRAG 实例均以 `workspace=""` 创建，同进程内 Nano 实例与 Qdrant shadow 实例共享同一份内存 `doc_status`，导致 Qdrant shadow 阶段把 Nano 已处理的文档误判为重复、0 点入库。
- **修复**：`settings_for_knowledge_base` 按 backend+generation 生成唯一 token（`nano-<gen>` / `qdrant-<gen>`，legacy 无 generation 记录时保持 `None` → `""`），`build_official_backend` 将其传给 `LightRAG(workspace=...)`；所有项目侧文件路径引用（健康检查、doc_status、GraphML）同步改为读取 `working_dir/<token>/`。真实 E2E 日志确认修复后 Qdrant 阶段 `doc status load ... with 0 records` 并正常完成抽取与写入。

### 3.3 接口兼容性核验

- 构造参数：`LightRAG(working_dir, llm_model_func, llm_model_name, embedding_func, vector_storage, workspace, vector_db_storage_cls_kwargs, ...)` 与 1.5.4 构造签名一致。
- embedding：`EmbeddingFunc(embedding_dim=1024, max_token_size=8192, send_dimensions=True)`，adapter 以 `self.embedding_func.embedding_dim` 建 collection，`embedding_func(texts)` 批量调用。
- point ID：`_point_id(value)` = `uuid.UUID(bytes=sha256(value)[:16])`，稳定。
- upsert payload：保留 `full_doc_id / content / file_path`（chunks）、`entity_name / source_id / content / file_path`（entities）、`src_id / tgt_id / source_id / content / file_path`（relationships），与 LightRAG 查询所需字段一致。
- query 返回：与 `BaseVectorStorage.query` 契约一致（id、distance/score、payload 字段），`cosine_better_than_threshold` 通过 `vector_db_storage_cls_kwargs` 传入。
- 生命周期：`finalize()` 关闭 `AsyncQdrantClient`；`index_done_callback` 为 no-op（与 Qdrant 后端行为一致）；网络/认证异常直接向上传播。
- collection 复用校验：已存在 collection 时核对 dimension 与 distance（COSINE），不匹配即拒绝复用。

---

## 4. Collection 设计

- 命名规则（`CollectionNameResolver`，单一可信来源）：`<prefix>_kb_<kb_id>_<generation>_<namespace>`。
  - prefix 来自服务端 Settings（`QDRANT_COLLECTION_PREFIX`），客户端不可指定；
  - kb_id 与 generation 均经正则校验（`_validate_kb_id`、`^g[a-z0-9]{8,63}$`），不含用户 KB 名、文档名或 Windows 路径；
  - 测试 prefix 与生产 prefix 分离，删除只使用持久化 generation 记录解析出的精确名称。
- 每个 KB × generation 固定三个物理 collection：`chunks`、`entities`、`relationships`。
- dimension：1024（`text-embedding-v4`）；distance：COSINE。
- payload：见 §3.3。

---

## 5. Migration

Revision 链（一次性 SQLite 数据库验证）：

```text
d7e568c55ad8 (Phase 2D) -> 4f2c7d9a8b1e (add Qdrant vector backend generation metadata)
                        -> 9e6f0a2c3b4d (add normalized vector index generations)
```

真实命令与结果（`DATABASE_URL=sqlite:///<temp>`）：

```text
alembic upgrade head        -> OK（d7e568c55ad8 -> 4f2c7d9a8b1e -> 9e6f0a2c3b4d）
alembic downgrade -1        -> OK（9e6f0a2c3b4d -> 4f2c7d9a8b1e）
alembic upgrade head        -> OK（4f2c7d9a8b1e -> 9e6f0a2c3b4d）
```

Legacy 兼容验证：在 `d7e568c55ad8` 阶段插入 Phase 2D 格式的 `knowledge_bases` 行 → `upgrade head` 后仍可读取，`vector_backend='nano'`、`active_vector_generation_id=NULL`、`vector_index_generations` 无伪造行 → `downgrade -1` + `upgrade head` 再次通过。

---

## 6. 单元测试

质量命令（全部在 `industrial-rag` 解释器下执行）：

```text
python -m pytest --collect-only -q   -> 369 collected（Phase 2D 基线 307，新增 62）
python -m pytest -q                  -> 358 passed, 11 skipped, 0 failed
python -m ruff check .               -> All checks passed
```

新增测试资产与规模：

| 测试文件 | 数量 | 类型 |
|---|---|---|
| tests/test_vector_collections.py | 26 | CollectionNameResolver 边界/契约 |
| tests/test_physical_qdrant_storage.py | 13 | Fake Async Qdrant Client 单元 |
| tests/test_kb_runtime_settings.py | 3 | 运行时设置解析 |
| tests/test_generation_fingerprint_service.py | 2 | 确定性指纹 |
| tests/test_vector_index_generation_models.py | 1 | 数据模型/FK |
| tests/test_vector_index_generation_repository.py | 1 | generation repository |
| tests/test_runtime_manager_generations.py | 1 | Runtime cache key |
| tests/test_storage_layout_generations.py | 1 | workspace 布局 |
| tests/test_lifecycle_restart_recovery.py | 1 | running 任务恢复 |
| tests/test_vector_backend_api.py | 1 | 后端切换 API |

**Fake Client 单元测试数：13**（覆盖：不存在时创建、一致时复用、dimension/distance 冲突、upsert/query/payload/score、精确删除、幂等删除、删除/网络/auth 错误传播、client close、失败不激活、last_error 记录等）。

Skips 共 11 项：9 项真实 Qdrant 集成 + 2 项真实 E2E，均因 opt-in 环境变量未设置（常规单测模式下符合设计）。启用后见 §7/§8。

> 本报告不将 Fake Client 测试计入真实 Qdrant 验证。

---

## 7. 真实 Qdrant 验证

命令：

```text
$env:IRA_QDRANT_INTEGRATION="1"
python -m pytest tests/test_qdrant_integration.py -q
```

结果：**9 passed**。

覆盖场景：

| 场景 | 结果 |
|---|---|
| 真实 collection 创建（名称/dimension=1024/distance=COSINE/status 正常） | ✅ |
| 真实写入与查询（chunks/entities/relationships 三 namespace） | ✅ |
| 真实 count == upsert 点数 | ✅ |
| KB 隔离（KB-A 查询不返回 KB-B） | ✅ |
| Generation 隔离（G1/G2 物理 collection 不同） | ✅ |
| 精确清理（只删除登记 generation 的 collection） | ✅ |
| 客户端重建模拟重启后查询 | ✅ |
| verify_generation 对缺失 collection 明确报错 | ✅ |
| Qdrant 不可用时报明确错误 | ✅ |

测试 prefix：每次运行随机生成（`ira_e2e_<hex>` / 集成随机 prefix），所有测试结束时只删除精确登记名称；核验后 Qdrant 现存 collection 为 **0**（含先前失败运行遗留的 6 个精确命名 collection，均已精确删除）。

---

## 8. 生命周期 E2E

命令：

```text
$env:IRA_QDRANT_E2E="1"
python -m pytest tests/test_qdrant_e2e_migration.py -q -s
```

结果：**2 passed**（真实 PDF + 真实 DashScope embedding/LLM + 真实 Qdrant）。

主场景验证：

1. PyMuPDF 解析 → Nano 基线建立（真实抽取，kimi-k2.6 403 自动降级 qwen 成功）。
2. `POST /v1/knowledge-bases/{id}/vector-backend {"target_backend":"qdrant"}`：
   - 相同任务 pending 时重复请求返回同一 `task_id`（幂等）；
   - 与运行中迁移冲突的请求返回 409；
   - 迁移任务最终 succeeded。
3. 真实 Qdrant `verify_generation(expected_chunks=len(children))`：chunks 点数 ≥ ChildChunk 数；KB 切换为 `qdrant` + 新 active generation；Nano workspace 保留；旧 Runtime 被 evict。
4. API 查询（Qdrant Runtime）返回 200 且 status 为 success/insufficient_evidence。
5. 模拟重启：`close_all()` 后重新查询成功（从 DB active generation 重新初始化，不创建多余 collection）。
6. 注入 ChildChunk drift → 回滚请求 202，任务失败且 `error_code="nano_generation_stale"`，KB 保持 qdrant。
7. 恢复 fingerprint → 回滚任务 succeeded，KB 切回 nano，active generation 为 nano。
8. 回滚后 Nano 查询成功。

失败不提升场景：Qdrant URL 指向不可达地址 → 迁移任务失败，KB 保持 nano，无 active qdrant generation，无孤儿 collection。

---

## 9. 重启和故障

- Runtime 重启：E2E 第 5 步 evict + 重建后查询成功。
- Qdrant 故障：集成测试覆盖不可达时报错明确，迁移失败不提升（E2E 第 2 个测试）。
- Qdrant 恢复：E2E 使用真实实例完成迁移后查询成功。
- 启动任务恢复：`lifecycle_task_executor` 将 `running` 任务恢复为 retrying（`test_lifecycle_restart_recovery.py` 通过）。
- 不出现 `RuntimeError: This event loop is already running`：KB 查询走 async route + RuntimeManager，无 `run_until_complete` 嵌套。

---

## 10. 回归

Phase 2 原有功能回归（358 passed 中全部通过）：

- 默认 Nano KB（`test_runtime_settings_preserve_legacy_nano_workspace` 等）
- Legacy `/v1/query`
- KB scoped query
- Upload / Parse / PyMuPDF
- MinerU 可选配置与 fallback
- Parent/Child 切块
- LifecycleTaskExecutor
- KB 删除 / Document 删除触发全 KB rebuild
- protected KB
- readiness、startup/shutdown

Qdrant disabled 时不要求 Qdrant 可用；`VECTOR_BACKEND=nano` 时无需 `QDRANT_URL`。

---

## 11. 质量命令汇总

| 命令 | 结果 |
|---|---|
| `python -m pytest --collect-only -q` | 369 collected |
| `python -m pytest -q` | 358 passed / 11 skipped / 0 failed |
| `python -m ruff check .` | All checks passed |
| `python -m ruff format --check .` | 41 个文件存在基线格式漂移（HEAD 基线即不通过，非本阶段引入；仓库未启用 format 门禁，未做无关重排） |
| `alembic upgrade head` / `downgrade -1` / `upgrade head`（临时 SQLite） | 全部通过 |
| `IRA_QDRANT_INTEGRATION=1 pytest tests/test_qdrant_integration.py` | 9 passed |
| `IRA_QDRANT_E2E=1 pytest tests/test_qdrant_e2e_migration.py -s` | 2 passed |
| Qdrant 清理核验 | 0 collection 剩余 |

---

## 12. 文件变更与 commit

见提交说明（本报告生成后按逻辑拆分提交，记录于提交信息与 `git log`）。新增/修改文件清单同 `docs/phase-3-qdrant-storage-progress-report.md` §4，另新增本报告。未提交差异备份 `phase3-uncommitted-backup.patch` 保留。

---

## 13. 已知限制

- Qdrant 是外部基础设施，服务不托管其生命周期。
- Graph / KV / doc status 仍为本地文件；只有向量数据在 Qdrant。
- 单文档删除仍使用全 KB rebuild，未引入点级删除。
- 单进程 LifecycleTaskExecutor。
- SQLite 并发限制。
- MinerU CDN/网络依赖与 API Key 配额（kimi 免费额度耗尽时自动降级 qwen，真实 E2E 已验证降级路径）。
- Parent-Child A2/A3 尚待完成（不属于 Phase 3 验收项）。
- `ruff format --check` 未作为门禁；基线存在 41 个文件格式漂移。

---

## 14. 下一步

Phase 3 complete，按此前追加指令进入 **Phase 3A：MinerU Online 与 PyMuPDF 解析质量、切块与 RAG 检索效果对比**（范围与未执行原因见 [mineru-vs-pymupdf-evaluation.md](mineru-vs-pymupdf-evaluation.md)）。Rerank 不启动。
