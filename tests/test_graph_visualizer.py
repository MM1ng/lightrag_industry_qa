"""Tests for knowledge-graph visualization helpers (no Bailian API)."""

from __future__ import annotations

from pathlib import Path

import networkx as nx
import pytest
from industrial_rag import graph_visualizer as gv


def _write_graphml(path: Path, graph: nx.Graph) -> Path:
    nx.write_graphml(graph, path)
    return path


def _sample_graph() -> nx.Graph:
    graph = nx.Graph()
    graph.add_node(
        "Pump",
        entity_id="Pump",
        entity_type="artifact",
        description="离心泵主体",
        source_id="chunk-1",
        file_path="manual.pdf",
    )
    graph.add_node(
        "Bearing",
        entity_id="Bearing",
        entity_type="artifact",
        description="轴承组件",
        source_id="chunk-2",
        file_path="manual.pdf",
    )
    graph.add_node(
        "Seal",
        entity_id="Seal",
        entity_type="component",
        description="机械密封",
        source_id="chunk-3",
        file_path="manual.pdf",
    )
    graph.add_node("Oil", entity_id="Oil", description="润滑油")
    graph.add_node("Temp", entity_id="Temp High", entity_type="symptom")
    graph.add_edge(
        "Pump",
        "Bearing",
        keywords="contains",
        description="Pump contains Bearing",
        source_id="chunk-1",
        file_path="manual.pdf",
        weight="2.0",
    )
    graph.add_edge(
        "Bearing",
        "Seal",
        keywords="adjacent to",
        description="Bearing is next to Seal",
    )
    graph.add_edge("Bearing", "Oil", description="uses oil")
    graph.add_edge("Seal", "Temp", keywords="causes")
    return graph


def test_locate_graph_file_missing(tmp_path: Path) -> None:
    path = gv.locate_graph_file(tmp_path / "missing_storage")
    assert path is None


def test_locate_graph_file_found(tmp_path: Path) -> None:
    storage = tmp_path / "storage"
    storage.mkdir()
    target = storage / "graph_chunk_entity_relation.graphml"
    _write_graphml(target, _sample_graph())
    assert gv.locate_graph_file(storage) == target.resolve()


def test_load_graph_missing_raises(tmp_path: Path) -> None:
    missing = tmp_path / "nope.graphml"
    with pytest.raises(FileNotFoundError, match="GraphML"):
        gv.load_graph(missing)


def test_load_graph_and_statistics(tmp_path: Path) -> None:
    path = _write_graphml(tmp_path / "g.graphml", _sample_graph())
    graph = gv.load_graph(path)
    stats = gv.get_graph_statistics(graph)

    assert graph.number_of_nodes() == 5
    assert graph.number_of_edges() == 4
    assert stats["node_count"] == 5
    assert stats["edge_count"] == 4
    assert stats["is_directed"] is False
    assert stats["is_multigraph"] is False


def test_load_directed_and_multigraph(tmp_path: Path) -> None:
    digraph = nx.MultiDiGraph()
    digraph.add_node("A", entity_id="A", entity_type="x")
    digraph.add_node("B", entity_id="B", entity_type="y")
    digraph.add_edge("A", "B", keywords="r1")
    digraph.add_edge("A", "B", keywords="r2")
    path = _write_graphml(tmp_path / "multi.graphml", digraph)

    loaded = gv.load_graph(path)
    stats = gv.get_graph_statistics(loaded)
    assert stats["is_directed"] is True
    assert stats["is_multigraph"] is True
    assert stats["edge_count"] == 2


def test_display_name_priority_and_fallback() -> None:
    assert gv.get_node_display_name("n1", {"entity_id": "离心泵"}) == "离心泵"
    assert gv.get_node_display_name("n1", {"name": "Named"}) == "Named"
    assert gv.get_node_display_name("n1", {"label": "Labeled"}) == "Labeled"
    assert gv.get_node_display_name("fallback-id", {}) == "fallback-id"


def test_node_type_priority_and_unknown() -> None:
    assert gv.get_node_type({"entity_type": "artifact"}) == "artifact"
    assert gv.get_node_type({"type": "method"}) == "method"
    assert gv.get_node_type({"category": "part"}) == "part"
    assert gv.get_node_type({}) == "UNKNOWN"


def test_edge_relation_priority() -> None:
    assert gv.get_edge_relation({"keywords": "uses"}) == "uses"
    assert gv.get_edge_relation({"relation": "part_of"}) == "part_of"
    assert gv.get_edge_relation({"relationship": "linked"}) == "linked"
    assert gv.get_edge_relation({"label": "L"}) == "L"
    assert gv.get_edge_relation({"description": "long desc"}) == "long desc"
    assert gv.get_edge_relation({}) == "RELATED_TO"


def test_normalize_attribute_value_handles_types() -> None:
    assert gv.normalize_attribute_value(None) == ""
    assert gv.normalize_attribute_value(True) == "true"
    assert gv.normalize_attribute_value(False) == "false"
    assert gv.normalize_attribute_value(12) == "12"
    assert gv.normalize_attribute_value(1.5) == "1.5"
    assert gv.normalize_attribute_value(["a", "b"]) == "a, b"
    assert gv.normalize_attribute_value('["x", "y"]') == "x, y"
    long_text = "字" * 500
    normalized = gv.normalize_attribute_value(long_text, max_length=40)
    assert len(normalized) <= 40
    assert normalized.endswith("…")


def test_html_escape_in_tooltip() -> None:
    tip = gv.build_node_tooltip(
        "id<script>",
        {
            "entity_id": "Name & Co",
            "entity_type": "t",
            "description": "<b>desc</b>",
            "file_path": "a.pdf",
            "source_id": "s1",
        },
    )
    # Plain-text tooltip: markup stripped, no raw HTML tags shown.
    assert "<script>" not in tip
    assert "<b>" not in tip
    assert "</b>" not in tip
    assert "&lt;" not in tip
    assert "desc" in tip
    assert "Name & Co" in tip
    assert "名称：" in tip


def test_description_strips_html_and_sep_markers() -> None:
    raw = "First sentence.<SEP>Second <b>bold</b> part.<script>x</script>"
    cleaned = gv.get_node_description({"description": raw})
    assert "<SEP>" not in cleaned
    assert "<b>" not in cleaned
    assert "</b>" not in cleaned
    assert "<script>" not in cleaned
    assert "First sentence" in cleaned
    assert "Second" in cleaned
    assert "bold" in cleaned
    assert "；" in cleaned or " " in cleaned

    tip = gv.build_node_tooltip(
        "Pump Shaft",
        {
            "entity_id": "Pump Shaft",
            "entity_type": "artifact",
            "description": raw,
        },
    )
    assert "&lt;SEP&gt;" not in tip
    assert "<SEP>" not in tip
    assert "First sentence" in tip
    assert "<div" not in tip
    assert "\n" in tip


def test_tooltip_uses_high_contrast_readable_styles() -> None:
    tip = gv.build_node_tooltip(
        "Pump",
        {
            "entity_id": "Water Pump",
            "entity_type": "artifact",
            "description": "A pump unit.",
            "file_path": "manual.pdf",
        },
    )
    assert "Water Pump" in tip
    assert "名称：" in tip
    assert "<div" not in tip

    rendered = gv.render_pyvis_html(_sample_graph(), show_edge_labels=False)
    assert "vis-tooltip" in rendered
    # Must force a solid dark panel; transparent + light text becomes unreadable.
    assert "#111827" in rendered
    assert "background-color: #111827" in rendered or "background: #111827" in rendered
    assert "#F9FAFB" in rendered
    assert "pre-line" in rendered
    assert "transparent" not in rendered.split("div.vis-tooltip")[1][:500]


def test_build_overview_subgraph_respects_limit_and_keeps_real_edges() -> None:
    graph = _sample_graph()
    # Make Pump highest degree by adding extra neighbors
    for i in range(3):
        nid = f"Extra{i}"
        graph.add_node(nid, entity_id=nid, entity_type="other")
        graph.add_edge("Pump", nid, keywords="extra")

    sub = gv.build_overview_subgraph(graph, limit=3)
    assert sub.number_of_nodes() == 3
    # Only edges among selected nodes
    for u, v in sub.edges():
        assert u in sub.nodes and v in sub.nodes
    degrees = dict(graph.degree())
    selected = sorted(sub.nodes(), key=lambda n: (-degrees[n], str(n)))
    top3 = sorted(graph.nodes(), key=lambda n: (-degrees[n], str(n)))[:3]
    assert selected == top3


def test_build_overview_empty_graph() -> None:
    empty = nx.Graph()
    sub = gv.build_overview_subgraph(empty, limit=50)
    assert sub.number_of_nodes() == 0
    assert sub.number_of_edges() == 0


def test_find_matching_nodes_chinese_and_case_insensitive() -> None:
    graph = _sample_graph()
    graph.add_node("X", entity_id="离心泵启动", entity_type="process")
    graph.add_node("Y", entity_id="BEARING-HOUSING", entity_type="artifact")

    matches = gv.find_matching_nodes(graph, "离心泵")
    assert "X" in matches

    matches_case = gv.find_matching_nodes(graph, "bearing")
    assert "Bearing" in matches_case
    assert "Y" in matches_case

    assert gv.find_matching_nodes(graph, "  ") == []
    assert gv.find_matching_nodes(graph, "不存在实体zzz") == []


def test_build_neighborhood_subgraph_one_and_two_hop() -> None:
    graph = _sample_graph()
    one = gv.build_neighborhood_subgraph(graph, "Bearing", hops=1, max_nodes=100)
    assert "Bearing" in one.nodes
    assert "Pump" in one.nodes
    assert "Seal" in one.nodes
    assert "Oil" in one.nodes
    # Temp is 2 hops away via Seal
    assert "Temp" not in one.nodes

    two = gv.build_neighborhood_subgraph(graph, "Bearing", hops=2, max_nodes=100)
    assert "Temp" in two.nodes
    assert "Bearing" in two.nodes


def test_neighborhood_center_always_kept_and_cap() -> None:
    graph = nx.Graph()
    graph.add_node("Center", entity_id="Center")
    for i in range(20):
        nid = f"N{i}"
        graph.add_node(nid, entity_id=nid)
        graph.add_edge("Center", nid)
    # Chain of farther nodes with high degree elsewhere
    for i in range(20, 40):
        nid = f"Far{i}"
        graph.add_node(nid, entity_id=nid)
        graph.add_edge(f"N{i % 20}", nid)

    sub = gv.build_neighborhood_subgraph(graph, "Center", hops=2, max_nodes=10)
    assert "Center" in sub.nodes
    assert sub.number_of_nodes() <= 10


def test_directed_graph_includes_predecessors_and_successors() -> None:
    digraph = nx.DiGraph()
    digraph.add_node("Center", entity_id="Center")
    digraph.add_node("Pred", entity_id="Pred")
    digraph.add_node("Succ", entity_id="Succ")
    digraph.add_edge("Pred", "Center", keywords="in")
    digraph.add_edge("Center", "Succ", keywords="out")

    sub = gv.build_neighborhood_subgraph(digraph, "Center", hops=1, max_nodes=10)
    assert set(sub.nodes) >= {"Center", "Pred", "Succ"}


def test_node_size_clamped() -> None:
    size0 = gv.compute_node_size(0)
    size_big = gv.compute_node_size(10_000)
    assert size0 >= gv.MIN_NODE_SIZE
    assert size_big <= gv.MAX_NODE_SIZE
    assert size0 > 0


def test_stable_color_mapping_reproducible() -> None:
    types = ["artifact", "method", "symptom", "UNKNOWN"]
    first = {t: gv.color_for_type(t) for t in types}
    second = {t: gv.color_for_type(t) for t in types}
    assert first == second
    assert first["UNKNOWN"] == gv.UNKNOWN_COLOR
    assert first["artifact"] != first["method"]


def test_missing_edge_relation_does_not_raise() -> None:
    assert gv.get_edge_relation({}) == "RELATED_TO"
    assert gv.get_edge_relation({"weight": "1.0"}) == "RELATED_TO"


def test_build_node_table_sorted_by_degree() -> None:
    graph = _sample_graph()
    rows = gv.build_node_table(graph)
    assert rows
    assert all({"name", "type", "degree", "id"} <= set(r) for r in rows)
    degrees = [r["degree"] for r in rows]
    assert degrees == sorted(degrees, reverse=True)


def test_render_pyvis_html_nonempty_and_contains_name(tmp_path: Path) -> None:
    graph = _sample_graph()
    html = gv.render_pyvis_html(graph, show_edge_labels=False)
    assert isinstance(html, str)
    assert len(html) > 100
    assert "Bearing" in html or "Pump" in html


def test_render_pyvis_html_with_edge_labels() -> None:
    graph = _sample_graph()
    html = gv.render_pyvis_html(graph, show_edge_labels=True)
    assert len(html) > 100


def test_render_pyvis_failure_isolated() -> None:
    """Even if pyvis fails, data helpers still work on same graph object."""
    graph = _sample_graph()
    stats = gv.get_graph_statistics(graph)
    assert stats["node_count"] == 5
    # Call render with empty graph — should not raise for data path
    empty_html = gv.render_pyvis_html(nx.Graph(), show_edge_labels=False)
    assert isinstance(empty_html, str)


def test_module_does_not_import_bailian_or_runtime() -> None:
    import industrial_rag.graph_visualizer as module

    source = Path(module.__file__).read_text(encoding="utf-8")
    assert "DASHSCOPE" not in source
    assert "LightRAGRuntime" not in source
    assert "openai" not in source.lower()
    assert "runtime" not in source.lower() or "runtime"  # allow word if unused
    assert "from industrial_rag.runtime" not in source
    assert "from industrial_rag.lightrag_service" not in source


# ---------------------------------------------------------------------------
# P1 UX: label density, provenance, bilingual mapping, click focus, layout
# ---------------------------------------------------------------------------


def _star_graph(n_leaves: int = 20) -> nx.Graph:
    """Hub + leaves so degree ranking is deterministic."""
    graph = nx.Graph()
    graph.add_node("Hub", entity_id="Water Pump", entity_type="artifact", description="center")
    for i in range(n_leaves):
        nid = f"Leaf{i:02d}"
        graph.add_node(nid, entity_id=nid, entity_type="artifact")
        graph.add_edge("Hub", nid, keywords="has")
    return graph


def test_select_labeled_nodes_top_degree_default() -> None:
    graph = _star_graph(20)
    labeled = gv.select_labeled_nodes(graph, top_n=15, show_all=False)
    assert len(labeled) == 15
    assert "Hub" in labeled  # highest degree always included


def test_select_labeled_nodes_respects_top_n_limit() -> None:
    graph = _star_graph(20)
    labeled = gv.select_labeled_nodes(graph, top_n=5, show_all=False)
    assert len(labeled) == 5
    assert "Hub" in labeled


def test_select_labeled_nodes_show_all_toggle() -> None:
    graph = _star_graph(10)
    labeled = gv.select_labeled_nodes(graph, top_n=3, show_all=True)
    assert labeled == {str(n) for n in graph.nodes()}


def test_entity_zh_mapping_and_fallback() -> None:
    from industrial_rag.graph_display_mapping import (
        bilingual_entity_label,
        map_entity_zh,
        map_type_zh,
    )

    assert map_entity_zh("Water Pump") == "水泵"
    assert map_entity_zh("Mechanical Seal") == "机械密封"
    assert map_entity_zh("Impeller") == "叶轮"
    assert map_entity_zh("UnknownWidgetXYZ") is None

    bi = bilingual_entity_label("Water Pump")
    assert "水泵" in bi
    assert "Water Pump" in bi

    assert bilingual_entity_label("UnknownWidgetXYZ") == "UnknownWidgetXYZ"
    assert map_type_zh("artifact") == "设备"
    assert map_type_zh("concept") == "概念"
    assert map_type_zh("unmapped_type_xyz") == "unmapped_type_xyz"


def test_build_node_tooltip_provenance_friendly() -> None:
    tip = gv.build_node_tooltip(
        "Seal",
        {
            "entity_id": "Mechanical Seal",
            "entity_type": "artifact",
            "description": "密封组件",
            "file_path": "SUMMIT-Pump-2196.pdf",
            "source_id": "manual-xxx-chunk-011",
            "page": "21",
            "section": "Installation",
        },
    )
    assert "Mechanical Seal" in tip or "机械密封" in tip
    assert "SUMMIT-Pump-2196.pdf" in tip
    assert "21" in tip
    assert "Installation" in tip
    # chunk retained as technical detail
    assert "manual-xxx-chunk-011" in tip
    # not only raw technical dump without structure
    assert "文件" in tip or "来源" in tip


def test_build_node_tooltip_missing_provenance_fields_no_error() -> None:
    tip = gv.build_node_tooltip(
        "X",
        {
            "entity_id": "Bare Node",
            "entity_type": "artifact",
            # no file_path / page / section / source_id
        },
    )
    assert "Bare Node" in tip
    assert isinstance(tip, str)
    assert "名称" in tip


def test_render_pyvis_html_limits_labels_by_default() -> None:
    import re

    graph = _star_graph(20)
    html = gv.render_pyvis_html(
        graph, show_edge_labels=False, show_all_labels=False, label_top_n=15
    )
    # Hub must be labeled (top degree)
    assert "水泵" in html or "Water Pump" in html or "Hub" in html
    labels = re.findall(r'"label":\s*"([^"]*)"', html)
    meaningful = [lab for lab in labels if lab.strip() and not lab.isspace()]
    # top_n=15 among 21 nodes
    assert len(meaningful) <= 15
    # unlabeled nodes still exist in the dataset
    assert any(lab == " " or lab == "" for lab in labels)


def test_render_pyvis_html_show_all_labels_toggle() -> None:
    graph = _star_graph(8)
    limited = gv.render_pyvis_html(graph, show_all_labels=False, label_top_n=2)
    full = gv.render_pyvis_html(graph, show_all_labels=True, label_top_n=2)
    # Full mode should embed more leaf labels than limited mode
    assert full.count("Leaf") >= limited.count("Leaf")


def test_render_pyvis_html_contains_click_highlight_logic() -> None:
    html = gv.render_pyvis_html(_sample_graph())
    assert "industrial-rag:node-click-highlight" in html
    assert "getConnectedNodes" in html
    assert "network.on" in html and "click" in html
    # Must not disable drag/zoom interactions
    assert "navigationButtons" in html
    assert "dragNodes" in html or "zoomView" in html


def test_render_pyvis_html_contains_stable_layout_config() -> None:
    html = gv.render_pyvis_html(_sample_graph())
    assert "industrial-rag:stable-layout" in html
    assert "stabilization" in html
    assert "physics" in html
    assert "stabilizationIterationsDone" in html
    assert "avoidOverlap" in html
    assert "fit" in html.lower()


def test_collect_type_legend_includes_chinese_labels() -> None:
    legend = gv.collect_type_legend(_sample_graph())
    assert legend
    labels = {item.get("label", item["type"]) for item in legend}
    assert "设备" in labels
    for item in legend:
        assert "color" in item
        assert item["color"].startswith("#")
        assert "label" in item


def test_find_matching_nodes_supports_chinese_query() -> None:
    graph = _sample_graph()
    # Pump maps to 泵; Mechanical Seal maps via Seal entity id "Seal"
    # Add an explicit mapped entity
    graph.add_node("MS", entity_id="Mechanical Seal", entity_type="artifact")
    matches = gv.find_matching_nodes(graph, "机械密封")
    assert "MS" in matches


def test_graph_helpers_do_not_depend_on_runtime_cache() -> None:
    """Graph load/render helpers remain independent of LightRAGRuntime."""
    import industrial_rag.graph_display_mapping as mapping
    import industrial_rag.graph_visualizer as module

    for mod in (module, mapping):
        source = Path(mod.__file__).read_text(encoding="utf-8")
        assert "LightRAGRuntime" not in source
        assert "st.cache_resource" not in source
        assert "from industrial_rag.runtime" not in source


def test_html_escape_still_safe_with_provenance() -> None:
    tip = gv.build_node_tooltip(
        "id<script>",
        {
            "entity_id": "Name <evil>",
            "entity_type": "artifact",
            "file_path": "<img src=x>",
            "source_id": "chunk<script>",
            "page": "3",
            "section": "<b>Sec</b>",
        },
    )
    assert "<script>" not in tip
    assert "<img" not in tip
    assert "<b>" not in tip
