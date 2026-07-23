"""Single-page Streamlit UI for the centrifugal-pump LightRAG knowledge base."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from industrial_rag.config import (  # noqa: E402
    INDEX_METADATA_FILENAME,
    SUPPORTED_QUERY_MODES,
    Settings,
)
from industrial_rag.lightrag_service import LightRAGService, QueryMode  # noqa: E402

EXAMPLE_QUESTIONS = (
    "离心泵启动前需要检查什么？",
    "轴承温度过高可能是什么原因？",
    "水泵不输送液体应该如何排查？",
    "维修水泵前需要执行哪些安全步骤？",
    "气蚀产生的原因和危害是什么？",
    "机械密封失效有哪些可能原因？",
)

# Persistent event loop and service — LightRAG has module-level asyncio locks that
# must stay on the same loop across Streamlit reruns.
_loop: asyncio.AbstractEventLoop | None = None
_service: LightRAGService | None = None


def _get_or_create_loop() -> asyncio.AbstractEventLoop:
    global _loop
    if _loop is None or _loop.is_closed():
        _loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_loop)
    return _loop


async def _ensure_service(settings: Settings) -> LightRAGService:
    global _service
    if _service is None:
        _service = LightRAGService(settings)
        await _service.initialize()
    return _service


def _ask_sync(settings: Settings, question: str, mode: QueryMode):
    loop = _get_or_create_loop()
    service = loop.run_until_complete(_ensure_service(settings))
    return loop.run_until_complete(service.query(question, mode=mode))


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
                result = _ask_sync(settings, question.strip(), mode)
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
