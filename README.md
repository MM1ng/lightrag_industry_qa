# 基于 LightRAG 的工业离心泵知识库问答系统

这是一个收敛的单页知识库问答 MVP：使用 PyMuPDF 解析 `data\manuals` 中的两份离心泵 PDF，将带文件名、物理页码和章节信息的文本块导入 LightRAG，再通过阿里云百炼模型与 `text-embedding-v4`（1024 维，北京端点）回答问题。

## 功能范围

项目只包含 `src\industrial_rag`、问答/图谱 Streamlit 界面及其配套脚本，提供：

- 基于工业手册证据的问答与物理页码、chunk ID 引用。
- 只读知识图谱可视化与实体邻居查询。
- 固定黄金集的检索、引用、拒答、可用性与延迟评测。

不包含多 Agent、LangGraph、传感器分析、故障诊断、工单、审批或数据库业务流程。

## Windows Conda 安装

在 Anaconda Prompt 中执行：

```bat
cd /d D:\industrial_energy_agent
conda create -n industrial-rag python=3.11 -y
conda activate industrial-rag
python -m pip install --upgrade pip
python -m pip install -e "D:\industrial_energy_agent[dev]"
copy .env.example .env
```

编辑 `.env`，只填写本机密钥：

```dotenv
DASHSCOPE_API_KEY=你的百炼密钥
LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
LLM_MODEL=kimi-k2.6
LLM_FALLBACK_MODELS=qwen3.6-plus,qwen3.6-flash,qwen-plus,qwen3.5-flash-2026-02-23
EMBEDDING_MODEL=text-embedding-v4
EMBEDDING_DIM=1024
LIGHTRAG_WORKING_DIR=./lightrag_storage
```

`.env` 已被 Git 忽略。程序不会打印密钥，也不要把真实密钥写入 `.env.example`。

`LLM_MODEL` 是首选生成模型；`LLM_FALLBACK_MODELS` 是可选的、以英文逗号分隔的后备链。留空时，默认依次尝试 `kimi-k2.6`、`qwen3.6-plus`、`qwen3.6-flash`、`qwen-plus`、`qwen3.5-flash-2026-02-23`（如果首选模型已改为其中之一，会自动从默认后备项中排除）。仅在百炼返回额度耗尽、限流或模型不可用时才切换；其他异常会原样返回，避免掩盖配置和请求问题。一次切换成功后，进程后续请求继续使用该模型。Embedding 始终固定为 `text-embedding-v4`（1024 维），不会随生成模型切换。

## 首次运行

```bat
cd /d D:\industrial_energy_agent
conda activate industrial-rag
python scripts\inspect_environment.py
python scripts\parse_manuals.py
python scripts\ingest_documents.py
streamlit run app\streamlit_app.py
```

浏览器默认打开 `http://localhost:8501`。支持以下知识库检索模式，默认使用 `mix`：

- `mix`：知识图谱和向量检索综合模式，默认
- `hybrid`：结合 local 和 global 检索
- `local`：面向具体实体及局部关系
- `global`：面向整体主题和全局关系
- `naive`：普通文本块向量检索

本项目不提供 `bypass` 模式，因为 bypass 不会检索手册，无法满足基于证据回答和页码引用要求。引用来自 LightRAG 检索结果中由解析器写入的元数据，显示为 `[文档名称，第X页]`，页码不由模型生成。

## 本地 FastAPI 问答服务

在已经完成首次文档导入、且已激活 `industrial-rag` Conda 环境的终端中，先启动本地 API：

```bat
python -m uvicorn industrial_rag.api:app --host 127.0.0.1 --port 8000
```

另开一个已激活同一环境的终端，再启动 Streamlit：

```bat
python -m streamlit run app\streamlit_app.py
```

API 仅绑定到本机回环地址。`GET http://127.0.0.1:8000/readyz` 在索引和运行时可用时返回：

```json
{"status":"ready"}
```

使用 `POST http://127.0.0.1:8000/v1/query` 提交 JSON 请求；服务固定使用 `mix` 检索模式。下面是 PowerShell 示例（不启用服务认证时）：

```powershell
$body = @{ query = 'E102 如何处理？' } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:8000/v1/query' -ContentType 'application/json' -Body $body
```

响应的 `status` 为 `success` 时，`answer`、可追溯的 `citations`（文档名、页码、chunk ID）和 `claims` 均会返回。若没有足够的手册证据，HTTP 仍为 `200`，但 `status` 为 `insufficient_evidence`，且 `citations` 与 `claims` 都是空数组；客户端应将其作为拒答结果处理。

### 可选服务认证

`.env` 中的 `SERVICE_API_KEY` 是可选项：留空时，`/v1/query` 不要求认证；设置后，每次查询都必须携带 `Authorization: Bearer <SERVICE_API_KEY>`。不要将真实密钥写进命令历史、README 或版本控制。PowerShell 示例使用明显的占位符：

```powershell
$serviceApiKey = '<replace-with-local-service-api-key>'
$headers = @{ Authorization = "Bearer $serviceApiKey" }
$body = @{ query = 'E102 如何处理？' } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:8000/v1/query' -Headers $headers -ContentType 'application/json' -Body $body
```

`history` 可选，格式为最多 10 条 `user` 或 `assistant` 消息，每条内容最多 2000 个字符。知识库查询接口会将其限制为最近的有界上下文，仅用于解析当前问题中的安全指代、省略和明确条件继承，再把独立问题交给检索；历史内容不会作为答案证据，也不会写入检索 trace。若无法安全改写，接口会返回 `QUERY_REWRITE_AMBIGUOUS` 或 `QUERY_REWRITE_FAILED`，不会执行检索。旧版 `/v1/query` 仍保持兼容路径：它会校验请求格式，但当前代码不会使用 `history` 做会话改写，也不会把历史传给 legacy runtime。

## Vue 知识问答工作台

迁移期的统一前端位于 `frontend/`，普通用户入口为 `/chat` 和 `/graph`，管理员入口为 `/admin/login`，验证后可访问知识库、文档、更新任务和 Generation 页面。前端通过 Vite 将 `/v1`、`/readyz` 和 `/health` 代理到 `http://127.0.0.1:8000`；生产构建由 FastAPI 或独立静态服务器提供 `frontend/dist`。

启动本地 Vue 工作台：

```powershell
cd D:\industrial_energy_agent
npm --prefix frontend install
.\scripts\start_frontend.ps1
```

一键启动 Qdrant、FastAPI 和 Vue 工作台，并自动打开浏览器：

```powershell
cd D:\industrial_energy_agent
.\scripts\start_workbench.ps1
```

如果只想启动服务、不打开浏览器，使用 `-NoBrowser`。脚本会自动加载 `.env.local_staging`，并检查后端 `/readyz` 与前端 `/chat`。

也可以直接在 `frontend/` 目录执行 `npm run dev`。复制 `frontend/.env.example` 为 `frontend/.env` 后，可用 `VITE_API_BASE_URL` 指向非默认 API 地址；留空时使用开发代理。真实问答验证仍需要先启动 FastAPI、完成知识库导入并确认 `/readyz` 就绪。Streamlit 入口 `scripts\start_ui.ps1` 和 `app\streamlit_app.py` 保留为迁移期回退，不会被 Vue 构建覆盖。

普通用户不会看到 Chunk、Generation、trace、模型名或凭据；管理员 Bearer 凭据只保存在当前页面内存中，刷新后需要重新验证。后端仍对每个管理接口执行真实权限校验。

### 冻结索引管理元数据回填

如果接入的是已经构建完成的 `phase4_frozen_index`，索引本身可能已有文档和 chunks，但管理库没有对应的 `documents` 记录。此时只执行一次 dry-run：

```powershell
python scripts\backfill_frozen_index_metadata.py --knowledge-base-id 8fce4626859d44abb70a9ae5b0372cea
```

确认输出的文档数和 chunks 与现有 Generation 一致后，加上 `--apply` 执行回填。工具会把源 PDF 复制到该知识库的标准 uploads 目录，登记 `indexed/done/done` 文档状态并更新知识库文档与 chunks 统计；不会调用 LightRAG、Qdrant，不会创建历史更新任务，也不会修改 Generation。工具可重复执行，重复文件会跳过。

### 公共错误格式

所有 API 错误均返回不包含内部异常或密钥的公共 JSON：`request_id`、`code`、`message` 和 `retryable`。错误代码包括：

- `INVALID_REQUEST`（422；未知路径为 404、方法不允许为 405）：查询或 history 格式/长度不合法，或请求了无效路由。
- `UNAUTHORIZED`（401）：已配置 `SERVICE_API_KEY`，但 Bearer 凭据缺失或无效。
- `INDEX_NOT_READY`（503）：索引或运行时尚未就绪，可稍后重试。
- `TIMEOUT`（504）：查询超时，可稍后重试。
- `UPSTREAM_UNAVAILABLE`（502）：上游知识库服务暂时不可用，可稍后重试。

## 知识图谱可视化

应用包含「智能问答」与「知识图谱」两个页签。图谱由 LightRAG 文档导入时自动生成，文件位于：

`lightrag_storage/graph_chunk_entity_relation.graphml`

说明：

1. 页面只显示图谱子集，避免浏览器卡顿；全局概览默认按节点 degree 选取约 50 个节点。
2. 可搜索实体并展示 1 跳或 2 跳邻居子图。
3. 默认仅显示 degree 最高的约 15 个节点名称，可用「显示全部节点名称」切换；悬停可看完整中英文与来源信息。
4. 点击节点会高亮其邻居并弱化无关节点；稳定布局后自动居中 fit，拖拽后保持位置。
5. 「重新加载图谱」只重新读取 GraphML，不会清理问答 Runtime，也不会调用百炼 API。
6. GraphML 不存在时，请先执行 `python scripts/ingest_documents.py`。
7. 修改 `.env` 后需要重启 Streamlit。
8. 当前不支持图谱编辑和写回（展示层中文映射不影响实体 ID 与 GraphML）。

## 测试与 Smoke Test

离线命令不会调用真实百炼 API：

```bat
python -m pytest -q
ruff check .
python scripts\parse_manuals.py
python scripts\smoke_test.py
```

完成导入后，可单独验证真实接口、三次真实查询和无依据问题：

```bat
python scripts\ingest_documents.py
python scripts\smoke_test.py --real
```

## 质量评测

评测使用人工核验的黄金问题集，而不是由模型给自己打分。先复制格式样例：

```powershell
Copy-Item data\evaluation\golden_questions.example.jsonl data\evaluation\golden_questions.jsonl
```

逐行替换样例中的问题和 `expected_citations`。每个需要证据的问题必须填写实际解析/摄取产物中的 `source_file`、物理 `page_number` 和 `chunk_id`；无依据问题使用 `"expects_evidence": false` 和空引用数组。不要把示例文件中的 `example-manual.pdf` 当作真实评测证据。

确认索引已就绪、`.env` 中已配置本机 API Key 后，显式执行真实评测：

```powershell
$env:LIGHTRAG_WORKING_DIR='D:\industrial_energy_agent\lightrag_storage'
python scripts\evaluate.py --real --golden data\evaluation\industrial_pump_golden_set_50.jsonl --output dist\industrial_pump_trust_gates_report.json
```

该命令会调用已有 LightRAG 索引和模型，因此默认测试不会运行它。报告包含：

- `retrieval_recall_at_1/3/5`：人工标注的目标 chunk 在前 K 个返回引用中出现的比例。
- `mean_reciprocal_rank`：首个目标引用排名的倒数平均值，未命中记为 0。
- `citation_presence_rate`：应有证据的问题中，实际返回引用的比例。
- `citation_traceability_rate`：返回引用与黄金集完整 `文档名 + 页码 + chunk ID` 匹配的比例。
- `no_evidence_refusal_rate`：无依据问题返回固定依据不足提示且没有引用的比例。
- `average_citations_per_answer`、`max_citations_per_answer`：已完成且非拒答回答的平均/最大引用数；用于确认每次成功回答最多携带 3 个可追溯引用。
- `document_route_accuracy`：黄金证据只属于一份手册的问题中，返回引用非空且全部来自该手册的比例；跨手册黄金问题不计入该指标分母。
- `success_rate`、`latency_p50_ms`、`latency_p95_ms`：可用性与响应延迟。

信任门控会先按手册别名路由、筛选证据，再只把最多 3 个入选文本块交给模型生成；证据不足时直接返回固定拒答，不调用模型。Kimi K2.6 门控前的同一 50 题基线为 Recall@5 `0.70`、引用可追溯率 `0.9375`、拒答率 `0.0`、成功率 `1.0`。门控后的验收条件为：拒答率至少 `0.90`、引用可追溯率至少 `0.95`、最大引用数不超过 `3`、成功率保持 `1.0`，且 Recall@5 不得低于 `0.65`（相对基线最多下降 `0.05`）。若 Recall@5 低于该保护线，应回滚或修正证据筛选阈值后再发布。

2026-07-30 Kimi K2.6 实测报告位于 `dist/industrial_pump_trust_gates_report_final2.json`：50 题 Recall@5 `0.757143`、引用可追溯率 `0.958333`、无证据拒答率 `1.0`、最大引用数 `3`、文档路由准确率 `1.0`、成功率 `1.0`。

第一版不安装 Ragas。上述确定性指标和人工抽查引用是否支持回答，是发布前的基础质量门；后续如需语义评分，可在同一黄金集上将 Ragas 作为可选离线工具加入。

## 安全重建索引

Embedding 模型或维度不一致时，程序会停止并提示重建，不会自动删除旧索引。请先关闭 Streamlit，然后在 PowerShell 中备份现有目录：

```powershell
Set-Location 'D:\industrial_energy_agent'
$backup = '.\lightrag_storage.backup-' + (Get-Date -Format 'yyyyMMdd-HHmmss')
Move-Item -LiteralPath '.\lightrag_storage' -Destination $backup
python scripts\ingest_documents.py
```

确认新索引和查询正常后，再由你决定是否保留备份。原始 PDF 不会被修改。
