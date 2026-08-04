# Phase 10B-3J-R1：Candidate Queryability Recovery & J0 Runtime Certification

## 审查结论

本轮已完成 Candidate Queryability Recovery 与 J0 运行认证，未运行 J1–J4、Validation 或 Holdout，未激活 Candidate，未改变 Active Generation。

J0 真实 Development 结果：

```json
{
  "attempted": 36,
  "completed": 36,
  "http_200": 36,
  "trace_http_200": 36,
  "generation_invalid_state": 0,
  "runtime_lineage_trace_version": 36,
  "provider_lineage_complete": 36,
  "coverage_trace_complete": 36,
  "grounding_removal_trace_complete": 36,
  "candidate_generation_correct": 36,
  "wrong_generation": 0,
  "backend_second_query_called": 0,
  "active_pointer_changed": false
}
```

证据：`evaluation/phase10b3j_r1/j0_development_results.jsonl`、`j0_development_summary.json`、`provider_lineage_matrix.jsonl`、`coverage_predicate_matrix.jsonl`、`grounding_removal_matrix.jsonl`。

## 409 根因与修复

根因不是 Candidate 产物损坏，也不是路由的 Active-only 回归，而是运行环境错误：

- 旧 409 进程读取 `runtime/phase10b3b/industrial_rag_staging.db`；
- 该库将 Candidate `5bca792c08fcf2f7b08cbaed09b6d525` 记录为 `failed`；
- `QueryApplicationService._query` 在 `generation.status in {failed, deleted}` 时返回 409；
- `runtime/phase10b3c/industrial_rag_candidate.db` 中同一 Candidate 为 `ready`；
- Candidate 的旧 Active 仍为 `a2d1c77ce08b414495e9d845cc42f799`。

修复仅将 API 运行时 `DATABASE_URL` 指向候选库，未修改数据库记录、Generation 状态或 Active 指针。根因链见 `generation_invalid_state_root_cause.json`；环境证明见 `runtime_identity.json`。

## Candidate 完整性

Candidate 状态为 `ready/queryable=true/active=false`，旧 Active 为 `active`。Candidate Qdrant 三个 Collection 均存在且为 green：chunks 453 points、entities 570 points、relationships 555 points。Context Registry：453 child chunks、447 parents、1355 relationships；Chunk ID 唯一；Embedding 1024 / Cosine；未发现跨 Generation 关系。详见 `candidate_state_audit.json` 和 `candidate_artifact_integrity.json`。

## 查询契约与 Smoke

普通 KB 查询继续返回旧 Active `a2d1c77ce08b414495e9d845cc42f799`；显式 Candidate 查询返回 200，Generation 为固定 Candidate，wrong-generation=0；Active 指针前后不变。契约结果见 `queryability_contract_results.json`。

5 题 J0 Smoke 全部通过（2 简单事实题、1 多答案题、1 历史 Grounding False Negative、1 历史 Generation Refusal）：

- 普通 POST：5/5 HTTP 200；
- admin Trace GET：5/5 HTTP 200；
- `phase10b3j-runtime-lineage-v2`：5/5；
- provider evidence/context SHA/coverage/grounding removal 字段：5/5；
- `backend_second_query_called=false`：5/5；
- 普通响应未泄露内部 Trace：5/5。

证据见 `j0_smoke_results.jsonl`。

## Runtime Lineage 与 Coverage

36/36 Trace 均记录 provider evidence IDs、primary/completed/supplemental IDs、context order、context SHA、数量、token estimate、截断标志及第二次查询标志。Supplemental IDs 为空，第二次查询为 false。

36/36 记录 coverage before、after parent/adjacent、selected/generated/grounding-retained coverage 与 unresolved requirements。36/36 记录 grounding retained/removed answer points、removal reasons 和 false-negative diagnostics。普通 QueryResponse 未包含这些内部审计字段。

## Instrumentation 非回归边界

J0 所有新增质量实验 Flag 均为 false：Claim pruning、coverage-aware selection、false-negative recovery、partial generation、support validator、structured generation、supplemental retrieval、cache 均关闭；Grounding Audit 仅作为诊断 instrumentation 开启。J0 不计算质量门禁指标，不与 R2 旧结果拼接。`instrumentation_non_regression.json` 记录了状态分布和结构性非回归结果。

## 15 例人工审查包

`evaluation/phase10b3j/manual_support_review_packet.jsonl` 包含 15 条基于 J0 真实 Trace 的 overcitation/semantic-support 审查案例，包含问题、答案、Claim、expected evidence、provider/citation evidence 正文和匹配字段；`human_decision` 全部为 null。`manual_support_review_decisions.json` 保持 `awaiting_human_review`，未自动填写决定。

## 提交链与验证

提交对象和父子关系见 `evaluation/phase10b3j_r1/commit_lineage.json`。本轮新增 runner 与 admin Trace 公共契约字段，并采用 TDD 验证：先得到失败的缺字段测试，再补齐响应模型，聚焦测试通过。

- R1 runner/Trace focused tests：2 passed；
- 完整 pytest 与 Ruff 应在本轮最终提交后重新执行并写入 delivery manifest；
- Secret scan：`confirmed_secret_count=0`；
- 未读取 Validation/Holdout；
- 未创建 Tag、未打包 RC、未部署生产、未进入 Phase 10C。

## 阶段状态

`phase10b3j_r1_approved=true`（J0 运行认证完成）；`j1_j4_allowed=false`（等待 15 例人工决定）；`phase10c_allowed=false`；`validation_run=false`；`holdout_run=false`；`candidate_activation_performed=false`；`production_deployment_performed=false`。

完成本报告后停止，等待人工填写 15 例 `human_decision`。
