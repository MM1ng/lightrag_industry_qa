# Local Startup Runbook（Phase 6 Release Candidate）

## 1. 启动 Qdrant（仅基础设施）

```powershell
docker start ira-phase3-qdrant-test
docker ps --filter name=ira-phase3-qdrant-test
# 验证
Invoke-RestMethod http://127.0.0.1:16333/collections
```

## 2. 激活 Conda 环境

```powershell
C:\Users\12189\.conda\envs\industrial-rag\python.exe --version   # 3.11.x
```

## 3. 环境变量检查

必需：`DASHSCOPE_API_KEY`、`QDRANT_URL=http://127.0.0.1:16333`、
`QDRANT_COLLECTION_PREFIX=ira_p3ar_4ac7a596`、`LLM_MODEL=qwen-plus-2025-07-28`、
`MODEL_FALLBACK_ENABLED=false`、`VECTOR_BACKEND=nano`、`EMBEDDING_MODEL=text-embedding-v4`、
`EMBEDDING_DIM=1024`。

可选：`SERVICE_API_KEY`（启用后写接口需 Bearer）、`CITATION_SHADOW_AUDIT_ENABLED=true`。

> 业务进程不要设置 `VECTOR_BACKEND=qdrant`；Qdrant 知识库按 KB 元数据自动路由。

## 4. 数据库迁移

```powershell
$env:DATABASE_URL='sqlite+aiosqlite:///D:/industrial_energy_agent/data/db/industrial_rag.db'
C:\Users\12189\.conda\envs\industrial-rag\python.exe -m alembic upgrade head
```

## 5. 启动 FastAPI

```powershell
$env:PYTHONPATH='D:\industrial_energy_agent\src'
Start-Process -FilePath 'C:\Users\12189\.conda\envs\industrial-rag\python.exe' `
  -ArgumentList '-m','uvicorn','industrial_rag.api:app','--host','127.0.0.1','--port','8000' `
  -WorkingDirectory 'D:\industrial_energy_agent' -WindowStyle Hidden
```

## 6. 启动 Streamlit

```powershell
Start-Process -FilePath 'C:\Users\12189\.conda\envs\industrial-rag\python.exe' `
  -ArgumentList '-m','streamlit','run','app/streamlit_app.py','--server.port','8501' `
  -WorkingDirectory 'D:\industrial_energy_agent' -WindowStyle Hidden
```

## 7. 健康检查

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health    # 存活
Invoke-RestMethod http://127.0.0.1:8000/ready     # 配置/DB/Qdrant
Invoke-RestMethod http://127.0.0.1:8000/version   # 版本
```

## 8. 常见错误

| 现象 | 处理 |
|---|---|
| `/ready` qdrant=unavailable | `docker start ira-phase3-qdrant-test` |
| 401 | 检查 `SERVICE_API_KEY` 与请求头一致 |
| 503 INDEX_NOT_READY | 检查 `VECTOR_BACKEND=nano` 与冻结 KB 注册 |
| 502 检索失败 | 检查 `QDRANT_COLLECTION_PREFIX` 是否为冻结前缀 |
| 504 超时 | 检查模型/网络；不切换模型 |
