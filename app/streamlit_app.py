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

Graph visualization is fully separate: it only reads GraphML and never
touches LightRAGRuntime or the Bailian API.
"""

from __future__ import annotations

import asyncio
import platform
import sys
from pathlib import Path

if platform.system() == "Windows":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import streamlit as st
import streamlit.components.v1 as components

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from industrial_rag import graph_visualizer as gv  # noqa: E402
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

ENTITY_SEARCH_EXAMPLES = (
    "离心泵",
    "轴承",
    "机械密封",
    "叶轮",
    "气蚀",
    "润滑",
    "启动",
    "温度过高",
    "2196",
    "DESMI",
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


@st.cache_data(show_spinner=False)
def _load_graph_cached(graphml_path: str, mtime_ns: int, size: int):
    """Cache GraphML load keyed by path + mtime + size. Separate from runtime cache."""
    _ = mtime_ns, size
    return gv.load_graph(Path(graphml_path))


def _get_graph(working_dir: Path):
    path = gv.locate_graph_file(working_dir)
    if path is None:
        return None, None
    stat = path.stat()
    graph = _load_graph_cached(str(path.resolve()), stat.st_mtime_ns, stat.st_size)
    return path, graph


# ---------------------------------------------------------------------------
# UI sections
# ---------------------------------------------------------------------------


def _render_qa_tab(settings: Settings | None) -> None:
    if settings is not None:
        st.caption(
            f"模型：{settings.llm_model} ｜ Embedding："
            f"{settings.embedding_model}（{settings.embedding_dim} 维）"
        )
        marker = settings.working_dir / INDEX_METADATA_FILENAME
        st.info("LightRAG 状态：索引已就绪" if marker.is_file() else "LightRAG 状态：尚未导入文档")

    mode = st.selectbox("查询模式", SUPPORTED_QUERY_MODES, index=0, key="qa_mode")
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


def _render_graph_tab(settings: Settings | None) -> None:
    st.caption("只读展示 LightRAG 已生成的 GraphML 子集，不调用百炼 API，不修改图谱。")

    working_dir = (
        settings.working_dir if settings is not None else PROJECT_ROOT / "lightrag_storage"
    )
    graph_path = gv.locate_graph_file(working_dir)

    col_a, col_b = st.columns([3, 1])
    with col_a:
        if graph_path is None:
            st.warning(
                f"未找到图谱文件：`{working_dir / gv.GRAPHML_FILENAME}`。"
                "请先执行 `python scripts/ingest_documents.py` 导入手册。"
            )
        else:
            st.success(f"图谱文件：`{graph_path.name}`")
    with col_b:
        if st.button("重新加载图谱", use_container_width=True, key="reload_graph"):
            _load_graph_cached.clear()
            st.rerun()

    if graph_path is None:
        return

    try:
        path, graph = _get_graph(working_dir)
    except Exception as error:
        st.error(f"读取 GraphML 失败：{error}")
        return

    if graph is None or path is None:
        st.warning("图谱不可用。")
        return

    stats = gv.get_graph_statistics(graph)
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("总节点数", stats["node_count"])
    m2.metric("总边数", stats["edge_count"])
    m3.metric("有向图", "是" if stats["is_directed"] else "否")
    m4.metric("MultiGraph", "是" if stats["is_multigraph"] else "否")

    if stats["node_count"] == 0:
        st.info("图谱为空，没有可展示的节点。")
        return

    mode = st.radio(
        "展示模式",
        ("全局概览", "实体相关子图"),
        horizontal=True,
        key="graph_mode",
    )
    show_edge_labels = st.checkbox("显示关系标签", value=False, key="show_edge_labels")
    show_all_labels = st.checkbox("显示全部节点名称", value=False, key="show_all_labels")
    if not show_all_labels:
        st.caption(
            f"默认仅显示 degree 最高的 {gv.DEFAULT_LABEL_TOP_N} 个节点名称；悬停可查看全部信息。"
        )

    subgraph = None
    if mode == "全局概览":
        limit = st.selectbox(
            "节点数量限制", options=[30, 50, 80, 100], index=1, key="overview_limit"
        )
        subgraph = gv.build_overview_subgraph(graph, limit=int(limit))
        st.caption("按节点 degree 从高到低选取，并保留选中节点之间的真实边。")
    else:
        st.write("示例实体")
        example_cols = st.columns(5)
        for index, example in enumerate(ENTITY_SEARCH_EXAMPLES):
            if example_cols[index % 5].button(example, key=f"entity-example-{index}"):
                st.session_state["entity_query"] = example

        query = st.text_input(
            "实体搜索",
            key="entity_query",
            placeholder="例如：轴承 / 叶轮 / 2196",
        )
        hops = st.radio("邻居跳数", options=[1, 2], index=0, horizontal=True, key="hops")
        if not (query or "").strip():
            st.info("请输入实体名称以展示相关子图。")
            return
        matches = gv.find_matching_nodes(graph, query)
        if not matches:
            st.warning(f"未找到匹配实体：{query.strip()}")
            return
        labels = {
            node_id: (
                f"{gv.bilingual_entity_label(gv.get_node_display_name(node_id, graph.nodes[node_id])).replace(chr(10), ' ')} "
                f"[{gv.map_type_zh(gv.get_node_type(graph.nodes[node_id]))}]"
            )
            for node_id in matches
        }
        selected = st.selectbox(
            "匹配结果",
            options=matches,
            format_func=lambda node_id: labels[node_id],
            key="entity_match",
        )
        subgraph = gv.build_neighborhood_subgraph(
            graph,
            selected,
            hops=int(hops),
            max_nodes=gv.MAX_NEIGHBORHOOD_NODES,
        )

    if subgraph is None:
        return

    s1, s2 = st.columns(2)
    s1.metric("当前展示节点数", subgraph.number_of_nodes())
    s2.metric("当前展示边数", subgraph.number_of_edges())

    legend = gv.collect_type_legend(subgraph)
    if legend:
        st.write("实体类型图例")
        legend_cols = st.columns(min(4, len(legend)))
        for index, item in enumerate(legend):
            with legend_cols[index % len(legend_cols)]:
                legend_text = item.get("label") or item["type"]
                st.markdown(
                    f"<span style='display:inline-block;width:12px;height:12px;"
                    f"background:{item['color']};border-radius:2px;margin-right:6px;'></span>"
                    f"{legend_text}",
                    unsafe_allow_html=True,
                )

    try:
        html = gv.render_pyvis_html(
            subgraph, show_edge_labels=show_edge_labels, show_all_labels=show_all_labels
        )
        components.html(html, height=720, scrolling=True)
    except Exception as error:
        st.error(f"图谱渲染失败：{error}")

    with st.expander("查看当前子图节点"):
        rows = gv.build_node_table(subgraph)
        if rows:
            st.dataframe(rows, use_container_width=True, hide_index=True)
        else:
            st.info("当前子图没有节点。")


# ---------------------------------------------------------------------------
# Page shell
# ---------------------------------------------------------------------------

st.set_page_config(page_title="工业离心泵知识库问答", page_icon="🔧", layout="wide")
st.title("基于 LightRAG 的工业离心泵知识库问答系统")

try:
    settings = Settings.from_env()
except Exception as error:
    settings = None
    st.error(f"配置错误：{error}")

qa_tab, graph_tab = st.tabs(["智能问答", "知识图谱"])
with qa_tab:
    _render_qa_tab(settings)
with graph_tab:
    try:
        _render_graph_tab(settings)
    except Exception as error:
        st.error(f"知识图谱页面异常：{error}")
