# Phase 11 实施报告：在线反馈与评测样本回流

## 1. 结论

已完成最小问答质量反馈闭环：用户问答后可提交“有帮助/没帮助”，系统按 `request_id` 关联持久化答案快照、引用和检索摘要，管理员可查询、筛选、评审、统计并导出 JSON/CSV，线上样本可人工选择后回流 Development Set 或 Error Set。

本阶段建设的是问答质量反馈闭环，不是运维监控系统。没有增加告警、SLO、灰度、A/B、用户账户、复杂 Dashboard、LangGraph、多跳检索或新检索算法；没有提交、部署、激活或切换 Generation，也没有修改 Golden、Validation、Holdout 数据。

## 2. 当前状态审计结果

现有系统已具备：

- `request_id`、`trace_id`、`generation_id`、`knowledge_base_id`。
- 用户问题、最终答案、回答状态/拒答状态。
- Retrieval Trace 中的检索 chunk 和引用信息。
- Legacy `/v1/query` 与知识库问答路径。

原有缺口：没有持久化的最终答案快照表、用户反馈接口、管理员反馈查询/统计/导出接口，也没有回答下方反馈控件。本阶段只做最小增量补齐这些缺口。

## 3. 修改文件清单

核心实现：

- `src/industrial_rag/db/models.py`：新增 `AnswerFeedbackRecord` 模型。
- `migrations/versions/e2f3a4b5c6d7_phase11_answer_feedback.py`：新增 `answer_feedback` 表及索引。
- `src/industrial_rag/repositories/answer_feedback_repository.py`：按 `request_id` 获取、幂等创建、更新、筛选、分页和导出查询。
- `src/industrial_rag/services/answer_feedback_service.py`：答案快照、反馈校验、指标、检索摘要清洗和人工评审字段。
- `src/industrial_rag/routers/feedback.py`：反馈、管理员查询、指标、评审、JSON/CSV 导出 API。
- `src/industrial_rag/api.py`：在实际业务回答路径排队 best-effort 快照写入，并注册路由。
- `src/industrial_rag/retrieval_trace.py`、`src/industrial_rag/lightrag_service.py`：只在回答期间提取受限 `content_excerpt`，不写入完整 Trace payload。
- `src/industrial_rag/errors.py`：增加反馈不存在错误码。

前端/客户端：

- `app/api_client.py`：增加反馈提交客户端方法。
- `app/feedback_ui.py`：反馈原因编码与中文标签。
- `app/streamlit_app.py`：回答下方增加“有帮助/没帮助”和负反馈原因控件；提交失败只提示，不影响答案显示。

测试：新增 `tests/test_phase11_*.py` 覆盖数据库、服务、API、客户端、Streamlit 控件、评审/导出及问答快照边界。

## 4. 数据库模型和迁移说明

新增表 `answer_feedback`，每个 `request_id` 唯一一行。保存字段包括：

`id`、`request_id`、`trace_id`、`generation_id`、`knowledge_base_id`、`question`、`answer`、`answer_status`、`feedback_type`、`feedback_reason`、`feedback_comment`、`citations`、`retrieved_chunks`、`created_at`、`updated_at`，以及后续人工评审字段 `answer_correct`、`answer_complete`、`citation_supported`、`refusal_appropriate`、`root_cause`、`review_notes`。

只为 `answered`、`insufficient_evidence`、`refused` 创建答案快照。参数校验失败、认证失败、内部异常且没有业务回答的请求不会进入快照表。

`retrieved_chunks` 只保留以下字段，最多 20 条，每条 `content_excerpt` 最多 240 字符：

`chunk_id`、`document_name`、`page`、`initial_rank`、`reranked_rank`、`score`、`content_excerpt`。

快照通过独立 best-effort 后台任务写入：失败只记录日志，不改变答案、引用、拒答或检索结果；不重新检索，也不阻塞或重构核心问答路径。长期指标只查询 `answer_feedback`，不依赖默认 24 小时 TTL 的 Retrieval Trace。

## 5. 反馈接口示例

```http
POST /api/feedback
Content-Type: application/json

{"request_id":"req-123","feedback_type":"helpful"}
```

```http
POST /api/feedback
Content-Type: application/json

{"request_id":"req-123","feedback_type":"unhelpful","feedback_reason":"citation_unsupported","feedback_comment":"引用没有覆盖结论"}
```

`unhelpful` 必须使用固定原因之一；`helpful` 原因可为空。客户端提交的答案、引用、Generation、知识库等字段不会被信任，后端始终按 `request_id` 读取快照。同一回答重复提交会更新原记录，不产生反馈历史。

同时提供等价的 `/v1/feedback` 路径。

## 6. 管理员查询、评审和导出示例

```http
GET /api/admin/feedback?page=1&page_size=20&feedback_type=unhelpful&feedback_reason=answer_incorrect
GET /api/admin/feedback/metrics?knowledge_base_id=kb-1
PATCH /api/admin/feedback/1/review
Content-Type: application/json

{"answer_correct":false,"answer_complete":"unknown","citation_supported":false,"refusal_appropriate":"not_applicable","root_cause":"retrieval_failure","review_notes":"检索未召回设备型号段落"}
```

查询支持分页、反馈类型/原因、知识库、Generation、答案状态和创建时间范围筛选；返回问题、答案、反馈、引用、检索摘要、request_id、generation_id、knowledge_base_id、时间等字段。管理员可使用 `/api/admin/feedback/export?format=json` 或 `format=csv` 导出当前筛选结果。接口只导出数据，不自动改写任何离线数据集。

## 7. 指标公式和分母

所有指标从持久化答案快照表计算：

- Feedback Coverage = 已提交反馈的答案数 / 可反馈答案快照数。
- Negative Feedback Rate（已反馈口径）= `unhelpful` 数 / 已提交反馈数。
- Negative Feedback Rate（可反馈口径）= `unhelpful` 数 / 可反馈答案快照数。
- Citation Presence Rate = 已回答且至少有一个有效引用的快照数 / `answered` 快照数。
- Empty Evidence Answer Rate = `answered` 且没有检索证据的快照数 / `answered` 快照数。
- Refusal Rate = `refused` 快照数 / 全部业务答案快照数。

零分母时对应 rate 返回 `null`，同时返回 numerator 和 denominator。这里的“有引用”只表示存在引用，不表示引用支持答案；拒答率只是分布指标，不等于合理拒答率。Recall@K 和 MRR@K 继续通过离线黄金集计算，线上反馈不用于替代它们。

## 8. 自动测试结果

- Phase 11 定向测试：`22 passed`。
- 全量测试：`774 passed, 12 skipped, 1 warning`。
- Ruff：`All checks passed`。
- Python 目标文件 `py_compile`：通过。
- `git diff --check`：通过；仅有 Git 的换行符提示。

全量测试中的 12 个 skip 是已有的 MinerU/Qdrant/外部依赖 opt-in 测试；唯一 warning 是现有 Starlette/httpx 测试客户端弃用提示，不是 Phase 11 失败。

## 9. 手工验证步骤

1. 执行数据库迁移。
2. 启动现有 FastAPI 和 Streamlit 服务，提交一条正常问答。
3. 在答案下点击“有帮助”，确认返回成功。
4. 点击“没帮助”，选择固定原因并提交；不选择原因时应被前端/后端拒绝。
5. 使用同一 `request_id` 重复提交，确认仍只有一条记录且反馈已更新。
6. 调用管理员接口筛选 `unhelpful`，检查答案、引用和检索摘要字段。
7. 调用 metrics 和 JSON/CSV export，检查分母、筛选结果及字段内容。
8. 用不存在的 `request_id`、伪造答案/引用/Generation 字段验证错误处理和服务端取值。
9. 用参数校验失败、认证失败或内部异常请求确认不会产生答案快照。
10. 将导出结果人工标注后，手动加入 Development Set 或 Error Set；不要改写 Golden、Validation、Holdout，也不要直接用于 Recall@K/MRR@K。

## 10. 已知限制

- best-effort 后台写入在进程异常退出前可能丢失快照，这是本阶段对问答可用性的明确取舍。
- 旧的、未经过本阶段快照逻辑的请求不会自动回填。
- 反馈没有历史版本、审核任务分配或复杂审计流程；同一 `request_id` 只保留最新反馈。
- Legacy `/v1/query` 某些路径没有完整的 Generation/知识库上下文时，对应字段保持为空，不由客户端补造。
- 导出结果需要管理员人工筛选、标注后回流；系统不会自动修改任何冻结评测集。

## 11. 评测边界

线上反馈用于发现问答问题、定位检索/引用/生成/拒答根因，并补充新的 Development Set 或 Error Set；不能替代固定离线回归测试集。Golden、Validation、Holdout 继续保持冻结，Recall@K 和 MRR@K 继续离线计算。
