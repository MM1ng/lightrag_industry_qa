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

浏览器默认打开 `http://localhost:8501`。页面支持 `mix`、`local`、`global`、`naive`，默认使用 `mix`。引用来自 LightRAG 检索结果中由解析器写入的元数据，显示为 `[文档名称，第X页]`，页码不由模型生成。

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

## 安全重建索引

Embedding 模型或维度不一致时，程序会停止并提示重建，不会自动删除旧索引。请先关闭 Streamlit，然后在 PowerShell 中备份现有目录：

```powershell
Set-Location 'D:\industrial_energy_agent'
$backup = '.\lightrag_storage.backup-' + (Get-Date -Format 'yyyyMMdd-HHmmss')
Move-Item -LiteralPath '.\lightrag_storage' -Destination $backup
python scripts\ingest_documents.py
```

确认新索引和查询正常后，再由你决定是否保留备份。原始 PDF 不会被修改。
