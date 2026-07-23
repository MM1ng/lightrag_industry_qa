"""Single-page Streamlit UI for the centrifugal-pump LightRAG knowledge base.

IMPORTANT – Windows event loop policy
=====================================
Windows defaults to ``ProactorEventLoop``, which is incompatible with
``asyncio.Lock`` objects shared across event loops.  LightRAG uses internal
locks inside its storage managers.  We **must** force ``SelectorEventLoop``
on Windows **before** importing ``streamlit`` (which imports ``uvicorn``
and creates the default event loop).

See: https://github.com/HKUDS/LightRAG/pull/2704


IMPORTANT – persistent background event loop
=============================================
LightRAG initializes persistent worker coroutines (embedding, LLM, health-check)
that must live on a single event loop for the entire server lifetime.  Using
``run_until_complete`` repeatedly creates/destroys temporary async contexts,
which corrupts the internal locks and triggers "locked to a different event
loop" on every query after the first.

Fix: a daemon thread runs ``loop.run_forever()``; all LightRAG work is
submitted via ``asyncio.run_coroutine_threadsafe``.  The loop never stops.
"""

from __future__ import annotations

import asyncio
import platform
import sys
import threading

if platform.system() == "Windows":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import time  # noqa: E402
from pathlib import Path  # noqa: E402

import streamlit as st  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from industrial_rag.config import (  # noqa: E402
    INDEX_METADATA_FILENAME,
    SUPPORTED_QUERY_MODES,
    Settings,
)
from industrial_rag.lightrag_service import (  # noqa: E402
    LightRAGService,
    QueryMode,
    QueryResult,
)

# ---------------------------------------------------------------------------
# Persistent  background  event-loop  singleton
# ---------------------------------------------------------------------------

_MODULE = sys.modules[__name__]

EXAMPLE_QUESTIONS = (
    "离心泵启动前需要检查什么？",
    "轴承温度过高可能是什么原因？",
    "水泵不输送液体应该如何排查？",
    "维修水泵前需要执行哪些安全步骤？",
    "气蚀产生的原因和危害是什么？",
    "机械密封失效有哪些可能原因？",
)


def _get_bg_state() -> dict | None:
    """Return the module-level state dict or None if not yet created."""
    state: dict | None = getattr(_MODULE, "_bg_state", None)
    if state is None:
        return None
    loop: asyncio.AbstractEventLoop = state["loop"]
    if loop.is_closed():
        return None
    return state


def _ensure_bg_state(settings: Settings) -> dict:
    """Create (if needed) a daemon thread that runs `loop.run_forever()`,
    initialize LightRAG on it, and cache everything on the module."""
    existing = _get_bg_state()
    if existing is not None:
        return existing

    loop = asyncio.new_event_loop()

    # Communicate between threads via a simple future.
    ready: asyncio.Future[LightRAGService] = asyncio.Future(loop=loop)

    def _bg_thread():
        asyncio.set_event_loop(loop)
        loop.run_until_complete(ready)  # blocks until _init_service completes
        loop.run_forever()  # never returns

    threading.Thread(target=_bg_thread, daemon=True, name="lightrag-loop").start()

    async def _init_service() -> LightRAGService:
        svc = LightRAGService(settings)
        await svc.initialize()
        return svc

    init_coro = _init_service()
    future = asyncio.run_coroutine_threadsafe(init_coro, loop)
    svc = future.result(timeout=180)  # wait for init to finish on bg thread

    state = {"loop": loop, "svc": svc}
    _MODULE._bg_state = state
    # Signal the bg thread that init is done (the `ready` future is internal only,
    # we already have the result so it's fine to just let gg collect it).
    ready.set_result(svc)
    return state


def _ask_sync(
    settings: Settings, question: str, mode: QueryMode
) -> tuple[QueryResult, float]:
    state = _ensure_bg_state(settings)
    loop: asyncio.AbstractEventLoop = state["loop"]
    svc: LightRAGService = state["svc"]

    async def _query():
        return await svc.query(question, mode=mode)

    start = time.perf_counter()
    future = asyncio.run_coroutine_threadsafe(_query(), loop)
    result = future.result(timeout=180)
    elapsed = time.perf_counter() - start
    return result, elapsed


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

st.set_page_config(page_title="工业离心泵知识库问答", page_icon="🔧", layout="centered")
st.title("基于 LightRAG 的工业离心泵知识库问答系统")

try:
    settings = Settings.from_env()
except Exception as error:
    settings = None
    st.error(f"配置错误：{error}")

if settings is not None:
    st.caption(
        f"模型：{settings.llm_model} ｜ Embedding："
        f"{settings.embedding_model}（{settings.embedding_dim} 维）"
    )
    marker = settings.working_dir / INDEX_METADATA_FILENAME
    st.info("LightRAG 状态：索引已就绪" if marker.is_file() else "LightRAG 状态：尚未导入文档")

mode = st.selectbox("查询模式", SUPPORTED_QUERY_MODES, index=0)
st.write("示例问题")
columns = st.columns(2)
for index, example in enumerate(EXAMPLE_QUESTIONS):
    if columns[index % 2].button(
        example, key=f"example-{index}", use_container_width=True
    ):
        st.session_state["question"] = example

question = st.text_area(
    "请输入问题",
    key="question",
    placeholder="例如：离心泵启动前需要检查什么？",
    height=100,
)

if st.button("提交问题", type="primary", use_container_width=True):
    if settings is None:
        st.error("请先修正环境配置。")
    elif not question.strip():
        st.error("请输入问题后再提交。")
    else:
        try:
            with st.spinner("正在检索两份离心泵手册……"):
                result, elapsed = _ask_sync(settings, question.strip(), mode)
            st.caption(f"⏱ 查询耗时：{elapsed:.2f} 秒")
            st.subheader("回答")
            st.write(result.answer)
            st.subheader("引用来源")
            if result.citations:
                for citation in result.citations:
                    st.write(citation.display)
            else:
                st.info("本次回答没有可验证的手册页码来源。")
        except Exception as error:
            st.error(f"查询失败：{error}")
