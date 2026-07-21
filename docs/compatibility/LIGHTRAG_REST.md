# LightRAG Server 1.5.4 REST 兼容性基线

## 锁定结论

MVP 锁定 `lightrag-hku[api]==1.5.4`，运行在独立的 Python 3.11 Conda 环境
`energyops-lightrag`。业务包不安装、不导入 `lightrag`，只通过 HTTPX 调用 REST API。

2026-07-21 在 Windows、本机独立端口 `127.0.0.1:19621` 和全新
`data/processed/lightrag/contract_probe_task8_7970487_v4/storage` 中完成真实探测：

- `GET /health` 返回 `core_version=1.5.4`；
- LLM 为 `openai/qwen3.7-plus`；
- Embedding 为 `openai/text-embedding-v4`，维度 1024，并发送 `dimensions=1024`；
- `POST /documents/text` 和 `/documents/texts` 均成功完成后台处理；
- track status 和 paginated documents 可用于导入对账；
- `/query/data` 的 `local`、`global`、`hybrid`、`naive`、`mix` 五种模式均成功；
- 不带服务令牌访问受保护路由返回 HTTP 403。

注意：`/health.auth_mode` 描述的是 JWT 账户认证，不是 API Key 开关。本次配置未启用 JWT，
所以该字段为 `disabled`；API Key 是否生效必须以不带 `X-API-Key` 的受保护请求返回 403 为准。

机器可读的字段清单位于 `config/lightrag_contract.json`。

## 认证边界

服务令牌只使用 `LIGHTRAG_API_KEY`，请求头固定为 `X-API-Key`。不得使用
`Authorization`，也不得复用 `LLM_API_KEY` 或 `DASHSCOPE_API_KEY`。设置层和启动脚本都会拒绝
相同的服务令牌与模型密钥。

真实模型密钥只传给 LightRAG 子进程的 OpenAI binding：

```text
LLM_BINDING_API_KEY
EMBEDDING_BINDING_API_KEY
```

日志、Smoke 输出和契约文件均不得保存真实密钥。

## 已验证 REST 映射

| 操作 | 方法与路径 | 关键响应 |
| --- | --- | --- |
| 健康检查 | `GET /health` | `status`, `working_directory`, `configuration`, `auth_mode`, `core_version` |
| 单文本导入 | `POST /documents/text` | `status`, `message`, `track_id` |
| 批量文本导入 | `POST /documents/texts` | `status`, `message`, `track_id` |
| 导入跟踪 | `GET /documents/track_status/{track_id}` | `documents`, `total_count`, `status_summary` |
| 文档分页 | `POST /documents/paginated` | `documents`, `pagination`, `status_counts` |
| 纯检索数据 | `POST /query/data` | `data.entities`, `relationships`, `chunks`, `references` |

适配器使用 `/query/data`，避免让 LightRAG 再生成一次最终答案。HTTP 200 但
`status="failure"` 仍视为应用失败，不得转换为空结果。

LightRAG 的请求 Schema 还列出 `bypass`，但项目公共接口不暴露、也不把它静默映射为其他模式。

## 明确不支持的能力

1. 任意 metadata 服务端过滤；
2. 客户端指定 document ID；
3. 幂等 upsert；
4. 按任意文件路径直接 GET；
5. 独立 sources endpoint。

因此 `get_sources()` 必须通过本地摄取清单解析。模糊导入结果必须组合 track status、文档分页和
query references 三种探测；证据不一致时返回未知状态，不得自动重放并声称“恰好一次”。

## Windows 启动

创建或更新独立环境：

```powershell
D:\anaconda\Scripts\conda.exe env create -f environment.lightrag.yml
```

在当前 PowerShell 进程设置两个不同的密钥，然后启动：

```powershell
$env:LIGHTRAG_API_KEY = [Guid]::NewGuid().ToString("N")
# DASHSCOPE_API_KEY 或 LLM_API_KEY 需已在当前进程中设置。
.\scripts\start_lightrag.ps1 -Port 19621
```

启动脚本会：

- 在启动前拒绝占用端口，不会连接到其他项目的 LightRAG；
- 将服务绑定到 `127.0.0.1`；
- 使用隐藏进程；
- 把索引、输入和日志限制在 `data/processed/lightrag`；
- 强制子进程使用 UTF-8，规避 Windows GBK 无法编码启动横幅 emoji 的问题；
- 重定向空 stdin，规避缺少启动目录 `.env` 时的交互确认阻塞。

不要停止或复用其他项目已监听的 9621 服务。若 9621 被占用，显式选择空闲端口，并同步设置：

```powershell
$env:LIGHTRAG_BASE_URL = "http://127.0.0.1:19621"
```

## 复验命令

以下命令只读取当前进程环境，不自动加载 `.env`：

```powershell
D:\anaconda\Scripts\conda.exe run -n energyops-copilot python scripts/probe_lightrag_contract.py --require-all-modes
```

如需同时复验单文本和批量导入（会消耗模型额度）：

```powershell
D:\anaconda\Scripts\conda.exe run -n energyops-copilot python scripts/probe_lightrag_contract.py --exercise-insert --require-all-modes
```

默认离线测试不会访问该服务。显式设置 `RUN_LIGHTRAG_SMOKE=1` 后才运行 live smoke。
