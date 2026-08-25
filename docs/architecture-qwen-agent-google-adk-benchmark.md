# Architecture Benchmark Audit — Qwen-Agent + Google ADK

审计日期：2026-08-25
当前仓库：`MM1ng/lightrag_industry_qa`
当前分支：`codex/knowledge-qa-platform-design`
审计时 HEAD：`709d57b67221ffd8082b5b43ff35c7198d672433`

本报告是只读架构研究，不引入 Qwen-Agent 或 Google ADK，不修改生产行为，不调整评测框架。

## 1. Executive Summary

当前项目已经是一个有明确边界的工业文档知识问答系统，而不是通用 autonomous-agent 平台。它的核心链路已经形成了较强的可信度边界：

```text
Conversation History
  -> Query Rewrite / Normalization
  -> Generation-scoped LightRAG Retrieval
  -> Evidence Selection / Completion
  -> Provider Generation
  -> Grounding / Citation Binding
  -> Runtime Trace / Source Verification
```

结论：

- **KEEP**：当前的 Generation-scoped retrieval、evidence/citation lineage、deterministic grounding、bounded query rewrite 和 runtime lifecycle。它们比引入通用 Agent framework 更贴合工业 QA 的可审计要求。
- **ADOPT**：只建议借鉴三个模式：
  1. 统一 Provider Adapter/Gateway；
  2. 小型 request-local `QueryExecutionContext`；
  3. 明确的 pipeline stage contract 与 stage-level guardrail/observability。
- **DEFER**：持久化 Session/Memory、通用 Tool Registry、通用 Event Store、复杂 Workflow Runtime。
- **REJECT**：Multi-Agent、Supervisor、Planner、LangGraph、无限 ReAct、Agent Swarm、代码执行 Agent、Browser Agent 和大规模 MCP 接入。

当前最值得近期实施的唯一模式：**Unified Provider Adapter/Gateway**。原因是它直接对应当前已经出现过的 provider fallback、HTTP 500、OpenAI-compatible 兼容性和模型选择问题，收益明确，且不改变问答产品边界。

## 2. Source and Version Boundary

### Upstream sources

本次只使用两个官方开源项目的 README、官方源码和官方仓库内文档：

| Project | Revision used | Primary source URLs |
|---|---|---|
| Qwen-Agent | `main` source reviewed on 2026-08-25；可追溯发布 tag：`v0.0.26` / `37e7e5f` | [repository](https://github.com/QwenLM/Qwen-Agent), [README](https://github.com/QwenLM/Qwen-Agent/blob/main/README.md), [LLM base](https://github.com/QwenLM/Qwen-Agent/blob/main/qwen_agent/llm/base.py), [Assistant](https://github.com/QwenLM/Qwen-Agent/blob/main/qwen_agent/agents/assistant.py), [Tool base](https://github.com/QwenLM/Qwen-Agent/blob/main/qwen_agent/tools/base.py), [release tag](https://github.com/QwenLM/Qwen-Agent/releases/tag/v0.0.26) |
| Google ADK Python | `main` / `54493140a6697af5b82e03b9d7ecb77c15df4eb6` | [repository](https://github.com/google/adk-python), [README](https://github.com/google/adk-python/blob/main/README.md), [Runner](https://github.com/google/adk-python/blob/54493140a6697af5b82e03b9d7ecb77c15df4eb6/src/google/adk/runners.py), [InvocationContext](https://github.com/google/adk-python/blob/54493140a6697af5b82e03b9d7ecb77c15df4eb6/src/google/adk/agents/invocation_context.py), [Session](https://github.com/google/adk-python/blob/54493140a6697af5b82e03b9d7ecb77c15df4eb6/src/google/adk/sessions/session.py), [Plugin](https://github.com/google/adk-python/blob/54493140a6697af5b82e03b9d7ecb77c15df4eb6/src/google/adk/plugins/base_plugin.py) |

Qwen-Agent 的 `main` 分支在本次环境中无法通过 `git ls-remote` 获取完整 head SHA，因此报告使用官方当前 `main` 源码 URL，并额外记录官方最新可见 release tag `v0.0.26` / `37e7e5f`，避免把 tag 与 rolling branch 混为一谈。

### Current project files audited

- `src/industrial_rag/lightrag_service.py`
- `src/industrial_rag/services/query_application_service.py`
- `src/industrial_rag/runtime.py`
- `src/industrial_rag/services/runtime_manager.py`
- `src/industrial_rag/conversation/query_rewriter.py`
- `src/industrial_rag/answer_grounding.py`
- `src/industrial_rag/structured_generation_policy.py`
- `src/industrial_rag/structured_citation_output.py`
- `src/industrial_rag/citation_formatter.py`
- `src/industrial_rag/api.py`
- `src/industrial_rag/config.py`

## 3. Current Architecture Snapshot

### Application/runtime boundary

`QueryApplicationService` 已经承担了一个清晰的 application boundary：读取 KB 与 Generation 元数据、拒绝不可查询状态、执行 bounded query rewrite、安全策略、按 Generation 构造 Settings、从 `KnowledgeBaseRuntimeManager` 获取 runtime、调用 LightRAG，并把 rewrite 与 document identity 写回 retrieval trace。见 `services/query_application_service.py:38-185`。

`KnowledgeBaseRuntimeManager` 负责按 KB/Generation/backend/settings 维护 runtime cache；`runtime.py` 则是 Streamlit 同步线程到 asyncio LightRAG service 的生命周期桥接。后者明确保证 event loop、asyncio lock 和 service 在后台线程中创建，且 query/close 有超时和关闭语义。

### Conversation boundary

`QueryRewriter` 将历史限制为 bounded user/assistant messages，只用于解决指代、省略和约束继承，不把历史当作事实证据。它输出结构化的 `QueryRewriteResult`，在 ambiguous/failed 时拒绝猜测。见 `conversation/query_rewriter.py:1-5,21-55,58-83,86-123`。

### LightRAG pipeline boundary

`LightRAGService.query()` 虽然是较长的 orchestrator，但步骤已经有稳定边界：retrieval、evidence selection/completion、provider context construction、generation、structured citation validation、grounding、fallback 和 trace assembly。`structured_generation_policy.py` 与 `structured_citation_output.py` 负责确定性校验，不把“模型说有依据”当作依据。

因此，当前 `LightRAGService` 长度本身不足以证明需要拆成 Agent/Runner/DAG。当前问题更像是 provider boundary 和 stage metadata 的统一性问题，而不是缺少 workflow engine。

### Trust/lineage boundary

当前项目拥有：

- `generation_id` 约束的 retrieval 与 citation；
- `EvidenceRef` / `SourceRegistry` / `RequirementRegistry`；
- grounding audit 与 refusal/partial-answer 状态；
- `request_id`、`trace_id`、retrieval trace、provider context hash 和 structured output hash；
- Ragas、Golden Set 和 frozen snapshot evaluation。

这些能力已经比通用 Agent 框架的“消息 + 工具结果”粒度更适合回答“这句话由哪一代知识库中的哪一段材料支持”。

## 4. Qwen-Agent Findings

### 4.1 Model abstraction

Qwen-Agent 的 `BaseChatModel` 是 provider/model boundary：它保存 `model`、`model_type`、`generate_cfg`、`max_retries`，通过 `LLM_REGISTRY` 注册具体实现；统一 `chat()` 接受 Message/dict，处理 system message、token truncation、function calling、streaming、post-processing、cache 和 retry。错误通过 `ModelServiceError` 表达。见官方 [`qwen_agent/llm/base.py`](https://github.com/QwenLM/Qwen-Agent/blob/main/qwen_agent/llm/base.py#L29-L55)、[configuration and retry](https://github.com/QwenLM/Qwen-Agent/blob/main/qwen_agent/llm/base.py#L70-L88) 和 [chat boundary](https://github.com/QwenLM/Qwen-Agent/blob/main/qwen_agent/llm/base.py#L106-L125)。

对当前项目的启发不是复制 `BaseChatModel`，而是把 provider-specific 细节集中到一个可测试的 adapter/gateway：调用参数、超时、retryable error、fallback selection 和最终 error code 不应分散在 LightRAG、query rewrite、semantic judge 和脚本中。

### 4.2 Message and conversation

Qwen-Agent 的高层 Agent 接受 message list 并以 generator 流式返回 message list；README 示例明确由调用方维护 `messages` 并在每轮把 bot response 追加回 history。见 [README chatbot example](https://github.com/QwenLM/Qwen-Agent/blob/main/README.md#developing-your-own-agent)。

这说明 Qwen-Agent 的 raw conversation history 与 Agent execution logic 可以分离，但它并不自动解决工业 QA 的证据边界。当前项目的做法更严格：history 只进入 QueryRewriter，不能直接进入 evidence 或 answer grounding。应继续 KEEP。

### 4.3 Tool abstraction

`BaseTool` 使用 `TOOL_REGISTRY`、名称、description、parameters 和 `call()`，并提供 OpenAI-compatible JSON schema 检查；`ToolServiceError` 作为工具服务错误边界。见官方 [`tools/base.py`](https://github.com/QwenLM/Qwen-Agent/blob/main/qwen_agent/tools/base.py#L22-L38)、[schema validation](https://github.com/QwenLM/Qwen-Agent/blob/main/qwen_agent/tools/base.py#L55-L98) 和 [`BaseTool`](https://github.com/QwenLM/Qwen-Agent/blob/main/qwen_agent/tools/base.py#L100-L104)。

可借鉴的边界是：声明、输入校验、执行和错误翻译应分开。当前 retrieval 和 source verification 不是由模型自由决定的 tools，而是安全/可信 pipeline stages，因此不应把它们改成模型选择的 Tool Agent。

### 4.4 RAG integration

Qwen-Agent 的 `Assistant` 将外部 `knowledge` 或文件检索结果格式化成带 source/content 的 knowledge prompt，然后再交给父类 Agent 进行工具调用和生成；见 [`Assistant._run`](https://github.com/QwenLM/Qwen-Agent/blob/main/qwen_agent/agents/assistant.py#L76-L106) 与 [`_prepend_knowledge_prompt`](https://github.com/QwenLM/Qwen-Agent/blob/main/qwen_agent/agents/assistant.py#L107-L138)。

这是一种“retrieval component -> prompt knowledge -> agent”模式。对当前项目的结论是反向借鉴：LightRAG retrieval/evidence/grounding 应继续独立于生成，不应被塞进一个自主 Agent loop；当前系统的 citation binding 和 generation-scoped evidence 要保留在 Agent 之外。

### 4.5 Error/fallback

Qwen-Agent 在统一 LLM boundary 处理 retry、streaming retry 限制、cache 和 model-service error，而不是让每个高层 Agent 自己处理 provider exception。特别是源码明确指出 delta-stream 不走 retry，因为它不利于高级 post-processing 和 retry。见 [`BaseChatModel.chat`](https://github.com/QwenLM/Qwen-Agent/blob/main/qwen_agent/llm/base.py#L152-L157) 与 [retry dispatch](https://github.com/QwenLM/Qwen-Agent/blob/main/qwen_agent/llm/base.py#L228-L234)。

这与当前项目已有的 DashScope fallback、semantic judge HTTP 500 和 OpenAI-compatible 兼容性问题高度相关，是最有直接 ROI 的借鉴点。

## 5. Google ADK Findings

### 5.1 Agent versus Runner

ADK README 将 Runner/Workflow Runtime、Agent、Tool 和 Session 作为不同层次；Runner 源码的类注释明确说明它管理 agent 在 session 中的执行，并协调事件、artifact、session、memory、credential 和 plugin 服务。见 [ADK README](https://github.com/google/adk-python/blob/main/README.md#-key-features) 与 [`Runner` class](https://github.com/google/adk-python/blob/54493140a6697af5b82e03b9d7ecb77c15df4eb6/src/google/adk/runners.py#L192-L229)。

当前项目已经有类似但更窄的分层：FastAPI/router 负责 HTTP/auth/error mapping，`QueryApplicationService` 负责 application orchestration，`KnowledgeBaseRuntimeManager` 负责 runtime cache/lifecycle，`LightRAGService` 负责 QA pipeline。无需引入 ADK Runner；需要的是继续保持这些职责不互相渗透。

### 5.2 Session, state and invocation context

ADK `Session` 表示一组 user-agent interactions，包含持久化 `state`、有序 `events` 和更新时间；见 [`Session`](https://github.com/google/adk-python/blob/54493140a6697af5b82e03b9d7ecb77c15df4eb6/src/google/adk/sessions/session.py#L26-L68)。

ADK `InvocationContext` 表示一次从 user message 到 final response 的 invocation，携带 session、invocation id、agent path、agent state 和终止标记，并且对 LLM call 数量提供限制；见 [`InvocationContext`](https://github.com/google/adk-python/blob/54493140a6697af5b82e03b9d7ecb77c15df4eb6/src/google/adk/agents/invocation_context.py#L80-L125) 与 [context fields](https://github.com/google/adk-python/blob/54493140a6697af5b82e03b9d7ecb77c15df4eb6/src/google/adk/agents/invocation_context.py#L140-L188)。

对当前项目的低成本启发是区分：

- request-local：`request_id`、`trace_id`、原始 question、当前 rewrite result、deadline/timeout；
- query-local：`kb_id`、`generation_id`、generation epoch、retrieval query、evidence registry；
- conversation-local：有限 history；
- runtime infrastructure：LightRAG service、Qdrant client、runtime cache、provider client；
- persistent governance/evaluation：retrieval trace、answer snapshot、grounding audit、evaluation artifacts。

这些字段不应全部进入一个可变 God Object。只建议一个小型 frozen `QueryExecutionContext`，服务于跨 stage 的 identity/deadline/trace，不把 provider client、完整 history 或 evidence text 塞进去。

### 5.3 Tool execution separation

ADK 的 Runner/InvocationContext 将 agent execution、session events、tool calls 和 tool results 作为不同概念。当前 ADK 还通过 ToolContext/CallbackContext 暴露受控 context，而不是让工具自行查找全局 session。

当前项目的 retrieval/source verification 不应成为模型可任意调用的 Tool。若未来出现企业 API，ADK 的启发应限定为：typed input schema、explicit result/error、request-scoped context 和 audit event；不应引入开放式 tool loop。

### 5.4 Callback/plugin/guardrail lifecycle

ADK `BasePlugin` 的官方源码把 plugin 定义为可在 agent、tool、LLM 关键执行点拦截/修改行为的全局机制，适合 logging、monitoring、caching 和 request/response modification；plugin 与 agent callback 按顺序执行，并可 short-circuit。见 [`BasePlugin`](https://github.com/google/adk-python/blob/54493140a6697af5b82e03b9d7ecb77c15df4eb6/src/google/adk/plugins/base_plugin.py#L36-L65)。

当前项目不应把所有 pipeline stage 改造成 callback：那会削弱 evidence flow 的显式可读性和测试定位。值得借鉴的是“每个 stage 有统一 before/after metadata 和失败语义”，而不是通用 callback framework。

### 5.5 Observability

ADK 的 Runner 以 Session events 组织 user/model/tool interaction，InvocationContext 为一次执行提供 invocation identity 和上下文；这是一种通用 agent lineage 模型。当前项目已经有更领域化的 lineage：retrieval trace、provider context hash、evidence IDs、citation IDs、grounding audit 和 request/trace IDs。

评估：当前 trace 不是缺少一个通用 event bus，而是应继续确保各 stage 复用同一 execution identity，并避免把敏感 evidence/full history 复制到公开 response。结论 KEEP current trace；ADOPT 轻量 stage event schema，而不是 ADK event store。

## 6. Architecture Mapping

| Concern | Current Project | Qwen-Agent | Google ADK | Assessment |
|---|---|---|---|---|
| Model Provider | Settings、LightRAG backend/provider helpers、fallback models、evaluation adapters 分散在多个边界 | `BaseChatModel` + registry + `ModelServiceError` + retry/cache | model-agnostic model interface behind Runner/Agent flows | **ADOPT** unified provider gateway |
| Runtime | `LightRAGRuntime`、`KnowledgeBaseRuntimeManager`、async service lifecycle | Agent/LLM runtime inside agent loop | `Runner` owns execution/services | **KEEP** narrow runtime boundary; no framework migration |
| Application Service | `QueryApplicationService` resolves KB/Generation, rewrite, safety, runtime query and trace enrichment | Agent delegates to LLM/tools | Runner drives Agent in Session | **KEEP**, optionally add small context |
| Conversation History | bounded history only enters `QueryRewriter`; not evidence | caller/Agent passes message list | Session events persist interaction history | **KEEP** current evidence-neutral boundary |
| Session | browser session history; request/trace records and evaluation snapshots separate | message list is caller-managed | persistent Session with state/events | **DEFER** persistent agent session |
| Execution Context | fields passed across service/runtime/trace functions | model config and kwargs | `InvocationContext` | **ADOPT** small frozen query context |
| Retrieval | Generation-scoped LightRAG/Qdrant plus selection/completion | RAG knowledge injected into Assistant prompt | tool/workflow capable, framework-agnostic | **KEEP** independent retrieval |
| Tool | no model-selected production tools in QA path | `BaseTool`, registry, JSON schema, `call()` | typed tool/context/result and confirmation | **DEFER**; use only for future bounded enterprise APIs |
| Generation | provider context -> one generation -> structured citation validation | Assistant prepends knowledge then runs Agent | Agent steps may call LLM/tools repeatedly | **KEEP** bounded generation |
| Guardrail | safety policy, grounding, structured output and citation validation | model/tool error classes and output processing | callbacks/plugins can intercept/short-circuit | **ADOPT** stage contract, not global callback rewrite |
| Grounding | deterministic claim/evidence support and refusal | not the primary abstraction | framework-level control, not domain grounding | **KEEP** current domain-specific grounding |
| Citation | SourceRegistry, EvidenceRef, citation binding and source verification | source/content knowledge snippets | events/tool results provide generic lineage | **KEEP** current stronger domain lineage |
| Tracing | request/trace IDs, retrieval/provider/grounding traces, hashes | logger/model errors/cache | session events/invocation context | **KEEP + small stage metadata alignment** |
| Retry | runtime timeouts, provider-specific fallback and evaluation retries in multiple places | centralized LLM retry dispatch | runtime/workflow retry options | **ADOPT** provider retry/error translation |
| Fallback | DashScope/model fallback plus safe citation fallback | model-service and tool errors | callbacks/events can control flow | **ADOPT** one provider policy; keep evidence fallback deterministic |
| Configuration | `Settings`, environment parsing, KB runtime settings, feature hashes | `llm_cfg` propagated into model/Agent | Runner/App/RunConfig/Context | **KEEP**, centralize only provider config |
| Error Mapping | `AppError`, public error shapes, provider exceptions at several layers | `ModelServiceError`, `ToolServiceError` | runner/plugin callbacks and typed errors | **ADOPT** provider error taxonomy |
| State | DB KB/Generation state; request-local result; browser Pinia chat state | message list and agent memory | Session state plus event persistence | **KEEP** split; do not add generic state store now |
| Memory | bounded conversation history; no autonomous long-term memory | memory/RAG components available | `MemoryService` separate from Session | **DEFER/REJECT** for current industrial QA |
| Evaluation | Golden Set, Ragas, frozen snapshots and trace replay | DeepPlanning/agent examples | ADK evaluation support | **KEEP** current evaluator; upstream evaluation ideas DEFER |

## 7. KEEP / ADOPT / DEFER / REJECT

### KEEP

1. Generation-scoped retrieval and citation lineage.
2. Query rewrite as a bounded, evidence-neutral preprocessing stage.
3. Deterministic grounding and structured citation validation outside the model.
4. Explicit refusal/partial-answer semantics.
5. Runtime manager and thread/async lifecycle separation.
6. Existing trace/evaluation artifacts as the authority for QA quality.

### ADOPT

Only the three patterns in Section 8. They are proposals, not implementation in this audit.

### DEFER

- Persistent Session/Memory abstraction: useful for multi-turn products, but current Vue history is intentionally local and current QA quality depends more on evidence correctness than durable agent memory.
- Tool registry and tool confirmation: reserve for a future bounded enterprise API, not retrieval or source verification.
- General event-sourced runtime: useful for large agent workflows, but current trace already captures the domain lineage needed for QA.
- Evaluation integration borrowed from upstream frameworks: current Ragas/Golden/frozen-snapshot contract is frozen and should not be redesigned.

### REJECT

- Multi-Agent or Supervisor: no current business requirement; adds routing and provenance surfaces.
- Autonomous Planner: industrial QA needs deterministic retrieval and refusal, not open-ended task planning.
- Infinite ReAct loop: increases latency and makes evidence/citation completeness harder to prove.
- LangGraph or a new workflow engine: duplicates the current explicit pipeline without solving the current provider pain.
- Agent Swarm, browser agent, code execution agent and broad MCP adoption: outside the product scope and increases security/governance risk.

## 8. Top 3 High-ROI Patterns

### #1 Unified Provider Adapter / Gateway

**Problem**：provider errors, fallback, timeout, model selection and OpenAI-compatible quirks are currently spread across LightRAG integration, settings, runtime paths and evaluation scripts.

**Current implementation**：`Settings` already exposes primary/fallback models and timeout values; `LightRAGService` owns provider-facing generation; semantic evaluation has its own provider preflight and compatibility handling. This works, but policy is not one reusable boundary.

**Upstream pattern**：Qwen-Agent puts provider/model config, registry, message normalization, streaming, retry and `ModelServiceError` behind `BaseChatModel`.

**Why upstream uses it**：high-level Agent code should not know whether the model is DashScope, OpenAI-compatible or another provider, and retry behavior must respect streaming mode.

**Minimal adaptation**：introduce one internal `ProviderGateway`/adapter interface for production LLM calls only. It should own model selection, request config, timeout, retryable classification, fallback order and normalized provider error. It must return the existing project-level answer/trace inputs and must not own retrieval, grounding or citations.

**Files potentially affected**：initially `lightrag_service.py`, provider/model helper module, `config.py`, and focused provider tests. Keep the change below five core production modules.

**Expected benefit**：provider resilience, fewer scattered `try/except` branches, consistent trace/error mapping and easier provider substitution.

**Complexity**：MEDIUM
**Risk**：MEDIUM
**User-visible benefit**：HIGH when a provider degrades; otherwise LOW
**Resume/interview value**：HIGH
**Do now?**：YES, after a small design/test task; do not introduce Qwen-Agent dependency.

### #2 Small Frozen QueryExecutionContext

**Problem**：`kb_id`, `generation_id`, request/trace identity, rewrite metadata, runtime settings and deadlines cross several layers. Passing them separately is workable today but makes future stage additions prone to omission or inconsistent trace identity.

**Current implementation**：`QueryApplicationService` creates/receives many of these values and enriches retrieval trace; `api.py` separately creates request/trace IDs; `LightRAGService` carries Generation/provider metadata in result and trace structures.

**Upstream pattern**：ADK creates an `InvocationContext` for one invocation, linking invocation identity, user content, Session, agent path/state and termination/call limits. `Runner` owns execution around that context.

**Why upstream uses it**：a bounded context prevents every agent/tool layer from rediscovering session and runtime state, and gives lifecycle/observability one identity.

**Minimal adaptation**：define a small immutable request/query context containing only `request_id`, `trace_id`, `kb_id`, `generation_id`, generation epoch, deadline/timeout and rewrite metadata. Do not include full history, provider client, full evidence text, DB session, runtime object or mutable global state. Pass it to stage functions that already need multiple identity values.

**Files potentially affected**：`api.py`, `services/query_application_service.py`, retrieval trace types/service and selected `LightRAGService` boundaries.

**Expected benefit**：less parameter drift, consistent lineage and easier stage-level tests.

**Complexity**：LOW-MEDIUM
**Risk**：MEDIUM if allowed to become a mutable God Object
**User-visible benefit**：LOW directly; MEDIUM through more reliable trace/error behavior
**Resume/interview value**：HIGH
**Do now?**：NO immediate production change; first document the field boundary and wait for another cross-stage feature.

### #3 Explicit Pipeline Stage Contract with Guardrail/Trace Hooks

**Problem**：the pipeline is already explicit in code, but new stages can add ad hoc metadata, exceptions or fallback behavior. A full callback rewrite would obscure the safety-critical evidence flow.

**Current implementation**：selection, completion, structured generation, grounding and citation code already expose strong typed/deterministic boundaries; trace assembly is concentrated in `LightRAGService`.

**Upstream pattern**：ADK plugins/callbacks provide before/after interception, global logging/monitoring/caching and short-circuit control; `InvocationContext` also provides bounded invocation control.

**Why upstream uses it**：cross-cutting concerns can be added without modifying every Agent implementation.

**Minimal adaptation**：define a narrow internal stage result/trace convention, for example `stage_name`, `status`, `duration_ms`, `failure_code`, `input_fingerprint`, `output_fingerprint` and `generation_id`. Add hooks only at deterministic boundaries: rewrite, retrieval, evidence selection, generation, grounding and citation binding. Hooks must observe/record and may enforce existing policy, but must not silently change evidence or add autonomous retries.

**Files potentially affected**：trace dataclasses/service, `QueryApplicationService`, `LightRAGService`, and existing grounding/citation modules.

**Expected benefit**：consistent observability and failure localization without adopting a workflow engine.

**Complexity**：MEDIUM
**Risk**：MEDIUM; callback overuse can hide control flow
**User-visible benefit**：LOW directly; MEDIUM for truthful degraded-state responses
**Resume/interview value**：MEDIUM-HIGH
**Do now?**：NO immediate production change; adopt only when the next pipeline stage requires shared instrumentation.

## 9. Things We Should NOT Copy

The following are explicitly out of scope for the current industrial QA product:

- **Multi-Agent**：not needed; one evidence-grounded QA path is the product.
- **Supervisor/Agent-to-Agent delegation**：adds routing and lineage complexity without a business task requiring delegation.
- **Planner**：not needed for a bounded document question; deterministic rewrite/retrieval is preferable.
- **LangGraph or another workflow engine**：the current pipeline already has explicit code boundaries; framework migration would be architecture churn.
- **Infinite ReAct loop**：conflicts with bounded latency, evidence completeness and safe refusal.
- **Complex workflow/fan-out/fan-in**：ADK supports it, but current QA does not need it.
- **Code execution / Browser Agent**：not part of industrial document QA and materially expands security scope.
- **Broad MCP server integration**：future enterprise tools may be bounded and authenticated, but broad MCP adoption is not a current requirement.
- **Autonomous long-term memory**：conversation history is not evidence; current product should not infer durable memory from chat.

## 10. Recommended Next Action

Do not begin a framework migration or broad architecture refactor.

The next architecture task, if one is authorized, should be a small proposal/test spike for **Unified Provider Adapter/Gateway**:

1. Inventory current provider call sites and error/fallback rules.
2. Define a narrow adapter contract and normalized error taxonomy.
3. Prove it with fake provider tests for timeout, HTTP 5xx, malformed output, primary success and fallback success.
4. Integrate only the production generation path first.
5. Preserve all current grounding, citation, Generation and evaluation contracts.

Until that spike demonstrates reduced duplication without changing answer behavior, the correct decision is **NO broad architectural change needed now**.

## 11. Source References

### Qwen-Agent

1. [Qwen-Agent README — model service, Agent construction, message history, RAG and tool calling](https://github.com/QwenLM/Qwen-Agent/blob/main/README.md)
2. [`qwen_agent/llm/base.py` — registry, BaseChatModel, config, message normalization, streaming and retry](https://github.com/QwenLM/Qwen-Agent/blob/main/qwen_agent/llm/base.py)
3. [`qwen_agent/agents/assistant.py` — Assistant RAG knowledge injection and Agent boundary](https://github.com/QwenLM/Qwen-Agent/blob/main/qwen_agent/agents/assistant.py)
4. [`qwen_agent/tools/base.py` — tool registry, JSON schema validation and BaseTool](https://github.com/QwenLM/Qwen-Agent/blob/main/qwen_agent/tools/base.py)
5. [Qwen-Agent `v0.0.26` release](https://github.com/QwenLM/Qwen-Agent/releases/tag/v0.0.26)

### Google ADK Python

1. [ADK README — modular runtime, workflows, tools, code-first design and model agnosticism](https://github.com/google/adk-python/blob/main/README.md)
2. [`runners.py` — Runner responsibilities and session/service coordination](https://github.com/google/adk-python/blob/54493140a6697af5b82e03b9d7ecb77c15df4eb6/src/google/adk/runners.py)
3. [`invocation_context.py` — InvocationContext identity, Session, state and call limits](https://github.com/google/adk-python/blob/54493140a6697af5b82e03b9d7ecb77c15df4eb6/src/google/adk/agents/invocation_context.py)
4. [`sessions/session.py` — Session state and ordered event history](https://github.com/google/adk-python/blob/54493140a6697af5b82e03b9d7ecb77c15df4eb6/src/google/adk/sessions/session.py)
5. [`plugins/base_plugin.py` — global plugin callback lifecycle and short-circuit semantics](https://github.com/google/adk-python/blob/54493140a6697af5b82e03b9d7ecb77c15df4eb6/src/google/adk/plugins/base_plugin.py)
6. [ADK official session/state guidance](https://github.com/google/adk-docs/blob/main/docs/sessions/state.md)
7. [ADK official callback guidance](https://github.com/google/adk-docs/blob/main/docs/callbacks/index.md)

### Current project

All current-project claims above are based on the audited checkout at HEAD `709d57b67221ffd8082b5b43ff35c7198d672433`; see the files listed in Section 2. No production code, tests, dependencies or evaluation configuration were modified for this audit.
