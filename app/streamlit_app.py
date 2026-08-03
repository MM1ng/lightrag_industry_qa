"""Single-page Streamlit UI backed by the P3 Knowledge QA API.

All LightRAG and model work runs behind the API boundary. The graph tab stays
local and reads GraphML only, so it does not depend on API availability.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

PROJECT_ROOT = Path(__file__).resolve().parents[1]
for import_path in (PROJECT_ROOT, PROJECT_ROOT / "src"):
    resolved = str(import_path)
    if resolved not in sys.path:
        sys.path.insert(0, resolved)

from industrial_rag import graph_visualizer as gv  # noqa: E402
from industrial_rag.config import INDEX_METADATA_FILENAME  # noqa: E402

from app.api_client import ApiError, ApiKnowledgeBase, KnowledgeApiClient  # noqa: E402
from app.chat_state import (  # noqa: E402
    AssistantMessage,
    ChatSession,
    UserMessage,
    add_error_message,
    add_user_message,
    clear_session,
    create_empty_session,
)
from app.components.knowledge_base_selector import (  # noqa: E402
    knowledge_base_label,
    queryable_knowledge_bases,
)
from app.p3_chat import append_p3_answer, build_p3_history  # noqa: E402
from app.ui_theme import inject_theme_css  # noqa: E402

# ---------------------------------------------------------------------------
# API client and local graph configuration
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


API_BASE_URL = os.environ.get("KNOWLEDGE_API_URL", "http://127.0.0.1:8000")
API_KEY = os.environ.get("SERVICE_API_KEY", "")
ADMIN_API_KEY = os.environ.get("ADMIN_API_KEY", "")
WORKING_DIR = Path(os.environ.get("LIGHTRAG_WORKING_DIR", PROJECT_ROOT / "lightrag_storage"))


def _api_timeout_seconds() -> float:
    """Read a bounded timeout without making an invalid environment value fatal."""
    try:
        timeout = float(os.environ.get("API_TIMEOUT_S", "120"))
    except ValueError:
        return 120.0
    return timeout if timeout > 0 else 120.0


@st.cache_resource(show_spinner=False)
def _get_client(base_url: str, api_key: str, timeout: float) -> KnowledgeApiClient:
    """Create one reusable P3 HTTP client per Streamlit process."""
    return KnowledgeApiClient(base_url, api_key=api_key, timeout=timeout)


def _ask_api(kb_id: str, question: str, history: list[dict[str, str]]):
    """Execute one P3 query through the configured API boundary."""
    return _get_client(API_BASE_URL, API_KEY, _api_timeout_seconds()).query_knowledge_base(
        kb_id,
        question,
        history,
    )


def _load_queryable_knowledge_bases() -> tuple[ApiKnowledgeBase, ...]:
    """Load only ready KBs through the service credential."""
    try:
        items = _get_client(API_BASE_URL, API_KEY, _api_timeout_seconds()).list_knowledge_bases()
    except ApiError:
        return ()
    return queryable_knowledge_bases(items)


def _render_knowledge_base_selector() -> ApiKnowledgeBase | None:
    """Render the ordinary-user KB selector and isolate chat on changes."""
    items = _load_queryable_knowledge_bases()
    previous_id = st.session_state.get("selected_kb_id")
    if not items:
        st.session_state["selected_kb_id"] = None
        st.warning("当前没有可查询的知识库，请稍后再试或联系管理员。")
        return None

    ids = [item.id for item in items]
    selected_id = previous_id if previous_id in ids else ids[0]
    selected_id = st.selectbox(
        "选择知识库",
        ids,
        index=ids.index(selected_id),
        format_func=lambda value: knowledge_base_label(next(item for item in items if item.id == value)),
        key="selected_kb_id",
    )
    if previous_id is not None and selected_id != previous_id:
        st.session_state["chat_session"] = clear_session()
        st.info("已切换知识库，当前对话已隔离。")
    return next(item for item in items if item.id == selected_id)


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
# Helpers
# ---------------------------------------------------------------------------


def _get_status_bar_graph_stats(working_dir: Path) -> dict | None:
    """Return graph statistics for the shared status bar.

    Uses the existing GraphML cache. Failures are silent so Q&A stays available.
    """
    try:
        _, graph = _get_graph(working_dir)
    except Exception:
        return None
    if graph is None:
        return None
    try:
        return gv.get_graph_statistics(graph)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Chat submission — single entry path
# ---------------------------------------------------------------------------


def _submit_question(prompt: str) -> None:
    """Unique query submission path used by chat_input and example buttons."""
    normalized = prompt.strip()
    if not normalized:
        return

    kb_id = st.session_state.get("selected_kb_id")
    if not isinstance(kb_id, str) or not kb_id.strip():
        st.warning("请先选择一个可查询的知识库。")
        return

    session = st.session_state["chat_session"]
    history = build_p3_history(session)
    new_session, user_msg = add_user_message(session, normalized)
    st.session_state["chat_session"] = new_session

    # Immediate user-message feedback for the current run only.
    with st.chat_message("user"):
        st.markdown(user_msg.content)

    try:
        with st.spinner("正在检索离心泵手册……"):
            result = _ask_api(kb_id, normalized, history)
        new_session, _ = append_p3_answer(new_session, result)
    except ApiError as exc:
        new_session, _ = add_error_message(new_session, f"查询失败 [{exc.code}]：{exc.message}")
    except Exception:
        new_session, _ = add_error_message(new_session, "查询失败：服务暂时不可用，请稍后重试。")

    st.session_state["chat_session"] = new_session
    st.rerun()


# ---------------------------------------------------------------------------
# Chat render helpers
# ---------------------------------------------------------------------------


def _render_status_bar(working_dir: Path, graph_stats: dict | None) -> None:
    """Compact shared status bar above both tabs."""
    parts = ["🔧 工业离心泵知识库"]
    parts.append(f"API: {API_BASE_URL}")
    marker = working_dir / INDEX_METADATA_FILENAME
    parts.append("本地图谱已就绪" if marker.is_file() else "本地图谱未就绪")
    if graph_stats is not None:
        parts.append(f"节点: {graph_stats['node_count']}")
        parts.append(f"边: {graph_stats['edge_count']}")
    with st.container(key="status-bar"):
        st.caption(" ｜ ".join(parts))


def _render_chat_history(session: ChatSession) -> None:
    """Render the full chat history."""
    for msg in session:
        if isinstance(msg, UserMessage):
            with st.chat_message("user"):
                st.markdown(msg.content)
        elif isinstance(msg, AssistantMessage):
            _render_assistant_message(msg)


def _render_assistant_message(msg: AssistantMessage) -> None:
    """Render one assistant message: body, meta line, citations."""
    with st.chat_message("assistant"):
        st.markdown(msg.content)
        _render_message_meta(msg)
        _render_citations(msg)


def _render_message_meta(msg: AssistantMessage) -> None:
    """Compact per-message metadata line."""
    parts: list[str] = []
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
    """Collapsed citation panel with filename / page / chunk_id."""
    if not msg.citations:
        st.caption("本次回答没有可验证的来源。")
        return

    with st.expander(f"📎 引用来源（{msg.citation_count} 条）"):
        for index, citation in enumerate(msg.citations, start=1):
            st.markdown(f"**来源 {index}**")
            st.write(citation.source_file)
            st.caption(f"第 {citation.page_number} 页 · Chunk：{citation.chunk_id}")


def _render_empty_state() -> None:
    """First-open empty state with example questions."""
    with st.container(key="empty-state"):
        st.markdown("### 你可以这样问我")
        st.caption("基于离心泵运维手册的证据检索问答 · 选择示例或直接在下方输入")
        cols = st.columns(2, gap="small")
        for index, example in enumerate(EXAMPLE_QUESTIONS[:4]):
            if cols[index % 2].button(
                example,
                key=f"example-chat-{index}",
                use_container_width=True,
            ):
                _submit_question(example)


# ---------------------------------------------------------------------------
# Graph tab (core logic unchanged)
# ---------------------------------------------------------------------------


def _render_graph_tab(working_dir: Path) -> None:
    st.caption("只读展示 LightRAG 已生成的 GraphML 子集，不调用百炼 API，不修改图谱。")

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
# QA tab
# ---------------------------------------------------------------------------


def _render_qa_tab() -> None:
    with st.container(key="qa-shell"):
        selected_kb = _render_knowledge_base_selector()
        if selected_kb is not None:
            st.caption(
                f"当前知识库：{selected_kb.name} · Active Generation："
                f"{(selected_kb.active_generation or '未激活')[:12]}"
            )
        with st.container(key="qa-toolbar"):
            col1, col2 = st.columns([3, 1], vertical_alignment="bottom", gap="small")
            with col1:
                st.caption("P3 工作流会自动选择检索策略")
            with col2:
                if st.button("清空会话", use_container_width=True, key="clear-session"):
                    st.session_state["chat_session"] = clear_session()
                    st.rerun()

        session = st.session_state.get("chat_session", [])
        if session:
            _render_chat_history(session)
        else:
            _render_empty_state()

    if prompt := st.chat_input(
        placeholder="请输入离心泵运维问题，例如：离心泵启动前需要检查什么？"
    ):
        _submit_question(prompt)


# ---------------------------------------------------------------------------
# Knowledge base update tab (Phase 9)
# ---------------------------------------------------------------------------


def _mgmt_headers() -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if ADMIN_API_KEY.strip():
        headers["Authorization"] = f"Bearer {ADMIN_API_KEY.strip()}"
    return headers


def _mgmt_get(path: str) -> dict:
    import httpx

    with httpx.Client(
        base_url=API_BASE_URL.rstrip("/"), headers=_mgmt_headers(), timeout=60.0
    ) as client:
        resp = client.get(path)
        resp.raise_for_status()
        return resp.json()


def _mgmt_post(path: str) -> dict:
    import httpx

    with httpx.Client(
        base_url=API_BASE_URL.rstrip("/"), headers=_mgmt_headers(), timeout=600.0
    ) as client:
        resp = client.post(path)
        resp.raise_for_status()
        return resp.json()


def _mgmt_upload(path: str, file) -> dict:
    import httpx

    headers = (
        {"Authorization": f"Bearer {ADMIN_API_KEY.strip()}"}
        if ADMIN_API_KEY.strip()
        else {}
    )
    with httpx.Client(
        base_url=API_BASE_URL.rstrip("/"), headers=headers, timeout=600.0
    ) as client:
        resp = client.post(path, files={"file": (file.name, file.getvalue(), "application/pdf")})
        resp.raise_for_status()
        return resp.json()


def _mgmt_delete(path: str) -> dict:
    import httpx

    with httpx.Client(
        base_url=API_BASE_URL.rstrip("/"), headers=_mgmt_headers(), timeout=600.0
    ) as client:
        resp = client.delete(path)
        resp.raise_for_status()
        return resp.json()


def _render_update_tab() -> None:
    import pandas as pd

    st.subheader("知识库更新（增量 Generation 生命周期）")
    try:
        kbs = _mgmt_get("/v1/knowledge-bases")
    except Exception as error:
        st.error(f"无法读取知识库列表：{error}")
        return
    kb_items = kbs.get("items") or []
    if not kb_items:
        st.info("暂无知识库。请先在 API 创建知识库。")
        return
    kb_labels = {f"{kb['name']}（{kb['id'][:8]}）": kb["id"] for kb in kb_items}
    selected_label = st.selectbox(
        "选择知识库", list(kb_labels), key="update-kb-select"
    )
    kb_id = kb_labels[selected_label]

    try:
        detail = _mgmt_get(f"/v1/knowledge-bases/{kb_id}")
        generations = _mgmt_get(f"/v1/knowledge-bases/{kb_id}/generations")
        documents = _mgmt_get(f"/v1/knowledge-bases/{kb_id}/documents")
        jobs = _mgmt_get(f"/v1/knowledge-bases/{kb_id}/update-jobs")
    except Exception as error:
        st.error(f"读取知识库详情失败：{error}")
        return

    st.metric(
        "当前 Active Generation",
        detail.get("active_vector_generation") or "无",
    )
    col1, col2, col3 = st.columns(3)
    col1.metric("文档数", detail.get("active_document_count", 0))
    col2.metric("Chunk 数", detail.get("chunk_count", 0))
    col3.metric("Generation 数", len(generations))

    # 1. Documents list
    with st.expander("文档列表与版本", expanded=True):
        doc_items = documents.get("items") or []
        if doc_items:
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "document_id": d["id"],
                            "文件名": d["original_file_name"],
                            "版本": d["version"],
                            "状态": d["status"],
                            "chunk 数": d["child_chunk_count"],
                            "Hash": d["file_hash"][:12],
                        }
                        for d in doc_items
                    ]
                ),
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.info("暂无文档。")

    # 2. Upload new document
    with st.expander("上传新文档（新增）"):
        uploaded = st.file_uploader("选择 PDF", type=["pdf"], key="update-upload")
        if uploaded is not None:
            st.caption(f"文件：{uploaded.name}，{len(uploaded.getvalue())} bytes")
            if st.button("上传并创建 Candidate Generation", key="update-do-upload"):
                try:
                    result = _mgmt_upload(f"/v1/knowledge-bases/{kb_id}/documents", uploaded)
                    st.success(
                        f"任务已创建：{result.get('status')} job={result.get('job_id')}"
                    )
                    st.rerun()
                except Exception as error:
                    st.error(f"上传失败：{error}")

    # 3. Replace / delete selected document (high risk, must confirm)
    if doc_items:
        with st.expander("替换 / 删除文档（高风险，需二次确认）"):
            doc_labels = {
                f"{d['original_file_name']} v{d['version']}（{d['id'][:8]}）": d["id"]
                for d in doc_items
            }
            selected_doc_label = st.selectbox("选择文档", list(doc_labels), key="update-doc-select")
            selected_doc_id = doc_labels[selected_doc_label]
            st.caption(
                f"影响范围：KB={kb_id[:8]}…，将修改文档 {selected_doc_id[:8]}… "
                f"（当前文档数 {len(doc_items)}）"
            )
            replace_file = st.file_uploader(
                "选择新版本 PDF（替换）", type=["pdf"], key="update-replace-file"
            )
            replace_confirm = st.checkbox(
                "我确认替换该文档（旧版本将在发布后停止检索）", key="update-replace-confirm"
            )
            if replace_file is not None and replace_confirm and st.button(
                "执行替换", key="update-do-replace"
            ):
                import httpx

                headers = (
                    {"Authorization": f"Bearer {ADMIN_API_KEY.strip()}"}
                    if ADMIN_API_KEY.strip()
                    else {}
                )
                with httpx.Client(
                    base_url=API_BASE_URL.rstrip("/"), headers=headers, timeout=600.0
                ) as client:
                    resp = client.put(
                        f"/v1/knowledge-bases/{kb_id}/documents/{selected_doc_id}",
                        files={
                            "file": (
                                replace_file.name,
                                replace_file.getvalue(),
                                "application/pdf",
                            )
                        },
                    )
                    resp.raise_for_status()
                    result = resp.json()
                st.success(f"替换任务已创建：{result.get('status')} job={result.get('job_id')}")
                st.rerun()
            delete_confirm = st.checkbox(
                "我确认删除该文档（发布后正式查询将拒答该内容）", key="update-delete-confirm"
            )
            if delete_confirm and st.button("执行删除", key="update-do-delete"):
                try:
                    result = _mgmt_delete(
                        f"/v1/knowledge-bases/{kb_id}/documents/{selected_doc_id}"
                    )
                    st.success(
                        f"删除任务已创建：{result.get('status')} job={result.get('job_id')}"
                    )
                    st.rerun()
                except Exception as error:
                    st.error(f"删除失败：{error}")

    # 4. Update jobs
    with st.expander("更新任务进度与审计"):
        job_items = jobs.get("items") or []
        if job_items:
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "job_id": j["job_id"][:12],
                            "操作": j["operation"],
                            "状态": j["status"],
                            "阶段": j["current_stage"],
                            "document_id": (j["document_id"] or "")[:12],
                            "candidate": (j["candidate_generation_id"] or "")[:12],
                            "重试": j["retry_count"],
                            "错误": j["error_code"],
                        }
                        for j in job_items
                    ]
                ),
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.info("暂无更新任务。")

    # 5. Generations: validate / promote / rollback / diff
    with st.expander("Generation 生命周期（验收 / 发布 / 回滚）"):
        gen_items = generations or []
        if not gen_items:
            st.info("暂无 Generation。")
        else:
            gen_labels = {
                f"{g['generation']}（{g['status']}）": g["id"] for g in gen_items
            }
            selected_gen_label = st.selectbox(
                "选择 Generation", list(gen_labels), key="update-gen-select"
            )
            selected_gen_id = gen_labels[selected_gen_label]
            selected_gen = next(g for g in gen_items if g["id"] == selected_gen_id)
            st.caption(
                f"影响范围：KB={kb_id[:8]}…，Generation={selected_gen['generation']}，"
                f"状态={selected_gen['status']}"
            )
            vcol, pcol, rcol, dcol = st.columns(4)
            if vcol.button("验收 Validate", key="update-do-validate"):
                try:
                    result = _mgmt_post(
                        f"/v1/knowledge-bases/{kb_id}/generations/{selected_gen_id}/validate"
                    )
                    st.success(f"验收通过：{result.get('passed')}")
                    st.json(result.get("gates") or {})
                    st.rerun()
                except Exception as error:
                    st.error(f"验收失败：{error}")
            promote_confirm = st.checkbox(
                "确认发布该 Candidate（原子切换 Active 指针）", key="update-promote-confirm"
            )
            if pcol.button("发布 Promote", key="update-do-promote", disabled=not promote_confirm):
                try:
                    result = _mgmt_post(
                        f"/v1/knowledge-bases/{kb_id}/generations/{selected_gen_id}/promote"
                    )
                    st.success(f"发布结果：{result.get('status')}")
                    st.rerun()
                except Exception as error:
                    st.error(f"发布失败：{error}")
            rollback_confirm = st.checkbox(
                "确认回滚到该 Generation（无需重新解析）", key="update-rollback-confirm"
            )
            if rcol.button("回滚 Rollback", key="update-do-rollback", disabled=not rollback_confirm):
                try:
                    result = _mgmt_post(
                        f"/v1/knowledge-bases/{kb_id}/generations/{selected_gen_id}/rollback"
                    )
                    st.success(f"回滚结果：{result.get('status')}")
                    st.rerun()
                except Exception as error:
                    st.error(f"回滚失败：{error}")
            if dcol.button("查看 Diff", key="update-do-diff"):
                try:
                    diff = _mgmt_get(
                        f"/v1/knowledge-bases/{kb_id}/generations/{selected_gen_id}/diff"
                    )
                    st.json(diff)
                except Exception as error:
                    st.error(f"Diff 失败：{error}")


# ---------------------------------------------------------------------------
# Page shell
# ---------------------------------------------------------------------------

st.set_page_config(page_title="工业离心泵知识库问答", page_icon="🔧", layout="wide")
inject_theme_css()

if "chat_session" not in st.session_state:
    st.session_state["chat_session"] = create_empty_session()

api_ready = _get_client(API_BASE_URL, API_KEY, _api_timeout_seconds()).ready()
if not api_ready:
    st.warning(
        f"知识库 API 未就绪（{API_BASE_URL}）。"
        "请确认 P3 API 已启动且 SERVICE_API_KEY 配置一致。"
    )

graph_stats = _get_status_bar_graph_stats(WORKING_DIR)
_render_status_bar(WORKING_DIR, graph_stats)

qa_tab, graph_tab, update_tab = st.tabs(["智能问答", "知识图谱", "知识库更新"])
with qa_tab:
    _render_qa_tab()
with graph_tab:
    try:
        _render_graph_tab(WORKING_DIR)
    except Exception as error:
        st.error(f"知识图谱页面异常：{error}")
with update_tab:
    _render_update_tab()
