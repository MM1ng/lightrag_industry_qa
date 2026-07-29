# 基于 LightRAG 的工业离心泵知识库问答系统

这是一个收敛的单页知识库问答 MVP：使用 PyMuPDF 解析 `data\manuals` 中的两份离心泵 PDF，将带文件名、物理页码和章节信息的文本块导入 LightRAG，再通过阿里云百炼 `qwen3.7-plus` 与 `text-embedding-v4`（1024 维，北京端点）回答问题。

新 MVP 只使用 `src\industrial_rag`、四个脚本和 `app\streamlit_app.py`。仓库中保留的旧 Agent、LangGraph、传感器、工单及数据库代码不属于本系统，也不会被导入。

## Windows Conda 安装

在 Anaconda Prompt 中执行：

```bat
cd /d D:\industrial_energy_agent
conda create -n industrial-rag python=3.11 -y
conda activate industrial-rag
python -m pip install --upgrade pip
pip install -r requirements.txt
copy .env.example .env
```

编辑 `.env`，只填写本机密钥：

```dotenv
DASHSCOPE_API_KEY=你的百炼密钥
LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
LLM_MODEL=qwen3.7-plus
EMBEDDING_MODEL=text-embedding-v4
EMBEDDING_DIM=1024
LIGHTRAG_WORKING_DIR=./lightrag_storage
```

`.env` 已被 Git 忽略。程序不会打印密钥，也不要把真实密钥写入 `.env.example`。

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
python scripts\evaluate.py --real --golden data\evaluation\golden_questions.jsonl --output dist\evaluation-report.json
```

该命令会调用已有 LightRAG 索引和模型，因此默认测试不会运行它。报告包含：

- `retrieval_recall_at_1/3/5`：人工标注的目标 chunk 在前 K 个返回引用中出现的比例。
- `mean_reciprocal_rank`：首个目标引用排名的倒数平均值，未命中记为 0。
- `citation_presence_rate`：应有证据的问题中，实际返回引用的比例。
- `citation_traceability_rate`：返回引用与黄金集完整 `文档名 + 页码 + chunk ID` 匹配的比例。
- `no_evidence_refusal_rate`：无依据问题返回固定依据不足提示且没有引用的比例。
- `success_rate`、`latency_p50_ms`、`latency_p95_ms`：可用性与响应延迟。

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
