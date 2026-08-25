# 阶段 3：Qdrant 分代存储生命周期 — 当前进度报告

**报告日期**: 2026-07-31  
**分支**: `codex/knowledge-qa-platform-design`  
**基线提交**: `64dcee4 feat: Phase 1A-2D complete - KB lifecycle, MinerU API, Parent-Child chunking, task executor, Alembic`  
**当前状态**: 核心实现已进入工作区，尚未完成受控环境验证；不得视为已验收或可发布。

---

## 1. 执行摘要

阶段 3 的目标是为工业知识问答服务增加 **每知识库、每索引 generation 物理隔离** 的 Qdrant 向量存储能力，并保留 NanoVectorDB 作为默认和可回滚后端。

当前工作区已完成主要实现骨架与大量对应测试资产，覆盖 Qdrant collection 命名、generation 元数据、确定性输入指纹、运行时选择、后端切换生命周期任务、精确清理及启动恢复等方向。

但本次全量测试在收集阶段即因执行环境错误而中止：实际运行的是全局 Anaconda/Python 环境，而非计划要求的 `industrial-rag` Conda 环境。因此当前尚无可信的全量单元测试、迁移验证或 Qdrant 集成测试通过结论。

> **验收结论：已实现，待验证。**
>
> 当前不能将阶段 3 标记为完成；下一步应首先恢复正确的 Python 3.11 Conda 环境，再依次完成测试、迁移与 Qdrant 实例验证。

---

## 2. 范围与设计约束

已记录的设计决策见 [qdrant-storage-design.md](qdrant-storage-design.md)，实施拆分见 [2026-07-31-qdrant-generation-lifecycle.md](superpowers/plans/2026-07-31-qdrant-generation-lifecycle.md)。本阶段遵循以下关键约束：

1. NanoVectorDB 仍是默认后端；既有 Nano workspace 不会被重命名、覆盖或删除。
2. 一个知识库的每个 Qdrant generation 使用独立物理 collection，而不是共享 collection 中的逻辑 namespace。
3. 每个 Qdrant generation 固定拥有 `chunks`、`entities` 和 `relationships` 三个 collection。
4. 后端迁移从现有 `ChildChunk` 产物执行影子重建，不重新解析 PDF，也不从 Nano 文件进行有损导入。
5. 新 generation 仅在本地索引与 Qdrant collection 验证成功后才允许激活；失败时不得影响当前可查询 generation。
6. 删除仅操作持久化 generation 记录精确解析得到的 collection，禁止通过列举、模糊匹配或批量删除清理 Qdrant。
7. Nano 回滚必须比对当前输入 fingerprint；若输入已变化或 Nano generation 不健康，必须拒绝回滚并保持 Qdrant 激活。
8. 本阶段止于存储与生命周期改造，**不启动 rerank 工作**。

---

## 3. 已实现能力（待验证）

| 能力域 | 当前实现状态 | 说明 |
|---|---|---|
| Qdrant 客户端依赖 | 已加入 | 项目依赖增加 `qdrant-client>=1.18,<2`。 |
| 后端配置 | 已实现 | 支持 `VECTOR_BACKEND`、Qdrant URL/API Key、collection 前缀与 generation/KB 运行时参数。 |
| 配置校验 | 已实现 | 当 `VECTOR_BACKEND=qdrant` 且缺少 `QDRANT_URL` 时拒绝启动。 |
| collection 名称解析 | 已实现 | 集中 resolver 以经过校验的 KB ID、generation、前缀和白名单 namespace 生成物理 collection 名称。 |
| 物理隔离存储适配 | 已实现 | 使用项目自定义 `PhysicalQdrantVectorDBStorage`，避免内建 LightRAG Qdrant 存储的共享 collection 行为。 |
| generation 数据模型 | 已实现 | 增加向量索引 generation 记录、状态、激活 generation 关联及数据库约束。 |
| 数据库迁移 | 已创建 | 已新增 generation 与 Qdrant 元数据的 Alembic migration；尚未完成升级/降级实测。 |
| generation repository | 已实现 | 提供创建影子 generation、查询活跃 generation、激活、失败标记和清理候选查询等持久化能力。 |
| 输入指纹 | 已实现 | 对文档、ChildChunk、embedding 与分块配置计算确定性 fingerprint，避免输入顺序影响结果。 |
| generation workspace 布局 | 已实现 | 扩展了 Nano/Qdrant 的 generation 级 workspace 路径。 |
| 影子索引与激活流程 | 已改造 | IndexService、LightRAG 运行时和生命周期 handler 已朝 supplied generation 的影子构建与验证方向改造。 |
| 后端切换 API | 已新增 | 已改造 router/schema/service/handler，支持创建向量后端切换任务。 |
| 运行时 generation 感知 | 已改造 | 运行时缓存和查询路径已按 KB、backend、generation、workspace 与 embedding 配置区分。 |
| 精确清理 | 已改造 | KB 删除和 generation 失败流程纳入精确 collection 与 generation workspace 清理。 |
| 启动恢复 | 已改造 | lifecycle executor 已增加 `running` 任务恢复为重试状态的处理方向。 |

**状态定义**：本表“已实现”仅表示相应代码位于当前未提交工作区，不表示测试或生产环境已验证通过。

---

## 4. 已变更资产

### 4.1 新增文档与设计资产

- [qdrant-storage-design.md](qdrant-storage-design.md) — Qdrant 物理隔离、迁移/回滚、故障不提升等设计决策。
- [2026-07-31-qdrant-generation-lifecycle.md](superpowers/plans/2026-07-31-qdrant-generation-lifecycle.md) — 阶段 3 实施计划与验证门槛。

### 4.2 新增数据库、存储与服务代码

- [4f2c7d9a8b1e_add_qdrant_vector_metadata.py](../migrations/versions/4f2c7d9a8b1e_add_qdrant_vector_metadata.py)
- [9e6f0a2c3b4d_add_vector_index_generations.py](../migrations/versions/9e6f0a2c3b4d_add_vector_index_generations.py)
- [kb_runtime_settings.py](../src/industrial_rag/kb_runtime_settings.py)
- [physical_qdrant_storage.py](../src/industrial_rag/physical_qdrant_storage.py)
- [vector_index_generation_repository.py](../src/industrial_rag/repositories/vector_index_generation_repository.py)
- [generation_fingerprint_service.py](../src/industrial_rag/services/generation_fingerprint_service.py)
- [qdrant_collection_service.py](../src/industrial_rag/services/qdrant_collection_service.py)
- [vector_collections.py](../src/industrial_rag/vector_collections.py)

### 4.3 已修改的核心实现区域

- 配置及依赖：[config.py](../src/industrial_rag/config.py)、[pyproject.toml](../pyproject.toml)、[requirements.txt](../requirements.txt)
- 数据库与迁移：[models.py](../src/industrial_rag/db/models.py)、[env.py](../migrations/env.py)
- 索引与 LightRAG：[index_service.py](../src/industrial_rag/services/index_service.py)、[lightrag_service.py](../src/industrial_rag/lightrag_service.py)
- KB / 任务生命周期：[knowledge_base_service.py](../src/industrial_rag/services/knowledge_base_service.py)、[handler_impls.py](../src/industrial_rag/services/handler_impls.py)、[lifecycle_task_executor.py](../src/industrial_rag/services/lifecycle_task_executor.py)、[task_repository.py](../src/industrial_rag/repositories/task_repository.py)
- API 与运行时：[api.py](../src/industrial_rag/api.py)、[runtime_manager.py](../src/industrial_rag/services/runtime_manager.py)、[knowledge_bases.py](../src/industrial_rag/routers/knowledge_bases.py)、[schemas.py](../src/industrial_rag/routers/schemas.py)
- 存储清理与目录布局：[cleanup_service.py](../src/industrial_rag/services/cleanup_service.py)、[storage_layout.py](../src/industrial_rag/storage_layout.py)

### 4.4 新增或扩展的测试资产

- [test_generation_fingerprint_service.py](../tests/test_generation_fingerprint_service.py)
- [test_kb_runtime_settings.py](../tests/test_kb_runtime_settings.py)
- [test_lifecycle_restart_recovery.py](../tests/test_lifecycle_restart_recovery.py)
- [test_runtime_manager_generations.py](../tests/test_runtime_manager_generations.py)
- [test_storage_layout_generations.py](../tests/test_storage_layout_generations.py)
- [test_vector_backend_api.py](../tests/test_vector_backend_api.py)
- [test_vector_collections.py](../tests/test_vector_collections.py)
- [test_vector_index_generation_models.py](../tests/test_vector_index_generation_models.py)
- [test_vector_index_generation_repository.py](../tests/test_vector_index_generation_repository.py)
- 已同步更新：[test_knowledge_base_api.py](../tests/test_knowledge_base_api.py)、[test_lightrag_service.py](../tests/test_lightrag_service.py)

---

## 5. 本次验证证据

### 5.1 已执行命令

```text
pytest -q
```

### 5.2 实际结果

**失败：测试收集阶段中止，退出码 4。** 测试尚未进入断言执行阶段。

观察到的主要环境错误：

```text
ModuleNotFoundError: No module named 'pymupdf'
ModuleNotFoundError: No module named 'app'
ERROR: Unknown config option: asyncio_mode
```

收集结果摘要：

```text
23 errors during collection
2 warnings
2.76s
```

### 5.3 结果解释

该失败说明当前调用 `pytest` 的解释器不是项目要求的 `industrial-rag` Conda 环境：

- 环境缺少项目所需的 `pymupdf` 依赖；
- 环境未加载预期的应用模块路径；
- 环境缺少支持 `asyncio_mode` 配置的 pytest 插件或版本组合。

因此，以上结果**不能用于判断阶段 3 代码逻辑正确或错误**。在正确环境中重新执行前，以下结论均不可作出：

- 新增单元测试是否通过；
- 既有回归测试是否通过；
- Alembic migration 是否可升级/降级；
- Qdrant collection 生命周期是否按设计运行。

---

## 6. 尚未完成的验收门槛

| 优先级 | 验收项 | 通过标准 |
|---|---|---|
| P0 | 恢复受控 Python 环境 | 明确使用 Python 3.11 的 `industrial-rag` Conda 环境，且依赖、项目路径和 pytest async 插件可用。 |
| P0 | 测试收集基线 | `python -m pytest --collect-only -q` 成功完成。 |
| P0 | 全量回归 | 受控环境下 `python -m pytest -q` 通过。 |
| P0 | 静态检查 | `ruff check .` 通过，或记录明确且被接受的存量豁免。 |
| P0 | 数据库迁移 | 在一次性 SQLite 数据库上验证两份 Alembic migration 的 upgrade 与 downgrade。 |
| P1 | Qdrant 单元验证 | 使用 fake async Qdrant client 覆盖 collection 解析、维度/点数检查、精确清理和失败不提升。 |
| P1 | Qdrant 实例集成验证 | 以独立测试前缀、随机 KB/generation 验证持久化、重启、跨 KB 隔离、删除和故障传播。 |
| P1 | Nano 回滚验证 | 输入 fingerprint 变化或 Nano generation 不健康时返回冲突，保持 Qdrant generation 激活。 |
| P2 | 最终报告 | 创建最终阶段报告，记录真实命令、版本、测试结果、局限与明确的“未启动 rerank”声明。 |

---

## 7. 已知风险与注意事项

| 风险 / 注意事项 | 影响 | 建议处理 |
|---|---|---|
| 当前改动均未提交 | 代码仍可能丢失、冲突或包含未验证内容。 | 在验证通过后审查差异、拆分合理提交并保留测试证据。 |
| 执行环境错误 | 现有测试失败无法反映实现质量，阻塞验收。 | 优先定位并激活 `industrial-rag` Conda 环境，不在全局 Anaconda 环境中继续判断结果。 |
| 缺少 Qdrant opt-in 集成验证证据 | 物理隔离和清理逻辑尚未对真实 Qdrant 服务证明。 | 增加/运行受 `IRA_QDRANT_INTEGRATION=1` 保护的集成测试，只使用专用安全前缀。 |
| Windows LF → CRLF 警告 | 可能引入无业务意义的格式 diff。 | 提交前检查 `.gitattributes` 与团队换行策略；避免仅格式化造成的混杂改动。 |
| 实施计划 checkbox 未回填 | 计划显示与实际代码状态不一致，易误导后续执行。 | 在每项受控验证完成后再如实勾选；未验证项保持未完成。 |
| 需求范围膨胀 | 可能提前进入 rerank 或点级删除等超出阶段 3 的内容。 | 保持“全 KB 影子重建、精确 generation 清理、暂不 rerank”的范围边界。 |

---

## 8. 建议的后续执行顺序

1. 列出并激活 `industrial-rag` Conda 环境，确认其 Python 版本与项目依赖可用。
2. 在该解释器下执行 `python -m pytest --collect-only -q`，消除收集错误。
3. 在该环境执行全量 `python -m pytest -q` 及 `ruff check .`，修复任何真实失败。
4. 使用临时 SQLite 数据库验证 Alembic upgrade/downgrade，并记录命令与结果。
5. 启动独立 Qdrant 测试实例，以隔离测试前缀运行 opt-in 集成验证。
6. 复核 shadow generation 的非提升失败、精确 collection 清理、重启选择与 Nano fingerprint 回滚拒绝场景。
7. 更新实施计划 checkbox，编写最终阶段报告，再进入提交与代码审查。

---

## 9. 交付状态

| 项目 | 当前结论 |
|---|---|
| 架构与设计记录 | 已具备 |
| 核心代码实现 | 已进入未提交工作区 |
| 单元测试资产 | 已新增，待在受控环境执行 |
| 全量回归结果 | 未获得；当前环境收集失败 |
| Migration 验证 | 未完成 |
| 真实 Qdrant 集成验证 | 未完成 |
| Phase 3 最终验收 | 未完成 |
| Rerank | 未开始，且不属于本阶段范围 |

**最终状态：阶段 3 处于“实现进行中、验证受环境阻塞”的状态。**
