# Chatbot UI Refresh — 完整实施计划 (v4, 最终版)

> **分支**: `feature/chatbot-ui-refresh` (从 `feature/lightrag-qa-mvp` 切出)
> **基线提交**: `5290ddf` Merge pull request #1 from feature/knowledge-graph-visualization
> **日期**: 2026-07-24
> **目标**: 将当前单次查询 UI 改造为连续对话界面，Apple 式克制 + 工业运维信息密度
> **实施模式**: apple-design → frontend-design → TDD → Codex
> **本阶段**: 仅设计计划，不修改代码

---

## 一、真实代码事实 (2026-07-24 基线)

### 1.1 `industrial_rag/__init__.py` — 立即导入副作用

```python
from industrial_rag.config import Settings
from industrial_rag.lightrag_service import LightRAGService
```

此文件在**任何** `from industrial_rag.xxx import ...` 时执行。`LightRAGService` 导入会触发 `lightrag_service` 模块加载（进而导入 `document_parser`、`config` 等核心模块）。`lightrag` 和 `openai` 的实际导入位于 `build_official_backend()` 函数内部，不在模块顶层。

**结论**: 任何放入 `src/industrial_rag/` 的模块，即使仅作为 `from industrial_rag.chat_state import ...` 导入，都会先执行 `__init__.py` 并加载核心包模块。UI 会话状态不应依赖核心包初始化。

### 1.2 `Citation` — 纯数据，但经过 `__init__.py`

```python
# citation_formatter.py — 仅 stdlib 依赖
@dataclass(frozen=True, slots=True)
class Citation:
    source_file: str
    page_number: int
    chunk_id: str
```

`Citation` 本身无副作用依赖，但 `from industrial_rag.citation_formatter import Citation` 仍会先执行 `__init__.py`。

### 1.3 `QueryResult` — 无 status 字段

```python
@dataclass(frozen=True, slots=True)
class QueryResult:
    answer: str
    citations: tuple[Citation, ...]
    mode: QueryMode
```

没有独立的 `status` 属性。证据不足仅通过 `answer == INSUFFICIENT_EVIDENCE_MESSAGE` 判断。

### 1.4 `INSUFFICIENT_EVIDENCE_MESSAGE`

```python
INSUFFICIENT_EVIDENCE_MESSAGE = "手册中未检索到充分依据，无法可靠回答该问题。"
```

LightRAGService 在两种情况下可能返回该文本：无证据无 citations；或检索到证据但模型答案为空，Service 将空答案替换为该固定文本。因此判定证据不足时不应额外要求 `citations == ()`。

此常量在 `streamlit_app.py` 中可导入（已有 `lightrag_service` 导入）。`chat_state.py` 不得导入它。

### 1.5 当前 `streamlit_app.py` — 327 行，单次查询覆盖

核心结构:
- 第 27-38 行: `WindowsSelectorEventLoop` 策略（必须在 streamlit 导入前）
- 第 41-51 行: `from industrial_rag import ...`（保持不变）
- 第 57-64 行: `EXAMPLE_QUESTIONS` / 第 66-77 行: `ENTITY_SEARCH_EXAMPLES`
- 第 80-88 行: `_get_runtime` (`st.cache_resource`)
- 第 91-94 行: `_ask_sync`
- 第 97-101 行: `_load_graph_cached` (`st.cache_data`)
- 第 104-110 行: `_get_graph`
- 第 118-160 行: `_render_qa_tab` — text_area + button + 单次答案
- 第 163-303 行: `_render_graph_tab` — 完整图谱逻辑
- 第 310-326 行: page shell (tab 编排)

**这些代码行将在增量重构中保留，不会被整文件推倒。**

### 1.6 Ruff 配置

当前 `include` 是显式文件列表。新增 `app/chat_state.py`、`app/ui_theme.py`、`app/__init__.py`、`tests/test_chat_state.py` 不会被 `ruff check .` 覆盖。需修改 `include`。

### 1.7 测试基线

基线和回归测试必须在已安装项目依赖的 `industrial-rag` Conda 环境中执行。环境外缺少 pymupdf 或 pytest 插件不属于本功能代码问题。`pyproject.toml` 中 `pytest-asyncio>=1.0,<2` 与 `asyncio_mode = "strict"` 是项目已有配置，本次不修改。

### 1.8 分支状态

- 当前分支: `feature/lightrag-qa-mvp` (远端 HEAD)
- `feature/chatbot-ui-refresh` 分支尚不存在
- 以下文件自 `5290ddf` 起无未提交 diff: `runtime.py`, `lightrag_service.py`, `citation_formatter.py`, `config.py`, `__init__.py`

### 1.9 现有 session_state key

| key | 用途 |
|-----|------|
| `qa_mode` | 查询模式下拉 |
| `question` | 文本区内容 (可逐步弃用) |
| `entity_query` | 图谱实体搜索 |
| `graph_mode` | 图谱展示模式 |
| `show_edge_labels` | 图谱边标签 |
| `show_all_labels` | 图谱节点标签 |
| `entity_match` | 图谱实体匹配 |
| `hops` | 图谱跳数 |
| `overview_limit` | 图谱节点限制 |
| `reload_graph` | 图谱重载 |

---

## 二、架构方案对比

### 方案 A: 核心包方案

将 `chat_state.py` 放入 `src/industrial_rag/`，修改 `__init__.py` 为轻量导入或延迟加载。

| 优点 | 缺点 |
|------|------|
| 可与 Citation 类型天然复用 | 必须修改 `__init__.py`，影响所有 import 路径 |
| | 破坏现有 `__all__` 语义 |
| | 回滚需要恢复 `__init__.py` |
| | 与现有知识图谱导入路径耦合 |

### 方案 B: UI 边界隔离方案 (✓ 选择)

将 `chat_state.py` 放入 `app/` 目录，定义独立的 `ChatCitation` 快照，在 UI 边界转换。

| 优点 | 缺点 |
|------|------|
| 不修改 `industrial_rag/` 下任何文件 | `ChatQueryMode` 与 `QueryMode` 临时重复定义 |
| `chat_state.py` 真正做到零工业依赖 | 需在 UI 边界做 Citation→ChatCitation 转换 |
| 回滚极其简单（删除新文件 + revert streamlit_app.py） | |
| 对 Runtime/知识图谱 零风险 | |
| 后续若需跨前端复用，再迁移为独立轻量包 | |

**选择方案 B。** 理由：
1. 当前功能属于 Streamlit 会话展示状态，不是 LightRAG 核心领域
2. `app/` 作为独立 UI 命名空间，语义清晰，不与 `src/` 共享 `sys.path`
3. UI 会话状态不应依赖 `industrial_rag` 核心包初始化
4. 对现有测试零影响
5. `ChatQueryMode` 的重复定义是本阶段可接受的技术债

---

## 三、最终文件结构

```
app/
├── __init__.py          # 新建，包标记，不导入任何模块
├── chat_state.py        # 新建，~230 行，纯 Python 消息模型和状态函数
├── ui_theme.py          # 新建，~120 行，CSS Token + 注入
└── streamlit_app.py     # 修改，~530 行 (从 327 行增长)

tests/
├── test_chat_state.py   # 新建，~280 行，纯单元测试
├── test_runtime.py      # 不修改
├── test_lightrag_service.py  # 不修改
├── test_citation_formatter.py  # 不修改
├── test_document_parser.py     # 不修改
└── test_graph_visualizer.py    # 不修改

pyproject.toml           # 最小修改：仅 [tool.ruff] include
```

### 文件职责

#### `app/__init__.py` (新建)

```python
"""Streamlit UI package for the industrial centrifugal-pump knowledge assistant."""
```

纯包标记，不导入任何模块，不暴露 `__all__`。

#### `app/chat_state.py` (新建, ~230 行, 纯 Python, 零外部依赖)

```
依赖:
  __future__ (annotations)
  collections.abc (Iterable, Sequence)
  dataclasses (dataclass, field)
  datetime (datetime, timezone, timedelta)
  math (isnan, isinf)
  typing (Literal)
  uuid (uuid4)

不依赖:
  streamlit
  industrial_rag (任何子模块)
  lightrag / openai / pymupdf
```

#### `app/ui_theme.py` (新建, ~120 行)

```
依赖:
  streamlit (st.markdown for CSS injection)

不依赖:
  industrial_rag
  Runtime / LightRAGService
  app.chat_state (不持有业务状态)
```

#### `app/streamlit_app.py` (增量修改, ~530 行)

保留所有现有代码结构。仅替换问答区域的渲染逻辑和提交逻辑。文件内函数定义顺序严格遵循:

1. imports + Windows event loop policy + sys.path
2. 常量
3. cache 函数 (`_get_runtime`, `_load_graph_cached`, `_get_graph`, `_ask_sync`)
4. helper 函数 (`_get_status_bar_graph_stats`, `_determine_status`, `_safe_error_text`)
5. chat 提交函数 (`_submit_question`)
6. render 函数 (`_render_status_bar`, `_render_chat_history`, `_render_assistant_message`, `_render_message_meta`, `_render_citations`, `_render_empty_state`)
7. graph render 函数 (`_render_graph_tab`)
8. page shell 顶层执行

**所有函数必须在顶层调用前定义，不存在前向引用。**

#### `tests/test_chat_state.py` (新建, ~280 行)

```
依赖:
  app.chat_state
  pytest

不依赖:
  streamlit
  industrial_rag
  网络 / API / 文件系统
```

---

## 四、聊天数据模型 (`app/chat_state.py`)

### 4.1 类型别名和运行时常量

```python
from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

ChatQueryMode = Literal["mix", "hybrid", "local", "global", "naive"]
MessageStatus = Literal["success", "insufficient_evidence", "error"]

# 运行时验证常量 — Literal 仅提供静态类型，不提供运行时检查
SUPPORTED_CHAT_QUERY_MODES: frozenset[str] = frozenset(
    {"mix", "hybrid", "local", "global", "naive"}
)
SUPPORTED_MESSAGE_STATUSES: frozenset[str] = frozenset(
    {"success", "insufficient_evidence", "error"}
)
```

### 4.2 ChatCitation — 不可变引用快照

```python
@dataclass(frozen=True, slots=True)
class ChatCitation:
    """UI 层不可变引用快照。不依赖 industrial_rag.citation_formatter.Citation。

    在 streamlit_app.py 边界从 Citation 转换:
        ChatCitation(source_file=c.source_file, page_number=c.page_number, chunk_id=c.chunk_id)
    """
    source_file: str
    page_number: int
    chunk_id: str

    def __post_init__(self) -> None:
        # source_file: 去除首尾空白后不能为空
        if not isinstance(self.source_file, str) or not self.source_file.strip():
            raise ValueError("source_file 不能为空")
        object.__setattr__(self, "source_file", self.source_file.strip())
        # page_number: 必须是 int、不能是 bool、且 > 0
        if isinstance(self.page_number, bool) or not isinstance(self.page_number, int):
            raise TypeError("page_number 必须为 int（不能为 bool）")
        if self.page_number < 1:
            raise ValueError("page_number 必须为正整数")
        # chunk_id: 去除首尾空白后不能为空
        if not isinstance(self.chunk_id, str) or not self.chunk_id.strip():
            raise ValueError("chunk_id 不能为空")
        object.__setattr__(self, "chunk_id", self.chunk_id.strip())

    @property
    def display(self) -> str:
        """与 Citation.display 格式一致: [文件名，第X页]"""
        return f"[{self.source_file}，第{self.page_number}页]"
```

### 4.3 UserMessage

```python
@dataclass(frozen=True, slots=True)
class UserMessage:
    """用户消息。role 由类型固定，不可覆盖。content 非空，规范化空白。"""
    content: str
    message_id: str = field(
        default_factory=lambda: uuid4().hex,
        init=False,
    )
    created_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    role: Literal["user"] = field(default="user", init=False)

    def __post_init__(self) -> None:
        _validate_and_normalize_content(self, "content")
        _validate_created_at(self)
```

### 4.4 AssistantMessage

```python
@dataclass(frozen=True, slots=True)
class AssistantMessage:
    """助手消息。每条消息独立保存自己的 mode/latency/citations/status。

    status="error" 允许 mode=None 和 latency_seconds=None。
    status="success" 和 "insufficient_evidence" 要求 mode 非空。
    """
    content: str
    mode: ChatQueryMode | None = None
    latency_seconds: float | None = None
    citations: tuple[ChatCitation, ...] = ()
    status: MessageStatus = "success"
    message_id: str = field(
        default_factory=lambda: uuid4().hex,
        init=False,
    )
    created_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    role: Literal["assistant"] = field(default="assistant", init=False)

    def __post_init__(self) -> None:
        _validate_and_normalize_content(self, "content")
        _validate_created_at(self)
        _validate_and_enforce_status(self)
        _validate_and_enforce_mode(self)
        _validate_latency(self)
        _enforce_tuple_citations(self)
        _validate_citation_items(self)

    @property
    def citation_count(self) -> int:
        return len(self.citations)
```

### 4.5 会话类型

```python
ChatMessage = UserMessage | AssistantMessage
ChatSession = list[ChatMessage]
```

### 4.6 统一不变量 (`__post_init__` 内使用 `object.__setattr__`)

由于 `frozen=True`，`__post_init__` 内修改字段值必须用 `object.__setattr__(self, field_name, value)`。

```python
def _validate_and_normalize_content(
    obj: UserMessage | AssistantMessage, field: str
) -> None:
    """规范化 content: 去除首尾空白，拒绝空内容/纯空白，保留 Markdown。"""
    raw = getattr(obj, field)
    if not isinstance(raw, str):
        raise TypeError(f"{field} 必须为字符串")
    cleaned = raw.strip()
    if not cleaned:
        raise ValueError("消息内容不能为空")
    if cleaned != raw:
        object.__setattr__(obj, field, cleaned)


def _validate_created_at(obj: UserMessage | AssistantMessage) -> None:
    """created_at 必须是带有效 timezone 的 datetime。"""
    dt = obj.created_at
    if not isinstance(dt, datetime):
        raise TypeError("created_at 必须为 datetime 对象")
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError("created_at 必须包含有效时区信息")


def _validate_and_enforce_status(msg: AssistantMessage) -> None:
    """status 必须属于 SUPPORTED_MESSAGE_STATUSES。"""
    if msg.status not in SUPPORTED_MESSAGE_STATUSES:
        raise ValueError(
            f"status 必须为 {sorted(SUPPORTED_MESSAGE_STATUSES)} 之一，"
            f"收到: {msg.status!r}"
        )


def _validate_and_enforce_mode(msg: AssistantMessage) -> None:
    """mode 为 None 或属于 SUPPORTED_CHAT_QUERY_MODES。
    success / insufficient_evidence 时 mode 必须非 None。"""
    if msg.mode is not None and msg.mode not in SUPPORTED_CHAT_QUERY_MODES:
        raise ValueError(
            f"mode 必须为 {sorted(SUPPORTED_CHAT_QUERY_MODES)} 之一，"
            f"收到: {msg.mode!r}"
        )
    if msg.status in ("success", "insufficient_evidence") and msg.mode is None:
        raise ValueError(f"status='{msg.status}' 时 mode 不能为 None")


def _validate_latency(msg: AssistantMessage) -> None:
    """latency 如果提供，必须为有限非负 float/int，不能为 bool。"""
    if msg.latency_seconds is None:
        return
    v = msg.latency_seconds
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise TypeError("latency_seconds 必须为数字（不能为 bool）")
    if math.isnan(v):
        raise ValueError("latency_seconds 不能为 NaN")
    if math.isinf(v):
        raise ValueError("latency_seconds 不能为 Infinity")
    if v < 0:
        raise ValueError("latency_seconds 不能为负数")


def _enforce_tuple_citations(msg: AssistantMessage) -> None:
    """确保 citations 字段始终为 tuple。"""
    current = msg.citations
    if not isinstance(current, tuple):
        object.__setattr__(msg, "citations", tuple(current or ()))


def _validate_citation_items(msg: AssistantMessage) -> None:
    """citations 中每一项必须是 ChatCitation 实例。"""
    for i, citation in enumerate(msg.citations):
        if not isinstance(citation, ChatCitation):
            raise TypeError(
                f"citations[{i}] 必须为 ChatCitation，收到: {type(citation).__name__}"
            )
```

**关键**: `add_assistant_message()` 和直接 `AssistantMessage(...)` 构造产生完全一致的校验结果。`__post_init__` 规范化所有输入，`add_*` 函数只作为便捷包装。

### 4.7 状态操作函数（纯函数，无原地修改）

```python
def create_empty_session() -> ChatSession:
    """返回新的空会话列表。"""
    return []


def add_user_message(
    session: Sequence[ChatMessage],
    content: str,
) -> tuple[ChatSession, UserMessage]:
    """追加用户消息。不修改原 session。"""
    msg = UserMessage(content=content)
    return [*session, msg], msg


def add_assistant_message(
    session: Sequence[ChatMessage],
    content: str,
    *,
    mode: ChatQueryMode | None = None,
    latency_seconds: float | None = None,
    citations: Iterable[ChatCitation] = (),
    status: MessageStatus = "success",
) -> tuple[ChatSession, AssistantMessage]:
    """追加助手消息。不修改原 session。

    citations 接受任何 Iterable，内部转换为 tuple 快照。
    """
    msg = AssistantMessage(
        content=content,
        mode=mode,
        latency_seconds=latency_seconds,
        citations=tuple(citations or ()),
        status=status,
    )
    return [*session, msg], msg


def add_error_message(
    session: Sequence[ChatMessage],
    content: str,
) -> tuple[ChatSession, AssistantMessage]:
    """错误消息快捷方式。复用 add_assistant_message 的统一校验。

    自动设置 status="error", mode=None, latency_seconds=None。
    """
    return add_assistant_message(
        session,
        content,
        mode=None,
        latency_seconds=None,
        citations=(),
        status="error",
    )


def clear_session() -> ChatSession:
    """返回空列表。不关闭 Runtime，不清理缓存。"""
    return []


def session_message_count(session: Sequence[ChatMessage]) -> int:
    """当前会话消息总数（用户 + 助手）。"""
    return len(session)
```

### 4.8 不可变性边界

- `UserMessage` 和 `AssistantMessage`: `frozen=True, slots=True` — 创建后字段不可再设
- `role`: `init=False` + 固定默认值 — 调用方无法覆盖
- `message_id`: `init=False` + 默认工厂 `uuid4().hex` — 调用方无法覆盖，创建时自动生成
- `created_at`: 默认工厂 `datetime.now(timezone.utc)` — 始终 UTC
- `citations`: `__post_init__` 强制转 `tuple` + 逐项验证 `ChatCitation` — 外部 list 修改不影响已存消息
- `ChatSession` (= `list[ChatMessage]`): 自身为可变 list
- 状态函数通过 `[*session, msg]` 创建新 list — 不修改传入 session

### 4.9 QueryMode 临时重复定义

`ChatQueryMode` 与 `lightrag_service.QueryMode` 字面量重复。这是有意设计:

- 避免 `app/` 导入 `industrial_rag` 包（触发 `__init__.py` 副作用链）
- 不修改 `lightrag_service.py` / `config.py`
- 本阶段可接受的技术债

一致性保障: `streamlit_app.py` 从 `SUPPORTED_QUERY_MODES` tuple 获取选项值传递给 `_ask_sync(mode=mode)`。`ChatQueryMode` 使用相同 5 个字符串。未来统一路径: 将 `QueryMode` 和 `SUPPORTED_QUERY_MODES` 移至独立共享模块。

### 4.10 证据不足状态映射

在 `streamlit_app.py` 中:

```python
from industrial_rag.lightrag_service import INSUFFICIENT_EVIDENCE_MESSAGE

def _determine_status(result: QueryResult) -> MessageStatus:
    """从 QueryResult 判定 MessageStatus — 精确字符串相等。

    LightRAGService 在两种情况下可能返回 INSUFFICIENT_EVIDENCE_MESSAGE:
    1. 无证据、无 citations
    2. 有 citations 但模型答案为空，Service 将空答案替换为固定文本
    因此不额外要求 len(result.citations) == 0。
    """
    if result.answer == INSUFFICIENT_EVIDENCE_MESSAGE:
        return "insufficient_evidence"
    return "success"
```

`chat_state.py` 不导入 `INSUFFICIENT_EVIDENCE_MESSAGE` — 状态判定逻辑属于 UI 边界层。

---

## 五、session_state 设计

### 5.1 键名

```
st.session_state keys:
  新增:
    "chat_session": ChatSession  — 从 create_empty_session() 初始化

  保留 (不变):
    "qa_mode", "question", "entity_query", "graph_mode",
    "show_edge_labels", "show_all_labels", "entity_match",
    "hops", "overview_limit", "reload_graph"
```

### 5.2 初始化

```python
if "chat_session" not in st.session_state:
    st.session_state["chat_session"] = create_empty_session()
```

### 5.3 Settings 不得存入 session_state

`Settings` 含有 `api_key`，不应无必要地存入 `session_state`。`settings` 始终通过函数参数传递:

```python
def _render_qa_tab(settings: Settings | None) -> None: ...
def _render_empty_state(settings: Settings | None, current_mode: QueryMode) -> None: ...
def _submit_question(settings: Settings | None, prompt: str, mode: QueryMode) -> None: ...
```

### 5.4 清空约束

```python
# 清空按钮回调 — 仅此操作
st.session_state["chat_session"] = clear_session()
st.rerun()

# 严禁:
# runtime.close()
# _get_runtime.clear()
# st.cache_resource.clear()
# _load_graph_cached.clear()
# 清理图谱状态
# 重建索引
```

### 5.5 持久性保证

`chat_session` 在以下操作后必须保留:
- Streamlit rerun
- 切换查询模式 (`qa_mode`)
- 打开/关闭引用 expander
- 切换 "智能问答" ↔ "知识图谱" tab
- 图谱重载按钮
- 图谱中切换展示模式/实体搜索

`st.session_state` 是 Streamlit 核心持久化机制，以上行为天然满足。仅需确保清空按钮以外的代码不意外修改 `chat_session`。

---

## 六、唯一查询提交路径

### 6.1 提交函数

只有一个提交函数。`st.chat_input` 和示例问题按钮使用同一入口。

**查询期间立即显示本轮用户消息**：`_submit_question` 在追加用户消息到 `session_state` 后，立即使用 `st.chat_message("user")` + `st.markdown` 即时渲染本轮用户消息，再进入 spinner 和 Runtime 阻塞调用。即时渲染仅存在于查询执行的当前 run；查询结束 rerun 后由历史列表重新渲染，不会永久重复。

```python
def _submit_question(
    settings: Settings | None,
    prompt: str,
    mode: QueryMode,
) -> None:
    """唯一查询提交路径。外部只能通过此函数提交问题。"""
    normalized = prompt.strip()
    if not normalized:
        return

    if settings is None:
        error_msg = "系统配置错误，请检查环境变量后重启应用。"
        session = st.session_state["chat_session"]
        new_session, user_msg = add_user_message(session, normalized)
        new_session, _ = add_error_message(new_session, error_msg)
        st.session_state["chat_session"] = new_session
        with st.chat_message("user"):
            st.markdown(user_msg.content)
        st.rerun()

    session = st.session_state["chat_session"]

    # Step 1: 追加用户消息
    new_session, user_msg = add_user_message(session, normalized)
    st.session_state["chat_session"] = new_session

    # Step 2: 即时渲染本轮用户消息
    with st.chat_message("user"):
        st.markdown(user_msg.content)

    # Step 3: 执行查询
    try:
        with st.spinner("正在检索离心泵手册……"):
            result, elapsed = _ask_sync(settings, normalized, mode)
        # Step 4: 转换引用 + 判定状态
        chat_citations = tuple(
            ChatCitation(
                source_file=c.source_file,
                page_number=c.page_number,
                chunk_id=c.chunk_id,
            )
            for c in result.citations
        )
        status = _determine_status(result)
        # Step 5: 追加助手消息
        new_session, _ = add_assistant_message(
            new_session,
            result.answer,
            mode=mode,
            latency_seconds=elapsed,
            citations=chat_citations,
            status=status,
        )
    except Exception as exc:
        # Step 6: 错误路径 — 用户消息已保留
        safe_text = _safe_error_text(exc, settings)
        new_session, _ = add_error_message(new_session, safe_text)

    # Step 7: 更新 session_state + rerun
    st.session_state["chat_session"] = new_session
    st.rerun()
```

### 6.2 异常安全性

```python
def _safe_error_text(exc: Exception, settings: Settings | None) -> str:
    """将异常转为安全错误消息。不泄露 API Key、不展示 traceback。"""
    text = str(exc).strip()
    if not text:
        return "查询过程中发生未知错误，请稍后重试。"
    if settings and settings.api_key:
        text = text.replace(settings.api_key, "***")
    if len(text) > 500:
        text = text[:500] + "…"
    return f"查询失败：{text}"
```

### 6.3 不使用 pending_prompt

`_submit_question()` 立即执行查询+即时渲染。不需要 `pending_prompt` 或"检测最后一条 user 后自动查询"的模式。无重复提交风险。

---

## 七、增量改造 `streamlit_app.py`

### 7.1 保留不变区域

以下代码块逐行保留，不做结构性修改:

| 区域 | 当前行号 (约) | 内容 |
|------|-------------|------|
| imports + event loop + sys.path | 25-51 | `import asyncio/streamlit` + SelectorEventLoop + `sys.path` + `from industrial_rag ...` |
| 常量 | 57-77 | `EXAMPLE_QUESTIONS`, `ENTITY_SEARCH_EXAMPLES` |
| `_get_runtime` | 80-88 | `st.cache_resource` — 绝不修改 |
| `_ask_sync` | 91-94 | Runtime 查询桥接 — 签名不变 |
| `_load_graph_cached` | 97-101 | `st.cache_data` — 绝不修改 |
| `_get_graph` | 104-110 | GraphML 加载辅助 — 不修改 |
| `_render_graph_tab` | 163-303 | 全部图谱逻辑 — 核心实现不动 |

### 7.2 替换区域

仅修改 `_render_qa_tab` (当前约第 118-160 行):

**旧代码删除**:
- `st.caption` 模型/索引信息 → 移至顶部状态栏
- `st.text_area("请输入问题", key="question", ...)` → 替换为 `st.chat_input`
- `st.button("提交问题")` 块 → 替换为 `_submit_question`
- `st.subheader("回答")` / `st.subheader("引用来源")` → 替换为消息渲染

### 7.3 显式导入路径保障

当前 `streamlit_app.py` 只向 `sys.path` 加入 `PROJECT_ROOT / "src"`。新增 `app/` 包后必须显式保证项目根目录可导入:

```python
PROJECT_ROOT = Path(__file__).resolve().parents[1]

for import_path in (PROJECT_ROOT, PROJECT_ROOT / "src"):
    resolved = str(import_path)
    if resolved not in sys.path:
        sys.path.insert(0, resolved)
```

在统一导入区（与其他 import 一起）导入 app 模块:

```python
from app.chat_state import (  # noqa: E402
    AssistantMessage,
    ChatCitation,
    ChatMessage,
    ChatSession,
    UserMessage,
    add_assistant_message,
    add_error_message,
    add_user_message,
    clear_session,
    create_empty_session,
    session_message_count,
)
from app.ui_theme import inject_theme_css  # noqa: E402
```

`# noqa: E402` 是必要的，因为这些导入在当前 styles 要求中应排在 E402（`module level import not at top of file`）之后。由于 `sys.path` 在 `import streamlit` 之前已完成配置，所有 app 导入放在统一导入区。

**不得**使用裸 `from chat_state import ...` — 这会与 `app.chat_state` 形成两个潜在模块身份。

**不得**在 page shell 顶层执行 area 临时写 `import`。

### 7.4 文件内函数定义顺序

最终 `app/streamlit_app.py` 必须严格遵循以下顺序（所有函数在顶层调用前定义）:

```
1. imports + Windows event loop policy + sys.path
2. 常量
3. cache 函数
   _get_runtime, _ask_sync, _load_graph_cached, _get_graph
4. helper 函数
   _get_status_bar_graph_stats, _determine_status, _safe_error_text
5. chat 提交函数
   _submit_question
6. render 函数
   _render_status_bar, _render_chat_history, _render_assistant_message,
   _render_message_meta, _render_citations, _render_empty_state
7. graph render 函数
   _render_graph_tab
8. _render_qa_tab
9. page shell 顶层执行
```

### 7.5 新增 helper 函数

```python
def _get_status_bar_graph_stats(settings: Settings | None) -> dict | None:
    """获取图谱统计用于状态栏展示。

    首次加载可能读取一次 GraphML（使用现有 _load_graph_cached 缓存）。
    后续 rerun 复用缓存。图谱读取失败时静默跳过统计，不影响问答。
    不调用 Runtime 或百炼 API。
    """
    if settings is None:
        return None
    try:
        path, graph = _get_graph(settings.working_dir)
    except Exception:
        return None
    if graph is None:
        return None
    try:
        return gv.get_graph_statistics(graph)
    except Exception:
        return None


def _determine_status(result: QueryResult) -> MessageStatus:
    """从 QueryResult 判定 MessageStatus — 精确字符串相等。

    LightRAGService 在有 citations 但模型答案为空时也会返回
    INSUFFICIENT_EVIDENCE_MESSAGE，因此不额外要求 citations 为空。
    """
    if result.answer == INSUFFICIENT_EVIDENCE_MESSAGE:
        return "insufficient_evidence"
    return "success"


def _safe_error_text(exc: Exception, settings: Settings | None) -> str:
    """安全错误消息 — 无 API Key、无完整 traceback。"""
    text = str(exc).strip()
    if not text:
        return "查询过程中发生未知错误，请稍后重试。"
    if settings and settings.api_key:
        text = text.replace(settings.api_key, "***")
    if len(text) > 500:
        text = text[:500] + "…"
    return f"查询失败：{text}"
```

### 7.6 新增 render 函数

```python
def _render_status_bar(settings: Settings | None, graph_stats: dict | None) -> None:
    """顶部紧凑状态栏 — 两个 tab 共享。显示真实数据，无硬编码。"""
    parts = ["🔧 工业离心泵知识库"]
    if settings is not None:
        parts.append(f"模型: {settings.llm_model}")
        marker = settings.working_dir / INDEX_METADATA_FILENAME
        parts.append("索引已就绪" if marker.is_file() else "索引未就绪")
    if graph_stats is not None:
        parts.append(f"节点: {graph_stats['node_count']}")
        parts.append(f"边: {graph_stats['edge_count']}")
    st.caption(" ｜ ".join(parts))


def _render_chat_history(session: ChatSession) -> None:
    """渲染对话历史 — 用户消息 + 助手消息 + 元信息 + 引用。"""
    for msg in session:
        if isinstance(msg, UserMessage):
            with st.chat_message("user"):
                st.markdown(msg.content)
        elif isinstance(msg, AssistantMessage):
            _render_assistant_message(msg)


def _render_assistant_message(msg: AssistantMessage) -> None:
    """渲染单条助手消息: 正文 + 元信息行 + 引用折叠区。"""
    with st.chat_message("assistant"):
        st.markdown(msg.content)
        _render_message_meta(msg)
        _render_citations(msg)


def _render_message_meta(msg: AssistantMessage) -> None:
    """紧凑元信息行。纯 Streamlit 原生组件。"""
    parts = []
    if msg.mode:
        parts.append(f"模式: {msg.mode}")
    if msg.latency_seconds is not None:
        parts.append(f"⏱ {msg.latency_seconds:.2f}s")
    parts.append(f"📎 {msg.citation_count}条引用")
    if msg.status == "insufficient_evidence":
        parts.append("⚠️ 证据不足")
    elif msg.status == "error":
        parts.append("❌ 查询失败")
    else:
        parts.append("✓ 成功")
    st.caption(" ｜ ".join(parts))


def _render_citations(msg: AssistantMessage) -> None:
    """折叠引用区域。默认折叠。逐条展示文件名、页码和 chunk_id。"""
    if not msg.citations:
        st.caption("本次回答没有可验证的来源。")
        return
    with st.expander(f"📎 引用来源（{msg.citation_count} 条）"):
        for i, citation in enumerate(msg.citations, start=1):
            st.markdown(f"**来源 {i}：{citation.source_file}**")
            st.caption(
                f"第 {citation.page_number} 页 · Chunk：{citation.chunk_id}"
            )


def _render_empty_state(settings: Settings | None, current_mode: QueryMode) -> None:
    """首次打开空状态 — 示例问题。settings 通过参数传递，不存入 session_state。"""
    st.markdown("### 你可以这样问我")
    cols = st.columns(2)
    for i, example in enumerate(EXAMPLE_QUESTIONS[:4]):
        if cols[i % 2].button(
            example, key=f"example-chat-{i}",
            use_container_width=True,
        ):
            _submit_question(settings, example, current_mode)
```

引用区域中文件名和 chunk_id 通过 `st.markdown` / `st.caption` 原生组件渲染，不拼接 `unsafe_allow_html`。长文件名和长 chunk_id 在 Streamlit markdown 容器内自然换行。

### 7.7 改造后的 `_render_qa_tab`

```python
def _render_qa_tab(settings: Settings | None) -> None:
    # 工具栏
    col1, col2 = st.columns([4, 1])
    with col1:
        mode = st.selectbox(
            "查询模式", SUPPORTED_QUERY_MODES,
            index=0, key="qa_mode",
        )
    with col2:
        if st.button("清空会话", use_container_width=True):
            st.session_state["chat_session"] = clear_session()
            st.rerun()

    # 对话历史
    session = st.session_state.get("chat_session", [])
    if session:
        _render_chat_history(session)
    else:
        _render_empty_state(settings, mode)

    # 底部输入
    if prompt := st.chat_input(
        placeholder="请输入离心泵运维问题，例如：离心泵启动前需要检查什么？"
    ):
        _submit_question(settings, prompt, mode)
```

### 7.8 Page shell

```python
st.set_page_config(page_title="工业离心泵知识库问答", page_icon="🔧", layout="wide")

# CSS 注入（inject_theme_css 已在 imports 区域导入）
inject_theme_css()

try:
    settings = Settings.from_env()
except Exception as error:
    settings = None
    st.error(f"配置错误：{error}")

# 初始化 chat_session
if "chat_session" not in st.session_state:
    st.session_state["chat_session"] = create_empty_session()

# 紧凑状态栏 (两个 tab 上方)
graph_stats = _get_status_bar_graph_stats(settings)
_render_status_bar(settings, graph_stats)

qa_tab, graph_tab = st.tabs(["智能问答", "知识图谱"])
with qa_tab:
    _render_qa_tab(settings)
with graph_tab:
    try:
        _render_graph_tab(settings)
    except Exception as error:
        st.error(f"知识图谱页面异常：{error}")
```

---

## 八、视觉设计系统 (`app/ui_theme.py`)

### 8.1 模块接口

```python
"""Streamlit UI theme — 设计 Token、CSS 注入、响应式布局。

不保存业务状态，不调用 Runtime，不处理 QueryResult。
"""

import streamlit as st


def inject_theme_css() -> None:
    """注入完整设计系统 CSS。在 st.set_page_config 之后调用一次。"""
    st.markdown(f"<style>{_build_css()}</style>", unsafe_allow_html=True)


def _build_css() -> str:
    """组装所有 CSS Token + 选择器覆盖。"""
    return _LIGHT_TOKENS + _DARK_TOKENS + _OVERRIDES
```

### 8.2 设计 Token (浅色)

```css
:root {
  --bg-page: #F5F5F7;
  --bg-surface: #FFFFFF;
  --text-primary: #1D1D1F;
  --text-secondary: #6E6E73;
  --text-tertiary: #AEAEB2;
  --border-light: #E5E5EA;
  --border-medium: #D1D1D6;
  --brand-accent: #2563EB;
  --brand-accent-subtle: rgba(37, 99, 235, 0.06);
  --color-success: #30B358;
  --color-warning: #F59E0B;
  --color-error: #EF4444;
  --radius-sm: 6px;
  --radius-md: 10px;
  --radius-lg: 16px;
  --shadow-sm: 0 1px 2px rgba(0,0,0,0.04);
  --max-content-width: 760px;
  --font-system: "Segoe UI", "Microsoft YaHei", -apple-system, BlinkMacSystemFont, sans-serif;
  --font-mono: "Cascadia Code", "Consolas", "Courier New", monospace;
}
```

### 8.3 暗色模式 Token

```css
/* Streamlit 暗色模式 Token。
   实际选择器需在浏览器中验证当前 Streamlit >=1.46 的 DOM 结构。
   以下为设计的语义方案 — 需要人工确认实际 DOM 后再微调选择器。 */
[data-theme="dark"],
.stApp[data-theme="dark"] {
  --bg-page: #1C1C1E;
  --bg-surface: #2C2C2E;
  --text-primary: #F5F5F7;
  --text-secondary: #AEAEB2;
  --text-tertiary: #6E6E73;
  --border-light: #3A3A3C;
  --border-medium: #545458;
  --brand-accent: #3B82F6;
  --brand-accent-subtle: rgba(59, 130, 246, 0.12);
}
```

### 8.4 主题识别策略

1. **优先**: 在 `:root` 中使用 `var(--st-background-color)` 和 `var(--st-text-color)` 作为回退值。
2. **暗色切换**: 通过浏览器 DevTools 验证 Streamlit >=1.46 暗色模式下 `<body>` 或根元素的 `data-theme` 属性。选择器具体写法依赖浏览器验证结果。
3. **降级**: 如果暗色选择器不可靠，暗色模式退化为仅依赖 Streamlit 原生 `--st-*` 变量 — 自定义 Token 使用原生变量派生而非硬编码。
4. **人工 + 浏览器验证**: 不声称"已测试对比度"。所有语义色在实施后需在深浅两种模式下人工检查。

### 8.5 CSS 覆盖范围

```css
/* 全局 */
.stMain { font-family: var(--font-system); }

/* 消息容器宽度限制 */
.stChatMessage { max-width: var(--max-content-width); margin: 0 auto; }

/* 元信息 */
.chat-meta { font-size: 0.8rem; color: var(--text-secondary); }

/* 状态栏 */
.status-bar { font-size: 0.8rem; color: var(--text-tertiary); padding: 8px 0; border-bottom: 1px solid var(--border-light); }

/* 响应式 */
@media (max-width: 768px) {
  .stChatMessage { max-width: 100%; }
  .status-bar { font-size: 0.75rem; }
}
```

### 8.6 UI 安全边界

`unsafe_allow_html=True` 仅用于:
1. `inject_theme_css()` — CSS `<style>` 注入，纯固定 Token，无动态内容
2. 图谱图例现有代码（保留不动）

以下使用 Streamlit 原生组件:
- `st.markdown` — 用户问题、模型回答、引用来源标题
- `st.caption` — 元信息行、页码 + chunk_id
- `st.expander` — 引用折叠区
- `st.chat_message` / `st.chat_input` — 对话界面
- `st.info` / `st.warning` / `st.error` — 状态提示

不实现 `get_status_badge_html()` — 状态通过 `st.caption` 原生文本表达，不拼接 HTML。

---

## 九、响应式方案

| 宽度 | 布局 | `max-content-width` |
|------|------|---------------------|
| < 768px | 全宽，控件堆叠 | 100% |
| 768px - 1366px | 居中 | 720px |
| ≥ 1366px | 居中 | 760px |

处理规则:
- `overflow-x: hidden` 防止横向滚动
- 长文件名: `word-break: break-all`
- 长回答: `overflow-wrap: break-word`
- `st.chat_input` 固定底部，min-height 56px
- 工具栏: flex + `flex-wrap: wrap`
- 按钮有文字，不纯图标
- 语义色 + 文字 + 图标三重编码状态

---

## 十、知识图谱视觉统一

仅统一外观，不修改核心逻辑:

- 标题间距与全局一致
- 控件 (selectbox, radio, checkbox) 通过全局 CSS 统一
- 指标 `st.metric` 样式统一
- 图例色块用 `--radius-sm`
- 图谱 HTML 不设 max-width（内容可宽于聊天区）
- 明暗模式下图谱背景跟随

**绝不修改**:
- GraphML 读取、子图构建、pyvis 渲染
- 实体搜索/匹配/跳数逻辑
- 工具提示内容、JS 交互代码

---

## 十一、Ruff 配置处理

### 决策: 将 include 改为可维护的范围模式

当前 `include` 是显式文件列表。新增文件不会被 `ruff check .` 覆盖。

**修改**: 仅 `[tool.ruff]` 下的 `include` 列表:

```toml
include = [
  "src/industrial_rag/**/*.py",
  "app/**/*.py",
  "tests/test_*.py",
  "scripts/inspect_environment.py",
  "scripts/parse_manuals.py",
  "scripts/ingest_documents.py",
  "scripts/smoke_test.py",
]
```

`tests/test_*.py` 仅匹配根目录 `tests/` 下的 MVP 测试文件，不扩大到 `tests/unit`、`tests/integration`、`tests/smoke`（这些目录被 pytest ignore 且包含遗留代码）。

**不修改** (pyproject.toml 其他部分):
- 依赖、Python 版本、构建配置
- `[tool.pytest.ini_options]` (ignore 列表和 asyncio_mode)
- `[tool.ruff.lint]` 和 `[tool.ruff.format]`
- `testpaths`
- 任何依赖版本

---

## 十二、TDD 实施顺序

### Step 0: 分支幂等准备

```bash
# 先在 conda industrial-rag 环境中检查当前状态
git status --short
git branch --show-current
git branch --list feature/chatbot-ui-refresh
git log --oneline -5

# 规则:
# - 已在 feature/chatbot-ui-refresh: 继续
# - 分支存在但不在该分支: git switch feature/chatbot-ui-refresh
# - 分支不存在: git checkout -b feature/chatbot-ui-refresh 5290ddf
# - 不删除、不覆盖任何现有分支
# - 工作区存在业务改动 (非 untracked docs/scripts/.claude 等): 停止并报告
```

### Step 1: 基线确认

```bash
# 在 conda industrial-rag 环境中:
python -m pytest -q
ruff check .
# → 确认基线通过。pytest 配置已包含 --ignore 参数，不需要手动重复。
```

### Step 2: 先写测试 → 红 (Red)

创建 `tests/test_chat_state.py`。覆盖第 13.1 节全部测试行为，零外部依赖。

直接导入 `app.chat_state`:

```python
# tests/test_chat_state.py
# pytest 默认将项目根加入 sys.path，app/ 可直接导入
from app.chat_state import (
    ChatCitation,
    UserMessage,
    AssistantMessage,
    ChatSession,
    SUPPORTED_CHAT_QUERY_MODES,
    SUPPORTED_MESSAGE_STATUSES,
    add_user_message,
    add_assistant_message,
    add_error_message,
    clear_session,
    session_message_count,
)
```

测试必须无 `importorskip`、无 skip、无弱断言。

### Step 3: 运行测试 → 确认红

```bash
python -m pytest tests/test_chat_state.py -q
# → FAILED (app.chat_state 尚不存在)
```

### Step 4: 实现 `app/__init__.py` + `app/chat_state.py` → 绿 (Green)

按第 4 节完整实现。

```bash
python -m pytest tests/test_chat_state.py -q
# → 全部通过
```

### Step 5: 修改 `pyproject.toml` Ruff include

按第 11 节修改，运行 `ruff check .` 确认通过。

### Step 6: 实现 `app/ui_theme.py`

按第 8 节实现。运行 `ruff check app/ui_theme.py`。

### Step 7: 增量修改 `app/streamlit_app.py`

按第 7 节实施:
1. 修改 sys.path 配置，添加 PROJECT_ROOT
2. 在统一导入区导入 `app.chat_state` 和 `app.ui_theme`
3. 新增 3 个 helper 函数
4. 新增 `_submit_question`
5. 新增 6 个 render 函数（引用区域展示 chunk_id）
6. 替换 `_render_qa_tab` 为连续对话版本
7. 调整 page shell
8. 图谱 tab 仅统一视觉（间距/字体通过全局 CSS），不修改逻辑

### Step 8: 全量验证

```bash
# 回归 + 新测试 (pytest 配置已包含 ignore)
git diff --check
python -m pytest --collect-only -q
python -m pytest -q
ruff check .
ruff format --check .

# 以下 8 条必须为空:
git diff -- src/industrial_rag/runtime.py
git diff -- src/industrial_rag/lightrag_service.py
git diff -- src/industrial_rag/citation_formatter.py
git diff -- src/industrial_rag/config.py
git diff -- src/industrial_rag/graph_visualizer.py
git diff -- src/industrial_rag/graph_display_mapping.py
git diff -- src/industrial_rag/document_parser.py
git diff -- src/industrial_rag/__init__.py
```

### Step 9: 启动 + 人工验收

```bash
streamlit run app/streamlit_app.py
# 按第 15 节清单逐项验证
```

### Step 10: 提交

```bash
git add app/__init__.py app/chat_state.py app/ui_theme.py app/streamlit_app.py
git add tests/test_chat_state.py
git add pyproject.toml
git add docs/superpowers/plans/2026-07-24-chatbot-ui-refresh.md
git commit -m "feat: chatbot UI refresh — continuous conversation with immutable chat state and Apple-inspired design tokens"
```

---

## 十三、测试清单

### 13.1 纯状态测试 — `tests/test_chat_state.py`

测试总数不写死，必须覆盖以下全部行为。

#### 依赖隔离 (4)

| # | 测试 | 验证内容 |
|---|------|---------|
| 1 | `test_module_has_no_streamlit_import` | `app.chat_state` 不导入 `streamlit`。使用 AST 检查或 subprocess 避免已导入模块缓存导致的误判 |
| 2 | `test_module_has_no_industrial_rag_import` | `app.chat_state` 不导入 `industrial_rag` |
| 3 | `test_module_has_no_runtime_import` | `app.chat_state` 不导入 Runtime |
| 4 | `test_module_has_no_lightrag_service_import` | `app.chat_state` 不导入 LightRAGService |

#### 不可变性 (7)

| # | 测试 | 验证内容 |
|---|------|---------|
| 5 | `test_user_message_role_not_overridable` | `UserMessage(role="assistant")` → TypeError |
| 6 | `test_assistant_message_role_not_overridable` | 同上 |
| 7 | `test_user_message_id_not_overridable` | `UserMessage(message_id="x")` → TypeError |
| 8 | `test_assistant_message_id_not_overridable` | 同上 |
| 9 | `test_each_message_has_unique_message_id` | 两条默认构造的消息 message_id 不同 |
| 10 | `test_message_id_immutable` | `msg.message_id = "x"` → FrozenInstanceError |
| 11 | `test_created_at_has_utc_timezone` | 默认 `created_at.tzinfo == timezone.utc` |

#### 内容校验 (4)

| # | 测试 | 验证内容 |
|---|------|---------|
| 12 | `test_content_empty_raises` | `content=""` → ValueError |
| 13 | `test_content_whitespace_only_raises` | `content="  \n  "` → ValueError |
| 14 | `test_content_strips_whitespace` | `content=" hello "` → `msg.content == "hello"` |
| 15 | `test_content_markdown_preserved` | `**文字**` 和 `\n` 保留内部格式 |

#### Citations 校验 (6)

| # | 测试 | 验证内容 |
|---|------|---------|
| 16 | `test_citations_list_converted_to_tuple` | 传入 `[c1, c2]` → `isinstance(msg.citations, tuple)` |
| 17 | `test_citations_source_list_mutation_no_effect` | 修改原 list → 消息内 tuple 不变 |
| 18 | `test_citations_rejects_non_chat_citation` | 传入非 ChatCitation → TypeError |
| 19 | `test_chat_citation_page_number_rejects_bool` | `ChatCitation(page_number=True, ...)` → TypeError |
| 20 | `test_chat_citation_normalizes_whitespace` | `source_file=" file.pdf "` → `"file.pdf"` |
| 21 | `test_chat_citation_preserves_chunk_id` | `ChatCitation(..., chunk_id="abc-123")` → `msg.citations[0].chunk_id == "abc-123"` |

#### Status / Mode 校验 (5)

| # | 测试 | 验证内容 |
|---|------|---------|
| 22 | `test_success_requires_mode` | `status="success", mode=None` → ValueError |
| 23 | `test_insufficient_evidence_requires_mode` | `status="insufficient_evidence", mode=None` → ValueError |
| 24 | `test_error_allows_none_mode` | `status="error", mode=None` 正常 |
| 25 | `test_invalid_status_rejected` | `status="fake_status"` → ValueError |
| 26 | `test_invalid_mode_rejected` | `mode="fake_mode"` → ValueError |

#### Latency 校验 (4)

| # | 测试 | 验证内容 |
|---|------|---------|
| 27 | `test_latency_negative_raises` | `latency_seconds=-1` → ValueError |
| 28 | `test_latency_nan_raises` | `latency_seconds=float("nan")` → ValueError |
| 29 | `test_latency_infinity_raises` | `latency_seconds=float("inf")` → ValueError |
| 30 | `test_latency_bool_rejected` | `latency_seconds=True` → TypeError |

#### Created_at 校验 (4)

| # | 测试 | 验证内容 |
|---|------|---------|
| 31 | `test_created_at_rejects_non_datetime` | `created_at="2026-01-01"` → TypeError |
| 32 | `test_created_at_rejects_naive_datetime` | `datetime(2026,1,1)` (无 tzinfo) → ValueError |
| 33 | `test_created_at_rejects_dangling_tzinfo` | `tzinfo` 非空但 `utcoffset()` 返回 None → ValueError |
| 34 | `test_created_at_accepts_non_utc_aware` | 带有效非 UTC offset 的 datetime 可通过 |

#### 操作语义 (4)

| # | 测试 | 验证内容 |
|---|------|---------|
| 35 | `test_original_session_not_mutated_by_add_user` | `add_user_message` 后原 session 不变 |
| 36 | `test_original_session_not_mutated_by_add_assistant` | 同上 |
| 37 | `test_clear_session_returns_empty_list` | `clear_session() == []` |
| 38 | `test_multi_round_message_order_preserved` | 3 轮问答 → 消息顺序 = [u1,a1,u2,a2,u3,a3] |

#### 集成行为 (2)

| # | 测试 | 验证内容 |
|---|------|---------|
| 39 | `test_historical_assistant_mode_not_affected_by_current_qa_mode` | 已有消息的 mode 不随外部变量变化 |
| 40 | `test_app_package_importable` | `import app.chat_state` 可从项目根目录正常导入 |

### 13.2 UI 行为验证 (人工 / Playwright, 18 项)

| # | 验证点 |
|---|--------|
| 1 | 首次打开为空状态 + 示例问题可见 |
| 2 | 点击示例问题 → 直接提交 (同一 `_submit_question`) |
| 3 | 查询期间立即显示本轮用户消息 |
| 4 | 连续三轮问答全部保留 |
| 5 | 每轮引用独立绑定到对应消息 |
| 6 | 引用区域展示文件名、页码和 chunk_id |
| 7 | 切换查询模式 → 旧消息 mode 不变 |
| 8 | 清空会话 → 不关闭 Runtime |
| 9 | 切换图谱 tab 再切回 → 聊天记录保留 |
| 10 | 图谱重载 → 聊天记录保留 |
| 11 | 查询失败 → 用户问题保留 |
| 12 | 错误信息不含 API Key / traceback |
| 13 | 无重复 API 调用 |
| 14 | 无 event loop 错误 |
| 15 | 图谱 P1 功能无回归 |
| 16 | 浅色 + 暗色模式可读 |
| 17 | 1366px / 768px 无横向溢出 |
| 18 | 长 chunk_id 安全展示，不溢出 |

---

## 十四、风险评估和回滚方案

### 14.1 风险矩阵

| 风险 | 概率 | 影响 | 缓解 |
|------|------|------|------|
| `app/` 包与现有导入路径冲突 | 低 | 中 | `app/` 不在 `src/` 下，通过 PROJECT_ROOT 加入 sys.path |
| `st.chat_message` API 行为与预期不符 | 低 | 中 | Streamlit >=1.46 稳定 API |
| CSS 覆盖 Streamlit 内联样式 | 中 | 低 | 优先使用特异性选择器，`!important` 为最后手段 |
| `chat_session` 在极端 rerun 场景丢失 | 低 | 高 | `st.session_state` 是 Streamlit 核心保障 |
| 图谱 JS 与新增 CSS 冲突 | 低 | 低 | CSS 限定在聊天区域选择器，不覆盖 `.vis-*` |

### 14.2 回滚方案

```bash
git checkout feature/lightrag-qa-mvp -- app/streamlit_app.py
rm app/__init__.py app/chat_state.py app/ui_theme.py tests/test_chat_state.py
git checkout pyproject.toml
```

回滚后:
- Runtime / LightRAGService / citation_formatter 零改动
- `st.cache_resource` / `st.cache_data` 不受影响
- 恢复 327 行单次查询 UI

---

## 十五、人工验收清单

### 功能验收 (19 项)

- [ ] 首次打开显示空状态 + 4 个示例问题
- [ ] 点击示例问题 → 直接提交并显示回答
- [ ] `st.chat_input` 输入问题 → 回答出现在对话区
- [ ] 查询期间立即显示本轮用户消息
- [ ] 连续 3 轮问答 → 6 条消息全部保留
- [ ] 每条助手消息: mode | latency | citation_count | status 可见
- [ ] 引用区域默认折叠，展开后展示文件名、页码和 chunk_id
- [ ] 清空会话 → 回到空状态
- [ ] 清空后重新提问 → Runtime 正常响应
- [ ] 清空后知识图谱 tab 正常工作
- [ ] 切换查询模式 → 新查询用新模式
- [ ] 旧消息中 mode 不变
- [ ] 切换 tab 再切回 → 聊天记录保留
- [ ] 图谱重载 → 聊天记录保留
- [ ] 查询失败 → 用户问题保留
- [ ] 错误信息不含 API Key
- [ ] 错误信息不含 Python traceback
- [ ] 证据不足回答 → 显示 "⚠️ 证据不足"
- [ ] 无引用回答 → 显示 "没有可验证的来源"

### 视觉验收 (8 项)

- [ ] 浅色模式 — 配色、对比度、留白正常
- [ ] 暗色模式 — 自动跟随，文本可读
- [ ] 1366px — 内容居中，无横向滚动
- [ ] 1920px — 内容居中
- [ ] 768px / 窄窗口 — 控件堆叠
- [ ] 长文件名换行
- [ ] 长 chunk_id 安全展示
- [ ] Markdown 格式正常渲染

### 测试验收 (4 项)

- [ ] chat_state 测试覆盖第 13.1 节全部行为
- [ ] 原有测试全部通过 (conda 环境中)
- [ ] 8 个核心服务文件零 diff
- [ ] `ruff check .` + `ruff format --check .` 通过

---

## 十六、严格禁止修改清单

### 文件级禁止修改

| 文件 | 原因 |
|------|------|
| `src/industrial_rag/runtime.py` | Runtime 稳定，单线程单 event loop |
| `src/industrial_rag/lightrag_service.py` | 查询逻辑、提示词、证据不足逻辑 |
| `src/industrial_rag/citation_formatter.py` | Citation 编码/解码/格式化 |
| `src/industrial_rag/config.py` | 设置、兼容性检查 |
| `src/industrial_rag/graph_visualizer.py` | 图谱渲染核心 |
| `src/industrial_rag/graph_display_mapping.py` | 实体映射 |
| `src/industrial_rag/document_parser.py` | 文档解析 |
| `src/industrial_rag/__init__.py` | 包初始化、导出 |
| `.env` / `.env.example` | 环境配置 |
| `lightrag_storage/` 下任何文件 | 索引数据 |
| `data/` 下任何文件 | 手册数据 |
| `tests/` 下现有 5 个测试文件 | 现有测试行为 |
| `scripts/` 下任何文件 | 脚本逻辑 |
| `config/` 下任何文件 | 合约配置 |

### 行为级禁止修改

- LightRAG 查询参数 (`top_k`, `chunk_top_k`, `enable_rerank`)
- 五种查询模式 (`mix`, `hybrid`, `local`, `global`, `naive`)
- 分块策略、Embedding 模型、百炼 API 配置
- `st.cache_resource` 和 `st.cache_data` 策略
- GraphML 生成、读取
- `_get_runtime` / `_ask_sync` / `_load_graph_cached` 签名
- pytest 配置 (`asyncio_mode`, `--ignore` 列表, `testpaths`)
- 任何依赖版本

### 依赖级禁止引入

- LangChain / LangGraph
- React / Next.js / FastAPI
- 外部 UI 框架
- 外部 CSS / 字体 CDN
- 外部图片

---

## 十七、Codex 实施摘要

> **项目**: 工业离心泵知识库智能助手 — Chatbot UI Refresh
> **基线**: `5290ddf` (feature/lightrag-qa-mvp)
> **目标分支**: `feature/chatbot-ui-refresh`
> **架构**: Scheme B — `app/` UI 边界隔离，不修改 `industrial_rag/`

### 变更文件

| 文件 | 操作 | 预估行数 |
|------|------|---------|
| `app/__init__.py` | **新建** | 1 行 |
| `app/chat_state.py` | **新建** | ~230 行 |
| `app/ui_theme.py` | **新建** | ~120 行 |
| `app/streamlit_app.py` | **增量修改** | ~530 行 (+203) |
| `tests/test_chat_state.py` | **新建** | ~280 行 |
| `pyproject.toml` | **最小修改** | Ruff include 改为范围模式 |

### 核心动作

1. **`app/chat_state.py`** — 纯 Python 消息模型
   - `UserMessage` / `AssistantMessage`: `frozen=True, slots=True`
   - `ChatCitation`: 不可变引用快照，零 `industrial_rag` 依赖
   - `SUPPORTED_CHAT_QUERY_MODES` / `SUPPORTED_MESSAGE_STATUSES`: `frozenset` 运行时验证
   - `role` `init=False`、`message_id` `init=False` — 防止调用方覆盖
   - `created_at` 校验: `isinstance(datetime)` + `tzinfo is not None and utcoffset() is not None`
   - `citations` 强制 tuple + 逐项 `ChatCitation` 类型验证
   - `latency` 拒绝 bool、NaN、Inf、负数
   - `page_number` 拒绝 bool
   - `from collections.abc import Iterable, Sequence` — Ruff UP 规则合规
   - 纯函数: `add_user_message`, `add_assistant_message`, `add_error_message`, `clear_session`

2. **`app/ui_theme.py`** — CSS 注入 + 明暗主题
   - CSS 变量设计 Token、浅色 + 暗色 Token 集
   - 暗色选择器需浏览器验证，提供降级方案
   - 响应式: 768px / 1366px / 1920px
   - `unsafe_allow_html` 仅用于 CSS；不实现 `get_status_badge_html()`

3. **`app/streamlit_app.py`** — 增量重构问答区域
   - 显式将 `PROJECT_ROOT` 加入 `sys.path` 保障 `app` 包可导入
   - `from app.chat_state import ...` 在统一导入区
   - 文件内严格函数定义顺序
   - 顶部紧凑状态栏 (真实数据)
   - `st.chat_message` + `st.chat_input` 连续对话
   - 查询期间立即渲染用户消息
   - 唯一 `_submit_question()` 提交路径
   - `settings` 始终通过函数参数传递，不存入 `session_state`
   - 证据不足仅基于精确字符串相等，不额外要求 `citations == ()`
   - 引用区域逐条展示文件名、页码和 chunk_id（`st.markdown` + `st.caption`）
   - 错误安全过滤 (无 API Key / no traceback)
   - 图谱 tab 视觉统一，核心逻辑不修改

4. **`tests/test_chat_state.py`** — TDD 红→绿
   - 至少 40 个纯单元测试，零外部依赖
   - 覆盖依赖隔离(4) + 不可变性(7) + content(4) + citations(6) + status/mode(5) + latency(4) + created_at(4) + 操作语义(4) + 集成行为(2)

### 关键约束

- 会话历史仅 UI 展示，不自动传入 LightRAG
- 清空会话 ≠ 关闭 Runtime
- `ChatQueryMode` 重复定义为可接受技术债
- `app/` 包与 `src/industrial_rag/` 零依赖
- `settings` 不含 `api_key` 入 `session_state`

### 最终验证命令

```bash
git diff --check
python -m pytest --collect-only -q
python -m pytest -q
ruff check .
ruff format --check .

# 以下 8 条必须为空:
git diff -- src/industrial_rag/runtime.py
git diff -- src/industrial_rag/lightrag_service.py
git diff -- src/industrial_rag/citation_formatter.py
git diff -- src/industrial_rag/config.py
git diff -- src/industrial_rag/graph_visualizer.py
git diff -- src/industrial_rag/graph_display_mapping.py
git diff -- src/industrial_rag/document_parser.py
git diff -- src/industrial_rag/__init__.py
```
