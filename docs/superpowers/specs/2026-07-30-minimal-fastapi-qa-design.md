# 最小 FastAPI 问答服务设计

**日期：** 2026-07-30
**状态：** 待评审

## 目标

为工业知识库提供一个可被 Streamlit 或其他客户端调用的最小 HTTP 问答服务。服务复用现有 `LightRAGRuntime` 与 LightRAG 检索、生成、引用链路，不引入 LangGraph、Agent 编排、任务路由、会话存储、摄取接口或运维平台功能。

## 范围

### 服务端点

| 端点 | 作用 | 行为 |
| --- | --- | --- |
| `GET /readyz` | 就绪探针 | 运行时已初始化时返回 200；不可用时返回 503。仅返回服务状态，不泄露配置或异常细节。 |
| `POST /v1/query` | 基于知识库的问答 | 接收问题，使用固定 `mix` 检索模式调用运行时，返回答案、引用、状态、请求 ID 与耗时。 |

`POST /v1/query` 接受如下请求体：

```json
{
  "query": "设备报警 E102 如何处理？",
  "history": [{"role": "user", "content": "前一轮问题"}]
}
```

`history` 仅为兼容当前前端调用契约而接受和校验；第一版不将其传给模型、不持久化，也不宣称支持多轮记忆。这避免在没有明确会话策略时产生不可追溯的“伪多轮”行为。

成功响应遵循现有前端预期，包含：

```json
{
  "request_id": "…",
  "status": "success",
  "answer": "…",
  "citations": [
    {
      "citation_id": "cite_1",
      "document_name": "设备维护手册.pdf",
      "page": 12,
      "chunk_id": "chunk-abc"
    }
  ],
  "claims": [
    {
      "claim_id": "claim_1",
      "text": "…",
      "citation_ids": ["cite_1"]
    }
  ],
  "latency_ms": 123
}
```

当运行时明确返回现有的“证据不足”答案或没有可引用证据时，`status` 为 `insufficient_evidence`。服务不编造引用；`claims` 只是在有答案和引用时提供一个可追溯的整体答案声明。

### 鉴权

新增可选环境变量 `SERVICE_API_KEY`：

- 未配置：便于本机开发，接口不要求鉴权。
- 已配置：`/v1/query` 要求 `Authorization: Bearer <SERVICE_API_KEY>`；缺失或错误均返回安全的 401 响应。

`/readyz` 保持无鉴权，以便本地和部署环境的健康检查使用。密钥不会写入日志、响应或文档示例。

### 生命周期与并发边界

FastAPI 的 lifespan 在应用启动时创建一个 `LightRAGRuntime(Settings.from_env())` 并保存到 `app.state`，关闭时调用 `close()`。每个请求通过该单例运行时查询，仍由 `LightRAGRuntime` 负责其专属事件循环与串行访问。

这是一个运行时适配层，不是 Agent：

```text
HTTP 请求 → FastAPI → LightRAGRuntime → LightRAG 检索与生成 → 答案与引用
```

不在 API 层重新实现检索、重排、图谱查询或异步事件循环管理。

### 安全错误契约

所有面向客户端的错误均使用稳定、可消费的字段，不直接透出上游异常、路径、模型地址、密钥或原始问题内容：

```json
{
  "request_id": "…",
  "code": "UPSTREAM_UNAVAILABLE",
  "message": "知识库服务暂时不可用，请稍后重试。",
  "retryable": true
}
```

首版映射如下：

| 场景 | HTTP | 错误代码 |
| --- | --- | --- |
| 请求字段不合法 | 422 | `INVALID_REQUEST` |
| 缺少或错误的 Bearer 凭据 | 401 | `UNAUTHORIZED` |
| 运行时尚未可用 | 503 | `INDEX_NOT_READY` |
| 查询超时 | 504 | `TIMEOUT` |
| 运行时或上游调用失败 | 502 | `UPSTREAM_UNAVAILABLE` |

使用标准结构化日志记录请求 ID、结果状态和耗时；不记录密钥与完整问题文本。Langfuse、Ragas、链路追踪和指标平台不属于此最小服务的首版范围，后续可在这个稳定边界上按需增加。

## 实现位置与依赖

- 新增 `src/industrial_rag/api.py`：应用工厂、路由、鉴权、错误映射和响应模型。
- 在 `Settings` 中增加服务端鉴权配置；不改变现有模型、嵌入模型与 LightRAG 配置。
- 在 `pyproject.toml` 中增加 FastAPI 与 Uvicorn 运行依赖。
- 更新 `.env.example` 与 README，说明本地 API 和 Streamlit 的启动顺序。
- 不改动或提交当前工作区中尚未跟踪的前端/API-client 文件；服务端只遵守其已使用的调用契约。

运行命令预计为：

```powershell
uvicorn industrial_rag.api:app --host 127.0.0.1 --port 8000
streamlit run app/streamlit_app.py
```

## 测试策略

测试使用可注入的伪 `LightRAGRuntime`，不初始化真实模型或访问外部服务，覆盖：

1. 服务就绪与启动/关闭生命周期；
2. 具备引用的成功问答响应及字段映射；
3. 证据不足响应；
4. 未配置、缺失、错误和正确 API Key 的鉴权行为；
5. 请求校验、超时与上游异常的安全错误映射；
6. 不将历史消息存储或传入运行时的边界。

完成后运行 Ruff 和全量 pytest；真实知识库连通性仍由现有运行时与冒烟流程在配置完整的环境中验证。

## 明确不做

- LangGraph、多个 Agent、意图分流或工具调用编排；
- 会话数据库、用户管理、租户与权限模型；
- 文档上传、切分、摄取、异步任务队列；
- 告警、监控面板、Langfuse 追踪接入；
- Ragas 在线评测。现有离线确定性评测框架继续保留。
