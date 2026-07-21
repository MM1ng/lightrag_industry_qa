# EnergyOps Copilot 决策记录

> 状态：已确认，允许开发
>
> 版本：0.2
>
> 日期：2026-07-21

## 决策状态说明

- **Accepted**：访谈中已确认，实施不得自行改变。
- **Accepted with validation gate**：方向已确认，但必须先通过真实版本或接口验证。
- **Deferred**：明确不属于 MVP，不得提前实现。

## ADR-001：采用模块化单体业务后端

- 状态：Accepted
- 决策：FastAPI 是唯一业务后端，在一个部署单元内组织领域、工作流、工具、持久化和 API 模块。
- 原因：保留清晰模块边界和可测试性，同时避免微服务的网络、部署和一致性成本。
- 后果：模块依赖必须向领域层收敛；禁止把所有逻辑堆入路由或少数超大文件。
- 未采用：全部逻辑放在 Streamlit；按意图拆分多个微服务。

## ADR-002：LightRAG 使用独立 REST 服务

- 状态：Accepted（2026-07-21 已通过 Windows/Python 3.11 真实 REST Smoke）
- 决策：MVP 的真实 RAG 后端只支持 LightRAG Server REST API，不同时实现本地库运行模式。
- 锁定版本：`lightrag-hku[api]==1.5.4`；已通过 Python 3.11/Windows 安装、独立端口启动、认证、单/批量导入、track、paginated 和五种查询模式 Smoke。业务环境不安装或导入 LightRAG，服务使用独立 Conda 环境。
- 原因：更接近生产边界，可独立管理索引、健康状态、超时和扩展。
- 后果：FastAPI 只能通过 `LightRAGRestAdapter` 调用；业务代码不得直接 `import lightrag` 或修改其工作目录。
- 验证门：开发开始后先锁定当前版本，核验真实启动命令、健康检查、导入、查询模式和来源返回结构，再实现适配器。
- 测试替代：离线测试使用 `FakeRAGAdapter`。

## ADR-003：使用百炼北京地域的 OpenAI-compatible Provider

- 状态：Accepted with validation gate
- 决策：聊天模型为 `qwen3.7-plus`，通过 OpenAI-compatible 接口调用阿里云百炼华北 2（北京）地域。
- 地址：优先显式配置业务空间专属地址 `https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/compatible-mode/v1`；未配置 `LLM_BASE_URL` 时回退北京共享地址 `https://dashscope.aliyuncs.com/compatible-mode/v1`，不得拼造 WorkspaceId。
- 官方能力基线：模型表确认其支持文本、图像、视频和 Function Calling；MVP 只启用文本和工具调用，视觉输入保持 Deferred。结构化输出只按非思考模式 JSON Mode 设计，不声称支持严格 JSON Schema。
- 原因：使用统一的 OpenAI-compatible Provider 保持厂商隔离；MVP 所需的文本对话、JSON Mode 和 Function Calling 仍必须由当前账户的真实 Smoke Test 确认。
- 后果：领域和工作流层不依赖阿里云 SDK；模型响应先转换为内部模型。
- 验证门：使用现有 `DASHSCOPE_API_KEY` 执行最小 Chat、结构化输出和工具调用 Smoke Test。
- 密钥：只从环境变量读取，禁止回显、写入仓库或保存到数据库。

## ADR-004：Embedding 使用 `text-embedding-v4` 1024 维

- 状态：Accepted with validation gate
- 决策：使用百炼北京地域 `text-embedding-v4`，OpenAI-compatible 请求显式传入 `dimensions=1024`、`encoding_format="float"` 并验证 1024 维。
- 原因：百炼官方将其推荐用于纯文本搜索和 RAG；与聊天模型共享地域和认证体系。
- 后果：Embedding 模型、维度、分块配置或规范化规则改变时必须建立新索引命名空间并重新导入，不能复用旧向量索引。
- 验证门：最小 Embedding 调用必须断言返回向量长度为 1024；不得误用 DashScope 原生接口的单数 `dimension` 或未被兼容接口确认的参数。

## ADR-005：Rerank 不作为 MVP 硬依赖

- 状态：Accepted
- 决策：保留可选 `RERANK_MODEL` 配置，但 MVP 基线不要求额外重排序服务；不得让 Rerank 阻塞主演示。
- 原因：LightRAG 查询和证据验证已经构成核心链路，额外 `qwen3-rerank` 会增加成本、接口和评估复杂度。
- 后果：若后续启用，必须单独评估收益、成本和失败回退，且不能改变已有引用身份。

## ADR-006：MinerU 优先，PyMuPDF 允许降级

- 状态：Accepted
- 决策：统一 `DocumentParser` 接口下优先使用 MinerU；MinerU 不可用或解析失败时自动降级到 PyMuPDF。
- 原因：保留复杂版面解析上限，同时避免 MinerU 安装或模型资源阻塞 MVP。
- 后果：解析报告必须记录解析器、版本、失败原因、受影响页和表格限制；不得猜测解析失败内容。
- 当前事实：`energyops-copilot` 环境未安装 MinerU，已安装并验证 PyMuPDF 1.28.0；`auto` 对两份手册执行整文档降级，分别保留 55/62 个物理页状态并生成 53/65 个页内块。表格只保留提取文本和邻近上下文，报告显式标记结构未保留，不重建或猜测单元格。

## ADR-007：文档块和引用由业务后端验证

- 状态：Accepted
- 决策：文档块不跨物理 PDF 页；页码使用从 1 开始的物理页码。`chunk_id` 固定为 `<doc_id>:p<page>:c<ordinal>:<normalized_text_sha256_8>`，保证相同输入与配置下可复现。
- 原因：LightRAG 或模型返回的自然语言页码不能直接作为可信引用。
- 后果：SQLite 保存摄取清单、内容哈希、解析器版本、分块版本、Embedding 模型和维度；最终引用必须能在清单中反查。
- 兼容策略：若 LightRAG REST 不能可靠往返 metadata，将稳定标识嵌入导入文本，并由本地摄取清单反查。

## ADR-008：液压数据采用严格、确定性的周期处理

- 状态：Accepted
- 决策：用户可见 `cycle_id` 使用 1–2205 的 1 基编号；内部索引转换必须集中处理并测试。
- 每个 17 个传感器计算 10 个特征，共 170 个特征列，另含 `cycle_id` 和 5 个 `profile` 标签。
- 数学定义：
  - `mean`：算术平均值。
  - `std`：总体标准差，`ddof=0`。
  - `min`、`max`、`median`：周期内统计量。
  - `range`：`max - min`。
  - `first`、`last`：周期首尾值。
  - `trend`：`last - first`。
  - `slope`：以真实采样时间（秒）为自变量的一元最小二乘斜率，单位为传感器单位/秒。
- 缺失策略：不插值、不静默丢弃；发现非数值、非有限值、列数或周期不一致时处理失败并写明文件和周期。
- 稳态策略：保留 `stable_flag`；当值为 `1` 时，查询和诊断必须显示“可能未达到稳态”，不能将该周期当作稳定工况基准。

## ADR-009：UCI 数据和设备手册是不同来源

- 状态：Accepted
- 决策：UCI 数据只代表公开液压实验台；两份 PDF 是泵类说明书。不得声称这些传感器周期来自手册所述设备、真实电厂或当前企业。
- 原因：二者不存在已验证的一一设备映射。
- 后果：融合诊断必须标注文档证据和实验数据证据的来源差异；模拟关联仅用于演示，不得描述为现场故障定论。

## ADR-010：Streamlit 是薄演示客户端

- 状态：Accepted
- 决策：Streamlit 只调用 FastAPI，展示回答、引用、Trace、传感器摘要、风险和工单草稿。
- 原因：快速完成清晰演示，同时保持可替换的生产 API 边界。
- 后果：Streamlit 不直连百炼、LightRAG、SQLite 或本地处理文件；后端状态是审批和工单的唯一事实来源。

## ADR-011：确定性安全规则优先并默认安全失败

- 状态：Accepted
- 决策：高风险识别在输入预检查和最终输出审查两处执行。LLM 可以补充说明，但不能降低确定性规则的风险等级。
- 原因：安全判断不能依赖一次概率性模型输出。
- 后果：安全审查失败、超时或不确定时强制 `approval_required=true` 并进入不再调用模型/工具的确定性失败关闭出口；高风险非工单请求只创建风险审阅记录。只有显式 `work_order_draft` 且草稿有效、证据充分、安全结论允许审阅时才创建工单审阅记录。审批 API 只更新审阅状态，不触发任何设备动作。
- 审计：审批写入审批人标识、时间、前后状态和幂等键；MVP 使用最小服务令牌保护审批和导入接口。

## ADR-012：离线测试与真实 Smoke Test 分离

- 状态：Accepted
- 决策：`pytest`、工作流和 API 测试默认使用 Fake LLM、Fake RAG 和临时 SQLite；真实百炼/LightRAG 调用由显式 Smoke 命令执行。
- 原因：保证测试可重复、低成本且不依赖用户密钥或网络。
- 后果：Fake 通过不能替代真实验收；真实调用遵循“离线通过 → 最小调用 → 统计分块/Token → 完整导入”的阶段门。

## ADR-013：使用 Python 3.11 独立环境

- 状态：Accepted
- 决策：不使用当前 PATH 默认的 Python 3.12.8，使用本机可用的 Python 3.11 创建项目专用虚拟环境。
- 原因：符合项目约束，并降低 LightRAG、MinerU 和数据依赖的兼容风险。
- 后果：README 所有 Windows 命令必须使用已验证的 3.11 环境路径；避免混用 `python` 和 `py` 指向的不同解释器。

## ADR-014：SQLite 只支撑单节点 MVP

- 状态：Accepted
- 决策：SQLite 保存会话摘要、Trace、摄取清单、工单和审批；周期特征使用 Parquet/CSV。
- 原因：单机 MVP 无需引入 PostgreSQL。
- 后果：启用 WAL 和 busy timeout，FastAPI 只运行一个写入 worker；需要多实例或高并发写入时先迁移 PostgreSQL。

## ADR-015：摄取是受保护、幂等的后台作业

- 状态：Accepted
- 决策：`POST /api/v1/ingest` 是唯一远程摄取入口，只接受已登记的文档 ID，不接受任意 URL 或任意本机路径；管理 CLI 必须调用该 API，或在同一进程复用相同摄取应用服务，禁止直连 LightRAG。接口返回作业 ID，由单进程持久化作业消费者处理。
- 原因：避免长 HTTP 超时、SSRF、路径穿越和重复收费式导入。
- 后果：MVP 不引入 Redis/Celery；摄取作业、内容哈希、状态、有限尝试次数和脱敏错误保存在 SQLite。作业使用 `PENDING | RUNNING | SUCCEEDED | FAILED | RECONCILE_REQUIRED` 状态与 `lease_expires_at` 租约；启动时恢复 `PENDING` 并回收租约过期的 `RUNNING`。
- 远端一致性：以来源哈希、解析器/分块版本、Embedding 模型/维度、命名空间和块内容哈希生成确定性业务指纹，将其编码到全局唯一的 `file_source` basename 和 `ENERGYOPS_INGEST_MANIFEST` 文本头并由本地清单反查。LightRAG 1.5.4 不接受客户端 ID、没有幂等 upsert 或按路径直接 GET；异步提交必须保存 `track_id` 并确认 track 完成后才能标记 `SUCCEEDED`。运行中通过独立心跳续租；若远端可能已成功而本地未提交，必须同时验证 track、全部文档分页、query references 和文本头 marker，无法判断时进入 `RECONCILE_REQUIRED` 且禁止自动重放。该状态可由对账命令再次探测，MVP 不宣称 SQLite 与 LightRAG 之间具备分布式“恰好一次”事务。

## ADR-016：Agent Trace 不包含隐藏推理

- 状态：Accepted
- 决策：只展示节点、工具、耗时、成功/失败、证据数量、有限参数摘要和错误码。
- 原因：提供可观察性，同时避免泄漏密钥、完整 Prompt、原始工业数据或隐藏思维链。

## ADR-017：验收门槛固定

- 状态：Accepted
- 决策：至少 30 条人工标注黄金问题；意图路由准确率至少 90%；适用问题的引用存在率为 100%，手册、传感器和模拟案例三类引用按各自 schema 分别达到字段完整率与可解析率 100%；无答案正确拒答率和高风险拦截率均为 100%；Fake 依赖工具调用成功率至少 95%。
- 后果：评估必须显示样本量、公式和失败样例；不得把 MVP 小样本结果宣传为工业认证准确率。

## ADR-018：MVP 暂不增加独立 BM25

- 状态：Accepted
- 决策：MVP 不建设独立 BM25、Elasticsearch 或 OpenSearch 检索链路，先以锁定版本实际支持的 LightRAG 查询模式建立基线。
- 原因：当前语料只有两份手册，新增中文分词、双索引同步、结果融合和权重调参会扩大首版范围。
- 演进门：若安全/规程问题目标块的 `Recall@5` 未达到 100%、普通手册检索 `Recall@5` 低于 95%，或型号/零件号/数字参数成为主要漏检类型，再在 `RAGAdapter` 后增加 BM25 候选并使用 RRF 融合；引用反查和安全门禁保持不变。

## Deferred：明确推迟的事项

- React/Vue 正式前端。
- PostgreSQL、Redis、Celery、Kafka。
- Kubernetes、服务网格和多租户权限系统。
- 真实 DCS/PLC/设备控制。
- 视觉解析作为 MVP 必经链路。
- 大规模训练、音频诊断和计算机视觉产品化。

## 开发前验证门

设计确认后，实施必须按顺序关闭以下风险：

1. 创建并确认 Python 3.11 环境，避免解释器混用。
2. 检查并锁定 LightRAG 当前版本；验证真实 Server REST 契约和查询模式。
3. 验证 `qwen3.7-plus` 的北京地域 Chat、结构化输出和 Function Calling。
4. 验证 `text-embedding-v4` 返回 1024 维。
5. 检查 Docker 服务是否可启动；如果官方 LightRAG 容器路径不可用，使用独立 Python Server 进程而不改变 REST 边界。
6. 解析前记录原始数据和 PDF 指纹；处理后复核指纹未变化。
7. 验证两份 PDF 的实际页数、文本可提取性和引用往返能力。
8. 完整导入前统计块数、Token 估算和预计外部调用量。

这些是实施验证门，不是待用户决定的开放问题。

## 官方资料基线

以下链接均为阿里云百炼官方文档，已于 2026-07-21 核查；开发时仍需重新确认接口更新时间并运行真实 Smoke：

- [Base URL 总览](https://help.aliyun.com/zh/model-studio/base-url)
- [子业务空间的模型调用](https://help.aliyun.com/zh/model-studio/model-calling-in-sub-workspace)
- [OpenAI 兼容 Chat API](https://help.aliyun.com/zh/model-studio/qwen-api-via-openai-chat-completions)
- [模型列表](https://help.aliyun.com/zh/model-studio/models)
- [Function Calling](https://help.aliyun.com/zh/model-studio/qwen-function-calling)
- [结构化输出](https://help.aliyun.com/zh/model-studio/qwen-structured-output)
- [OpenAI Embedding 接口兼容](https://help.aliyun.com/zh/model-studio/embedding-interfaces-compatible-with-openai)
- [向量模型维度说明](https://help.aliyun.com/zh/model-studio/embedding)
