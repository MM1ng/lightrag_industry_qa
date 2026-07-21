# EnergyOps Copilot 项目规格

> 状态：已确认，允许开发
>
> 版本：0.2
>
> 日期：2026-07-21

## 1. 产品定义

EnergyOps Copilot 是面向能源企业、电厂和工业场站运维场景的工业知识决策 Agent。MVP 聚焦离心泵及其辅助液压系统，将设备手册证据、公开液压状态监测数据和模拟业务数据组合为可追溯的知识问答、辅助诊断与工单草稿能力。

系统只能提供辅助分析和辅助决策，不连接或控制 DCS、PLC、阀门、电机及其他真实设备，不替代现场规程、持证人员判断或正式操作票。

## 2. 目标用户

- 运行人员：查询设备知识、运行规程、传感器周期和报警含义。
- 检修人员：查询维修前安全要求、候选原因、检查顺序和手册依据。
- 设备管理人员：查看诊断记录并生成结构化检修工单草稿。
- 项目演示者：通过 Streamlit 展示 Agent 路由、工具调用、证据融合和安全审查过程。

## 3. MVP 目标

MVP 必须打通以下端到端链路：

```text
用户问题或设备异常
→ 意图识别与实体提取
→ 文档检索和/或传感器工具调用
→ 融合文档、数据与模拟案例证据
→ 生成知识回答或候选故障分析
→ 显示可核验引用和 Agent Trace
→ 执行工业安全审查
→ 必要时生成待人工审批的工单草稿
```

主演示采用“异常诊断到工单草稿”闭环：

```text
在同一会话选中第 1200 周期，并描述“泵出口压力下降、温度升高且振动增加”
→ 查询第 1200 周期的真实数据摘要
→ 检索两份泵手册
→ 输出候选原因与建议检查顺序
→ 标注证据与不确定性
→ 安全审查
→ 生成不执行任何操作的工单草稿
```

系统必须把“压力下降、温度升高、振动增加”标记为用户观察，并独立展示第 1200 周期的真实数据是否支持、部分支持或不支持该观察；不得自动选择任意周期来制造数据证据。若故障诊断请求既未显式提供周期号，且同会话也没有已选周期，只能追问或明确进行无传感器证据的定性分析。

补充演示：

1. “离心泵启动前需要检查什么？”展示带页码引用的规程问答。
2. “直接切断电源并拆开泵体。”展示高风险拦截和 `approval_required=true`。

## 4. 已确认的技术决策

- Python：项目使用独立 Python 3.11 环境。
- 聊天模型：阿里云百炼北京地域 `qwen3.7-plus`。
- 模型协议：OpenAI-compatible，业务代码不绑定阿里云 SDK。
- Embedding：百炼北京地域 `text-embedding-v4`，1024 维。
- LightRAG：独立 Server REST API 模式；业务代码仅依赖适配器接口。
- 文档解析：MinerU 优先，失败或不可用时允许降级到 PyMuPDF。
- 后端：FastAPI。
- 演示前端：Streamlit，仅作为 API 客户端，不承载核心业务逻辑。
- 持久化：MVP 使用 SQLite 保存会话摘要、风险/草稿审阅记录和工单草稿。
- 外部调用：允许通过现有 `DASHSCOPE_API_KEY` 分阶段执行真实 Chat 与 Embedding 验证，不读取、打印或写入密钥值。

## 5. 功能范围

### 5.1 六类意图

| 意图 | 主要输入 | 主要工具或证据 | 最低输出要求 |
|---|---|---|---|
| `equipment_qa` | 设备名称、部件或参数问题 | 手册检索 | 结论、引用、限制说明 |
| `operation_procedure` | 启动、停机、润滑、检查、维护问题 | 手册规程检索 | 有顺序的步骤、前置条件、引用 |
| `safety_query` | 隔离、断电、泄压、冷却、关阀、排空问题 | 安全规则和手册 | 安全要求、风险提示、引用 |
| `sensor_query` | 周期编号、传感器或比较条件 | 周期级特征数据 | 结构化摘要、状态标签、数据来源 |
| `fault_diagnosis` | 异常描述和可选周期编号 | 手册、传感器、模拟案例 | 候选原因排序、证据、检查顺序、风险等级 |
| `work_order_draft` | 设备、现象、诊断上下文 | 工单生成工具 | 结构化草稿、审批状态、安全事项 |

### 5.2 文档知识检索

- 扫描 `data/manuals` 的真实文件，不依赖写死的中文文件名。
- 当前实际文件为：
  - `2196-ANSI-Manual-Chinese.pdf`，55 个物理 PDF 页。
  - `t1739cn.pdf`，62 个物理 PDF 页。
- 解析后每个文本块至少包含：
  - `source_file`
  - `document_title`
  - `page_number`
  - `section_title`
  - `chunk_id`
  - `document_type`
  - `equipment_type`
  - `parser_name`
  - `parser_version`
  - `source_sha256`
  - `limitations`
  - `extraction_warnings`
- 无限制或无警告时分别使用空列表；`section_title` 不可可靠提取时使用 `null`，上述字段不能被静默省略。
- 面向用户的引用格式至少为 `[文档名称，第X页，chunk_id]`。
- `page_number` 使用从 1 开始的物理 PDF 页；MVP 文档块不得跨物理页。
- `chunk_id` 必须由来源、页码、章节、块序号和规范化文本哈希稳定生成，并能反查摄取清单。
- 表格或图片无法可靠解析时，必须记录限制，不得补写或猜测缺失内容。
- LightRAG 至少提供 `local`、`global`、`hybrid` 或 `mix`、`naive` 查询能力；实际模式名称由锁定版本的官方 API 和 Smoke Test 校验后映射。

### 5.3 液压数据处理

原始目录 `data/raw_dataset/hydraulic_systems` 已确认包含 20 个文件和 2,205 个周期。数据集以 60 秒工作周期为行，TAB 为分隔符：

| 传感器 | 物理量 | 采样率 | 每周期点数 |
|---|---|---:|---:|
| `PS1`–`PS6` | 压力，bar | 100 Hz | 6000 |
| `EPS1` | 电机功率，W | 100 Hz | 6000 |
| `FS1`–`FS2` | 体积流量，l/min | 10 Hz | 600 |
| `TS1`–`TS4` | 温度，°C | 1 Hz | 60 |
| `VS1` | 振动，mm/s | 1 Hz | 60 |
| `CE` | 冷却效率，% | 1 Hz | 60 |
| `CP` | 冷却功率，kW | 1 Hz | 60 |
| `SE` | 效率因子，% | 1 Hz | 60 |

`profile.txt` 每行对应一个周期，五列标签为：

1. 冷却器状态：`3` 接近完全失效、`20` 效率降低、`100` 完全有效。
2. 阀状态：`100` 正常、`90` 轻微滞后、`80` 严重滞后、`73` 接近完全失效。
3. 泵内泄漏：`0` 无泄漏、`1` 轻微泄漏、`2` 严重泄漏。
4. 蓄能器压力：`130` 正常、`115` 略低、`100` 严重降低、`90` 接近完全失效。
5. 稳态标志：`0` 条件稳定、`1` 可能尚未达到静态条件。

处理脚本必须先验证完整文件列表、大小、行列数、分隔符、缺失值和周期一致性，再计算周期级特征：

- `mean`
- `std`
- `min`
- `max`
- `median`
- `range`
- `first`
- `last`
- `trend`
- `slope`

输出采用每周期一行的固定契约：`cycle_id`、5 个标签和 17 个传感器 × 10 个特征，共 176 列。特征列名使用 `<sensor>__<feature>`。数学定义固定为：总体标准差 `ddof=0`、`range=max-min`、`trend=last-first`，`slope` 为真实秒时间轴的一元最小二乘斜率。发现缺失、非有限值、列数或周期不一致时停止处理并报告，不进行隐式插值或均值填充。

用户可见 `cycle_id` 为 1–2205 的 1 基编号。所有周期均保留；`stable_flag=1` 时查询和诊断必须显示“可能尚未达到稳态”的警告。

输出固定为：

- `data/processed/hydraulic/cycle_features.parquet`
- `data/processed/hydraulic/cycle_features.csv`
- `data/processed/hydraulic/data_dictionary.json`
- `data/processed/hydraulic/processing_report.json`

传感器工具只返回周期级结构化摘要，不把高频原始数组发送给大模型。

UCI 数据来自公开液压实验台，两份 PDF 是不同系列的泵类说明书；二者不是同一真实资产。系统不得声称某个 UCI 周期来自手册所述泵、真实电厂或当前企业。融合只用于演示，并必须保留各自来源。

### 5.4 模拟业务数据

系统生成以下少量演示数据：

- `data/synthetic/equipment_master.csv`
- `data/synthetic/alarm_events.csv`
- `data/synthetic/fault_cases.json`
- `data/synthetic/work_orders.json`

每个独立合成业务实体必须包含 `data_type = synthetic_demo`。合成工单固定为 `status=DRAFT`、`executed=false`。界面、API 和文档必须明确说明这些记录不属于真实企业或电厂，也不能作为手册事实或工业验证案例。

### 5.5 LangChain 工具

至少实现：

- `search_manual_knowledge`
- `query_sensor_cycle`
- `compare_sensor_cycles`
- `search_fault_cases`
- `get_safety_requirements`
- `create_work_order_draft`

工具要求：

- 使用 Pydantic v2 定义输入和输出。
- 返回结构化结果和来源信息。
- 失败时返回可追踪的错误，不伪造成功。
- 不泄漏堆栈、密钥或内部路径给最终用户。
- 每次调用生成精简 Trace 条目。
- 每个工具具有独立单元测试。

### 5.6 Agent 工作流

- 明确 `AgentState`，覆盖用户输入、意图、实体、证据、引用、回答、重试、审批、工单和错误。
- 在任何工具调用前执行安全预检查，在最终回答返回前再次执行安全审查。
- 文档检索与传感器查询在互不依赖时并行执行。
- 证据不足时最多改写查询两次，不允许无限循环。
- 所有路由均有失败出口。
- 候选原因的 `confidence` 仅表示排序分数，不表示经过工业验证的故障概率。
- 回答必须区分事实、推断、建议检查项和未知信息。

### 5.7 工业安全与审批

当请求或回答涉及停机、断电、送电、拆卸、设备隔离、阀门切换、解除联锁、修改保护定值、修改控制参数、DCS/PLC 操作、强制信号、旁路保护或危险介质排放等动作的具体步骤、操作命令、建议或工单时：

- `approval_required=true`。
- 可以提供手册或规程引用。
- 可以给出受限的安全说明；只有用户明确请求 `work_order_draft` 且同一会话已有诊断时，才可以生成工单草稿。
- 必须要求具备资质和权限的人员确认。
- 不得声称任何操作已经执行。
- 不得提供绕过安全联锁或保护的方法。
- 高风险非工单请求只创建风险审阅记录，不得自动生成工单；工单和风险审阅使用不同记录类型。
- 审批 API 只允许审阅记录从 `PENDING_REVIEW` 变为 `REVIEWED/REJECTED`。工单审阅只更新草稿的 `approval_status`，风险审阅只记录人工决定；都不触发设备动作，也不表示设备操作获准。

纯解释性安全知识可以作为信息回答；只要请求或回答形成可执行的高风险步骤、操作命令或工单草稿，就必须要求审阅。解除联锁、旁路保护、强制信号和修改保护定值等绕过请求即使存在审阅记录也不得解锁。

所有面向用户的最终回答以以下文字结束：

> 本系统仅用于工业运维辅助分析，不能替代现场规程、持证人员判断或正式操作票。

## 6. API 范围

至少提供：

- `GET /health`
- `GET /api/v1/system/info`
- `POST /api/v1/chat`
- `POST /api/v1/ingest`
- `GET /api/v1/sensors/cycles/{cycle_id}`
- `POST /api/v1/sensors/compare`
- `GET /api/v1/work-orders`
- `POST /api/v1/work-orders/draft`
- `POST /api/v1/approvals/{approval_id}`

`POST /api/v1/chat` 至少返回 `answer`、`citations`、`trace`、`risk_level` 和 `approval_required`。所有接口使用统一错误结构和 Pydantic 请求/响应模型，并由 FastAPI 自动生成 OpenAPI 文档。

## 7. Streamlit 页面

Streamlit 仅调用 FastAPI，不直接读取数据库、LightRAG 存储或模型密钥。MVP 包含：

1. 智能问答：示例问题、回答、引用、Agent Trace。
2. 设备数据：周期编号、传感器摘要、简单趋势图、状态标签。
3. 故障分析：异常描述、候选原因、文档与数据证据、检查顺序、风险等级。
4. 工单草稿：编号、设备、现象、原因、检查项、安全事项、审批状态。

## 8. 非功能要求

### 8.1 可测试性

- 核心业务测试不依赖外部 API Key。
- 提供 Fake LLM、Fake RAG 和确定性的测试数据。
- 真实百炼和 LightRAG 测试作为显式 Smoke Test 分阶段运行。

### 8.2 可追溯性

- 回答保留引用和工具 Trace。
- 数据处理输出包含源文件、处理时间、算法版本、行数与异常报告。
- API 请求使用 `conversation_id` 或请求 ID 关联日志。

### 8.3 安全与隐私

- 只创建 `.env.example`，不写入真实密钥。
- 日志对 Key、Authorization Header 和敏感值做脱敏。
- `data/raw_dataset/hydraulic_systems/**` 与 `data/manuals/**` 只读；处理前后以相对路径、字节数和 SHA-256 清单证明未变化。
- 原始 PDF、模型文件和大型数据默认不提交 Git。
- 真实云调用先通过离线验证，并限制并发、重试和导入规模。

### 8.4 可维护性

- 使用 `src` 布局和小型、职责单一的模块。
- 业务代码只依赖 LLM/RAG/存储抽象，不散落厂商调用。
- 配置集中由 Pydantic Settings 管理。

## 9. 明确非目标

- 真实 DCS、PLC、阀门、电机或联锁接入。
- 自动执行停机、送电、拆卸或阀门操作。
- Kubernetes、大型微服务集群或多租户 SaaS。
- 复杂权限系统、手机 App、音频诊断、计算机视觉产品化或深度学习训练。
- 没有数据依据的故障概率。
- 将 Streamlit 作为长期生产前端。
- 独立 BM25/Elasticsearch/OpenSearch 检索链路；MVP 先以 LightRAG 已验证的查询模式建立基线，后续仅在检索评估未达门槛时引入。

## 10. 交付物

- 可安装的 Python 3.11 项目与 `.env.example`。
- FastAPI、Streamlit、独立 LightRAG 服务接入和 CLI/脚本。
- 数据检查、处理、文档解析、LightRAG 导入、模拟数据及 Smoke 脚本。
- 单元、集成、API、工作流、安全与评估测试。
- 至少 30 条黄金问题及带样本量的评估报告。
- 面向初学者的主 `README.md`、独立 `data/README.md`、API、数据字典、演示和故障排查文档。

完整通过条件见 [MVP_ACCEPTANCE_CRITERIA.md](MVP_ACCEPTANCE_CRITERIA.md)。
