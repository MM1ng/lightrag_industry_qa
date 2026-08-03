# Phase 10B-3C：Clean Staging Generation & Provider Recovery Report

## 结论

本阶段完成了旧 Active Generation 保护、重复 Chunk ID 审计、确定性 ID 修复、隔离 Candidate Generation 构建、上下文注册表完整性门禁、Golden evidence sidecar、Provider preflight 和 staging 运行验证。Candidate 未激活，未修改旧 Generation、Qdrant Collection 或 Golden Set；因此本阶段不宣称通过 Phase 10B-3A 质量门禁，也不进入 Phase 10C。

## 版本与范围

- 分支：`codex/knowledge-qa-platform-design`
- 实施代码提交：`0036016`
- Candidate：`g10b3c20260803`
- KB：`8fce4626859d44abb70a9ae5b0372cea`
- 旧 Active Generation：保持不变
- Holdout：未逐题读取、未运行
- Golden Set：未修改；仅生成只读 sidecar 映射
- Tag、RC 重打包、生产部署：均未执行

## 数据与 ID 审计

旧 Active child JSONL 共 453 条、383 个唯一 ID，发现 9 组、70 个重复实例。重复均为“相同内容位于不同位置”而非跨文档或同 ID 不同内容。根因是旧 child ID 只使用 parent ID 的短后缀，缺少完整 parent 位置身份。

Candidate 使用 `document_id + page/order/group ordinal + normalized content` 的确定性摘要生成 Parent/Child ID。Candidate 注册表共 453 个 Child、453 个唯一 ID、447 个 Parent、1355 条关系；Parent 链接 453/453，有效前后关系 451/451，无断链、跨文档、跨 Generation、自环或环路。表格元数据不存在，因此 `table_supported=false`，未猜测表头。

证据与清单：

- `evaluation/phase10b3c/duplicate_chunk_audit.json`
- `evaluation/phase10b3c/duplicate_chunk_groups.jsonl`
- `evaluation/phase10b3c/context_registry_integrity.json`
- `evaluation/phase10b3c/parser_build_manifest.json`
- `evaluation/phase10b3c/context_registry_manifest.json`
- `evaluation/phase10b3c/golden_evidence_mapping_g10b3c20260803.json`

Golden sidecar 共映射 83 条 evidence，未修改原始黄金集，且标记 `used_for_tuning=false`。

## Provider 与 staging 验证

Provider preflight 结果为：固定回答模型 `qwen-plus-2025-07-28` 可用，Embedding `text-embedding-v4` 可用；此前不可用的模型不再作为 fallback。结果只记录状态码、延迟和可用性，不记录响应体或密钥：`evaluation/phase10b3c/provider_preflight.json`。

恢复现有 staging Qdrant 后，服务在 `local_staging` 启动。普通 KB-scoped 查询使用 SERVICE 身份成功返回 `partial_answer`、`request_id`、`generation_id` 和结构化证据；ADMIN 诊断 GET 返回 200 且 trace 可读取。该运行验证使用的是旧 Active Generation，用于证明 Provider/服务恢复，不等同于 Candidate 已上线。

Candidate smoke 明确记录为 blocked：当前 API 只暴露 Active Generation 查询路径，没有在不切换 Active 指针的前提下执行 Candidate 查询的接口。因此 Candidate 保持隔离，`evaluation/phase10b3c/candidate_activation.json` 中 `activated=false`，`candidate_smoke_results.json` 中 `query_count=0`；没有伪造 smoke 结果。

## 配置冻结

`evaluation/phase10b3c/runtime_config_proof.json` 固定记录：naive、TopK 12、chunk TopK 20、normalization/grounding 开启、LLM cache 关闭、Rerank 关闭、fallback 关闭。密钥只从环境变量读取，未进入 Candidate、评估 JSON、日志或响应。`secret_scan.json` 的 `confirmed_secret_count=0`。

## 测试

- 全量 pytest：669 passed，12 skipped，1 warning。
- 结构化 Chunker 定向测试：24 passed。
- Ruff：新增和修改 Python 文件检查通过。

跳过项均为显式 opt-in 的真实 MinerU/Qdrant/DashScope 集成测试；本阶段没有以跳过项冒充 Candidate E2E 通过。

## 阶段状态

- `phase10b3c_approved`: false（Candidate smoke/激活门禁未完成）
- `phase10c_allowed`: false
- `production_deployment_performed`: false

下一步需要先提供或实现受控的 Candidate 查询执行入口，在不修改旧 Active 指针的条件下完成 Candidate smoke、52 题 development/validation 验收，然后再由人工审查决定是否进入后续阶段。
