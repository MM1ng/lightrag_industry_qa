"""Single-page Streamlit UI for the centrifugal-pump LightRAG knowledge base.

IMPORTANT - Windows event loop policy
=====================================
Windows defaults to ``ProactorEventLoop``, which is incompatible with
``asyncio.Lock`` objects shared across event loops.  LightRAG uses internal
locks inside its storage managers.  We **must** force ``SelectorEventLoop``
on Windows **before** importing ``streamlit`` (which imports ``uvicorn``
and creates the default event loop).

See: https://github.com/HKUDS/LightRAG/pull/2704


IMPORTANT - single event loop runtime
=====================================
All LightRAG async operations run on one daemon background thread with
one persistent event loop.  The ``LightRAGRuntime`` class (see
``src/industrial_rag/runtime.py``) owns the thread, loop, and service.
``st.cache_resource`` caches exactly ONE runtime per Streamlit process.
"""

from __future__ import annotations

import asyncio
import platform
import sys

if platform.system() == "Windows":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from pathlib import Path

import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from industrial_rag.config import (  # noqa: E402
    INDEX_METADATA_FILENAME,
    SUPPORTED_QUERY_MODES,
    Settings,
)
from industrial_rag.lightrag_service import (  # noqa: E402
    QueryMode,
    QueryResult,
)
from industrial_rag.runtime import LightRAGRuntime  # noqa: E402

# ---------------------------------------------------------------------------
# Cached runtime — one bg thread + one event loop per Streamlit process
# ---------------------------------------------------------------------------

EXAMPLE_QUESTIONS = (
    "离心泵启动前需要检查什么？",
    "轴承温度过高可能是什么原因？",
    "水泵不输送液体应该如何排查？",
    "维修水泵前需要执行哪些安全步骤？",
    "气蚀产生的原因和危害是什么？",
    "机械密封失效有哪些可能原因？",
)


@st.cache_resource(show_spinner=False)
def _get_runtime(_settings: Settings) -> LightRAGRuntime:
    """Cache exactly ONE LightRAGRuntime per Streamlit process.

    ``_settings`` is prefixed with ``_`` so Streamlit does NOT hash it
    for the cache key.  The cache returns the same runtime instance for
    the entire process lifetime.  Config changes require a Streamlit restart.
    """
    return LightRAGRuntime(_settings)


def _ask_sync(settings: Settings, question: str, mode: QueryMode) -> tuple[QueryResult, float]:
    """Execute a LightRAG query through the cached runtime."""
    runtime = _get_runtime(settings)
    return runtime.query(question, mode=mode)


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
    if columns[index % 2].button(example, key=f"example-{index}", use_container_width=True):
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
