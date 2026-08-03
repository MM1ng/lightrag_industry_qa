# Phase 10B-3C：Clean Staging Generation & Provider Recovery Report

## 结论

审查发现原报告错误地声称“当前 API 只暴露 Active Generation 查询路径”。实际代码已经存在 admin-only 显式 Generation 查询接口：

`POST /v1/knowledge-bases/{kb_id}/generations/{generation_id}/query`

本轮已正确核对并使用该接口完成 Candidate smoke 和 52 题显式 Generation 验收。接口不修改 Active 指针。Candidate 已正式注册、建立真实 Qdrant 索引并完成 development/validation 查询；由于质量指标未达到全部门禁，Candidate 保持未激活。

## 标识与版本

- 分支：`codex/knowledge-qa-platform-design`
- `code_under_test_commit`：`4214998`
- `report_commit`：`dace692`（本报告修订版的 docs 提交；最终 Git HEAD 另见交付记录）
- KB：`8fce4626859d44abb70a9ae5b0372cea`
- old_active_generation_id：`a2d1c77ce08b414495e9d845cc42f799`
- candidate_generation_id（数据库 `vector_index_generations.id`）：`5bca792c08fcf2f7b08cbaed09b6d525`
- candidate_generation_name（数据库 `vector_index_generations.generation`）：`g10b3c20260803`
- candidate_qdrant_collection：`ira_p3ar_4ac7a596_kb_8fce4626859d44abb70a9ae5b0372cea_g10b3c20260803_chunks`
- Candidate workspace：`runtime/phase10b3c/kb_data/8fce4626859d44abb70a9ae5b0372cea/g10b3c20260803/workspace`

## API 路由与权限验证

OpenAPI 已确认该路由为 POST，响应 schema 为 `QueryResponse`。实际验证结果：无凭证 401，SERVICE 403，ADMIN 合法 Candidate 200，不存在 Generation 404，其他 KB 404。Candidate 查询返回的 `generation_id`、Trace、Citation 和 Evidence 均为数据库 Candidate ID。

相关产物：

- `evaluation/phase10b3c/candidate_query_route_check.json`
- `evaluation/phase10b3c/candidate_identity.json`
- `evaluation/phase10b3c/candidate_registration_check.json`

## Candidate 注册与索引

Candidate 使用现有 `VectorIndexGenerationRepository` 注册，状态为 `ready`，workspace 位于稳定 runtime 目录，backend 为 qdrant，属于当前 KB，且 `active=false`。Candidate Qdrant chunks collection 存在 453 个 Point，向量维度 1024，距离为 Cosine，Embedding 模型为 `text-embedding-v4`。旧 Active collection 查询前后保持 453 个 Point，未向旧 Collection 写入。

相关产物：

- `evaluation/phase10b3c/candidate_vector_index_check.json`
- `evaluation/phase10b3c/context_registry_integrity.json`
- `evaluation/phase10b3c/parser_build_manifest.json`
- `evaluation/phase10b3c/context_registry_manifest.json`

旧 Active child JSONL 的 453 条记录中，原有 9 组、70 个重复实例；Candidate 通过包含文档、位置、分组序号和规范化内容的确定性 ID 修复为 453 个唯一 ID。表格元数据不存在，因此 `table_supported=false`，没有伪造 Table Completion。

## Candidate Smoke

通过显式 Generation 路由执行 7 个非黄金 Smoke 场景：普通可回答、无依据、partial、Adjacent、Parent、Multi-evidence 和安全问题。7/7 HTTP 200，7/7 Trace 200，响应/Trace Generation 均为 Candidate，wrong-generation citation 为 0。Table 场景按不支持处理。

详见 `evaluation/phase10b3c/candidate_smoke_results.jsonl` 和汇总 JSON。

## Development + Validation（52 题）

仅执行 development 36 题和 validation 16 题，未执行 Holdout，未修改 Golden Set。每题均通过 ADMIN Candidate Query → request_id → ADMIN Trace GET 流程完成，52/52 Trace 可读取。

使用冻结的 Golden evidence sidecar 将旧 evidence identity 映射到 Candidate Chunk ID；原始黄金集未改写。真实结果：

- Chunk Recall@20：63/72 = 87.5%
- Any Evidence Recall@20：50/50 = 100%
- Page Recall@20：50/50 = 100%
- MRR：0.6994
- False Rejection Rate：10/50 = 20%
- Negative Rejection Rate：2/2 = 100%
- Retrieval Trace Completeness：52/52 = 100%
- Claim-Citation Exact Mapping：52/52 = 100%
- Evidence Panel Completeness：52/52 = 100%
- Unsupported Answer Rate：1/1 = 100%（未通过）
- Question-level Citation Accuracy：1/1 = 100%（当前正例分母仅 1）
- Table trigger rate：unsupported，numerator/denominator/value 均为 null
- 端到端延迟：p50 约 2671ms，p95 约 6861ms

测试收尾：`pytest --collect-only` 收集 682 项；全量 pytest 为 670 passed、12 skipped、1 warning；`ruff check .` 通过。

指标原始数据保存在 `evaluation/phase10b3a/final_metrics.json`，逐题数据保存在 `development_results.jsonl` 和 `validation_results.jsonl`。

## 配置与安全

固定配置为 naive、TopK 12、chunk TopK 20、normalization/grounding 开启、Rerank 关闭、cache 关闭、fallback 关闭，Provider 固定 `qwen-plus-2025-07-28`。Secret scan confirmed_secret_count=0；密钥未进入 Candidate、Trace、响应、日志或报告。

## 阶段状态

```json
{
  "phase10b3c_data_rebuild_approved": true,
  "phase10b3c_provider_recovery_approved": true,
  "phase10b3c_candidate_smoke_approved": true,
  "phase10b3c_approved": false,
  "phase10b3a_approved": false,
  "phase10c_allowed": false,
  "production_deployment_performed": false
}
```

Candidate 不因已产生 Embedding 成本而激活。下一步需针对 False Rejection、Chunk Recall 和 Unsupported Answer 继续修复并重新验收；本阶段到此停止，不进入 Phase 10C，不创建 Tag，不重新打包 RC，不部署生产。
