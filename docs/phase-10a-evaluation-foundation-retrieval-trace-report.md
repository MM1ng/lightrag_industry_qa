# Phase 10A：Evaluation Foundation & Retrieval Trace 实施报告

日期：2026-08-03  
范围：Phase 10A（只建立评测基线与只读检索诊断能力）  
结论：`phase10a_approved=true`，`phase10b_started=false`，`phase10b_allowed=false`

## 1. 交付结论

Phase 10A 已完成并通过本地暂存验收：

- 普通问答契约未增加内部排名或 Score 字段；
- 评测器先调用真实普通问答 POST，再按 `request_id` 调用 admin-only Trace GET；
- Trace 来自普通问答使用的同一次 LightRAG `aquery_data` 查询，不存在第二套诊断检索链；
- 64 题均完成普通 POST + admin GET，Trace Completeness 为 64/64（100%）；
- LLM 缓存显式关闭；
- 未修改 Chunking、TopK、检索权重、Rerank、Prompt 或拒答阈值；
- 未创建 Tag、未重打 RC、未部署生产，也未进入 Phase 10B。

## 2. Retrieval Trace

新增接口：

`GET /v1/admin/diagnostics/requests/{request_id}/retrieval-trace`

local_staging 实测权限矩阵：

| 调用方式 | 结果 |
| --- | ---: |
| 缺少 Authorization | 401 |
| `SERVICE_API_KEY` | 403 |
| `ADMIN_API_KEY` | 200 |
| 普通问答使用 service | 200（64/64） |
| 普通问答使用 admin | 200 |

Trace 版本为 `phase10a-retrieval-trace-v1`，包含 request/trace/KB/Generation 标识、原始与规范化查询、检索配置、initial results、结构化 final selected chunks、引用/回答使用标志和各阶段延迟。

`initial_results` 严格表示 LightRAG `aquery_data` 返回的有序候选、位于本项目 Evidence Selection 和可选 Rerank 之前。上游未提供 Score 或子检索来源时返回：

- `initial_score=null`；
- `retrieval_source=lightrag_mix_unspecified`。

当前未启用 Rerank，64 条 Trace 均为：

- `rerank_applied=false`；
- `reranked_results=[]`；
- `reranked_rank=null`；
- `reranked_score=null`。

Trace 不包含 Authorization、Secret、系统/完整模型 Prompt、原始向量、完整文档正文、本地敏感路径或未脱敏 Endpoint。

## 3. 持久化与运行时容错

迁移 `c1d8f4a2b7e9` 新增不可变 `retrieval_traces` 表。Trace 默认 TTL 为 86400 秒，可配置范围为 60–604800 秒。

普通问答响应完成后才创建独立 SQLAlchemy Session 和事务写入 Trace。写入失败只回滚 Trace Session，不影响普通问答响应，并增加 `retrieval_trace_write_failure_total`；告警只记录 request/trace 标识和错误类型，不记录 Query Payload、凭证或异常堆栈。

运行时允许上述容错，但 Phase 10A 验收不允许缺失 Trace。本次真实基线不存在 Trace Missing。

## 4. 黄金集与来源冻结

黄金集共 64 题：60 道正例、4 道负例；development/validation/holdout 分布为 36/16/12。正例使用 `expected_evidence[]` 和 `expected_answer_points[]`，跨页与多证据题标注多个 Chunk；负例的证据和答案点为空且 `negative_reason` 必填。

数据集 SHA-256：

`22ae671b6579fa04e270e913c648fe359c622ccbd93cfefeb76334f6668c9fa3`

与被测正式 Generation 同源的两个固定 child artifact：

| 项目相对路径 | SHA-256 |
| --- | --- |
| `evaluation/experiments/parser_backend/P0/2196-ANSI-Manual-Chinese.pdf/child_chunks.jsonl` | `1c13df50995743f53ab0be41e66c5f6e2756b515f6a2aa7dd09ed2b491d3d8e0` |
| `evaluation/experiments/parser_backend/P0/t1739cn.pdf/child_chunks.jsonl` | `be38970c1538dc884e190f760de5058bf77657a7cdad38fded7cb5685433c620` |

实施中曾发现首轮黄金集误用 MinerU artifact，而暂存 Generation 是 PyMuPDF 冻结索引，导致 Chunk Recall 系统性为 0。该首轮结果未作为基线；修复来源后重新执行了完整 64 次普通 POST + admin GET，未通过补调诊断 GET 修饰失败记录。

## 5. 冻结指标与真实基线

所有 Rate 均保存 `numerator`、`denominator`、`value`；空分母返回 `null`。MRR 与检索 Recall 仅以 60 道 answerable 正例为问题分母，负样本不进入检索 Recall 分母。claim-level citation accuracy 当前不可用并明确返回 `value=null`。

| 指标 | numerator / denominator | value |
| --- | ---: | ---: |
| Chunk Recall@1 | 34 / 83 | 40.96% |
| Chunk Recall@3 | 46 / 83 | 55.42% |
| Chunk Recall@5 | 53 / 83 | 63.86% |
| Chunk Recall@10 | 56 / 83 | 67.47% |
| Chunk Recall@20 | 64 / 83 | 77.11% |
| Any Evidence Recall@5 | 49 / 60 | 81.67% |
| Complete Evidence Recall@20 | 48 / 60 | 80.00% |
| Document Recall@5 | 60 / 60 | 100.00% |
| Page Recall@5 | 49 / 60 | 81.67% |
| MRR | 39.0457 / 60 | 65.08% |
| graded nDCG@10 | 39.6966 / 60 | 66.16% |
| False Rejection Rate | 14 / 60 | 23.33% |
| Negative Rejection Rate | 4 / 4 | 100.00% |
| Unsupported Answer Rate | 3 / 46 | 6.52% |
| Question-level Citation Accuracy | 43 / 46 | 93.48% |
| Citation Trace Completeness | 64 / 64 | 100.00% |

延迟：检索 p50 2395.69 ms、p95 4619.69 ms；端到端 p50 5296 ms、p95 9488 ms。Rerank 未启用，因此 Rerank 延迟为 0。

这些数值是 Phase 10A 的诊断基线，不代表已进行质量调优。False Rejection Rate 23.33%、Chunk Recall@20 77.11% 和 Unsupported Answer Rate 6.52% 是 Phase 10B 可研究的问题，但本阶段未据此调整任何运行参数。

## 6. 验收证据

- `scripts/run_phase10a_baseline.py --verify-only`：`records=64 fixed20=20 trace_completeness=1.0`；
- 黄金集验证：`records=64 invalid=0`；
- 真实产物验收测试：3 passed；
- 全量 pytest：619 passed、12 skipped、0 failed；
- Ruff：All checks passed；
- Secret 扫描：API、UI、日志、JSONL/指标、数据库、源码和本报告的 `confirmed_secret_count=0`；
- API 双角色鉴权：管理诊断 401/403/200，普通问答 service/admin 均成功。

12 项 skip 均为既有显式 opt-in 的真实 MinerU、Qdrant E2E 或 Qdrant integration 测试。本轮已经另外执行真实 local_staging 64 题 HTTP 基线。最终 pytest 产生 1 条来自 FastAPI TestClient 依赖的 `StarletteDeprecationWarning`，未造成测试失败。

## 7. 独立提交

- `5f695ec`：捕获真实查询链 Retrieval Trace；
- `c8b078b`：不可变 Trace 持久化、迁移与 TTL；
- `66683af`：admin-only 诊断接口；
- `a7bde27`：64 题多证据黄金集；
- `1f1729c`：指标与确定性诊断；
- `98173cc`：普通 POST + admin GET 基线运行器；
- `3407928`：黄金证据与暂存 Generation 同源修复；
- `23fa900`：真实 local_staging 基线产物。

## 8. 停止边界

Phase 10A 到此停止。Phase 10B 尚未开始；holdout 集未用于调参。等待人工验收和下一步明确指令，不自动修改 Chunking、TopK、混合检索权重、Rerank、Prompt、拒答阈值或引用绑定策略。
