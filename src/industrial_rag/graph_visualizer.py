"""Read-only LightRAG GraphML visualization helpers (no Bailian / runtime)."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import networkx as nx

from industrial_rag.graph_display_mapping import (
    bilingual_entity_label,
    map_entity_zh,
    map_type_zh,
)

_TAG_RE = re.compile(r"<[^>]+>")
_SEP_RE = re.compile(r"(?i)<\s*SEP\s*>")
_WS_RE = re.compile(r"\s+")

GRAPHML_FILENAME = "graph_chunk_entity_relation.graphml"
DEFAULT_OVERVIEW_LIMIT = 50
MAX_OVERVIEW_LIMIT = 150
MAX_NEIGHBORHOOD_NODES = 100
MIN_NODE_SIZE = 12
MAX_NODE_SIZE = 48
DEFAULT_LABEL_TOP_N = 15
UNKNOWN_COLOR = "#9AA0A6"
_PALETTE = (
    "#1F77B4",
    "#FF7F0E",
    "#2CA02C",
    "#D62728",
    "#9467BD",
    "#8C564B",
    "#E377C2",
    "#7F7F7F",
    "#BCBD22",
    "#17BECF",
    "#4E79A7",
    "#F28E2B",
    "#59A14F",
    "#E15759",
    "#B07AA1",
    "#76B7B2",
)


def locate_graph_file(working_dir: Path | str) -> Path | None:
    path = Path(working_dir) / GRAPHML_FILENAME
    return path.resolve() if path.is_file() else None


def load_graph(graphml_path: Path | str) -> nx.Graph:
    path = Path(graphml_path)
    if not path.is_file():
        raise FileNotFoundError(f"GraphML 文件不存在: {path}")
    return nx.read_graphml(path)


def get_graph_statistics(graph: nx.Graph) -> dict[str, Any]:
    return {
        "node_count": graph.number_of_nodes(),
        "edge_count": graph.number_of_edges(),
        "is_directed": graph.is_directed(),
        "is_multigraph": graph.is_multigraph(),
    }


def strip_markup(text: str) -> str:
    """Remove LightRAG separators and HTML-like tags from display text."""
    cleaned = _SEP_RE.sub("；", text)
    cleaned = _TAG_RE.sub("", cleaned)
    cleaned = (
        cleaned.replace("&nbsp;", " ")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&amp;", "&")
        .replace("&quot;", '"')
        .replace("&#39;", "'")
    )
    return _WS_RE.sub(" ", cleaned).strip()


def normalize_attribute_value(value: Any, *, max_length: int | None = None) -> str:
    if value is None:
        text = ""
    elif isinstance(value, bool):
        text = "true" if value else "false"
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        text = str(value)
    elif isinstance(value, (list, tuple, set)):
        text = ", ".join(normalize_attribute_value(item) for item in value)
    elif isinstance(value, Mapping):
        text = json.dumps(dict(value), ensure_ascii=False, sort_keys=True)
    elif isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            try:
                parsed = json.loads(stripped)
            except json.JSONDecodeError:
                text = value
            else:
                if isinstance(parsed, list):
                    text = ", ".join(normalize_attribute_value(item) for item in parsed)
                else:
                    text = value
        else:
            text = value
    else:
        text = str(value)

    text = strip_markup(str(text)) if text is not None else ""
    text = text.strip()
    if max_length is not None and max_length > 0 and len(text) > max_length:
        return text[: max_length - 1] + "…"
    return text


def get_node_display_name(node_id: str, attrs: Mapping[str, Any] | None = None) -> str:
    data = attrs or {}
    for key in ("entity_id", "entity_name", "name", "label", "title"):
        value = normalize_attribute_value(data.get(key))
        if value:
            return value
    return str(node_id)


def get_node_type(attrs: Mapping[str, Any] | None = None) -> str:
    data = attrs or {}
    for key in ("entity_type", "type", "category"):
        value = normalize_attribute_value(data.get(key))
        if value:
            return value
    return "UNKNOWN"


def get_node_description(attrs: Mapping[str, Any] | None = None) -> str:
    data = attrs or {}
    return normalize_attribute_value(data.get("description"), max_length=400)


def get_edge_relation(attrs: Mapping[str, Any] | None = None) -> str:
    data = attrs or {}
    for key in ("keywords", "relation", "relationship", "label", "description"):
        value = normalize_attribute_value(data.get(key))
        if value:
            return value
    return "RELATED_TO"


def color_for_type(entity_type: str) -> str:
    kind = (entity_type or "UNKNOWN").strip() or "UNKNOWN"
    if kind.upper() == "UNKNOWN":
        return UNKNOWN_COLOR
    digest = hashlib.sha256(kind.encode("utf-8")).hexdigest()
    index = int(digest[:8], 16) % len(_PALETTE)
    return _PALETTE[index]


def compute_node_size(degree: int) -> int:
    safe = max(0, int(degree))
    # log scale keeps hubs visible without dominating the canvas
    size = MIN_NODE_SIZE + round(math.log1p(safe) * 8)
    return max(MIN_NODE_SIZE, min(MAX_NODE_SIZE, size))


def _tooltip_row(label: str, value: str) -> str:
    """Plain-text tooltip row.

    vis-network assigns ``title`` via textContent (not HTML). Returning HTML
    tags makes users see literal ``<div>`` / ``&lt;SEP&gt;`` markup.
    """
    clean_label = strip_markup(label)
    clean_value = strip_markup(value)
    return f"{clean_label}：{clean_value}"


def format_provenance_lines(attrs: Mapping[str, Any] | None = None) -> list[str]:
    """Build user-friendly provenance rows (file / page / section / chunk)."""
    data = dict(attrs or {})
    rows: list[str] = []

    file_raw = normalize_attribute_value(data.get("file_path"), max_length=300)
    if file_raw:
        # LightRAG may join multiple paths with ； after strip_markup
        files = [part.strip() for part in re.split(r"[;；|]", file_raw) if part.strip()]
        if len(files) == 1:
            rows.append(_tooltip_row("文件", files[0]))
        elif files:
            rows.append(_tooltip_row("文件", "；".join(files[:3])))

    page = ""
    for key in ("page", "page_number", "page_no", "page_num"):
        page = normalize_attribute_value(data.get(key), max_length=40)
        if page:
            break
    if page:
        rows.append(_tooltip_row("页码", page))

    section = ""
    for key in ("section", "chapter", "heading", "title"):
        # Avoid reusing entity title fields that are not sections
        if key == "title" and data.get("entity_id"):
            continue
        section = normalize_attribute_value(data.get(key), max_length=120)
        if section:
            break
    if section:
        rows.append(_tooltip_row("章节", section))

    source = normalize_attribute_value(data.get("source_id"), max_length=200)
    if source:
        chunks = [part.strip() for part in re.split(r"[;；|]", source) if part.strip()]
        chunk_text = "；".join(chunks[:2]) if chunks else source
        rows.append(_tooltip_row("chunk", chunk_text))

    return rows


def build_node_tooltip(node_id: str, attrs: Mapping[str, Any] | None = None) -> str:
    data = dict(attrs or {})
    name = get_node_display_name(node_id, data)
    bilingual = bilingual_entity_label(name)
    entity_type = get_node_type(data)
    type_label = map_type_zh(entity_type)
    type_display = f"{type_label} ({entity_type})" if type_label != entity_type else entity_type
    description = get_node_description(data)

    rows = [
        _tooltip_row("名称", bilingual.replace("\n", " ")),
        _tooltip_row("类型", type_display),
        _tooltip_row("ID", str(node_id)),
    ]
    if description:
        rows.append(_tooltip_row("描述", description))
    rows.extend(format_provenance_lines(data))
    return "\n".join(rows)


def build_edge_tooltip(attrs: Mapping[str, Any] | None = None) -> str:
    data = dict(attrs or {})
    relation = get_edge_relation(data)
    description = normalize_attribute_value(data.get("description"), max_length=300)
    weight = normalize_attribute_value(data.get("weight"))
    rows = [_tooltip_row("关系", relation)]
    if description and description != relation:
        rows.append(_tooltip_row("描述", description))
    if weight:
        rows.append(_tooltip_row("权重", weight))
    rows.extend(format_provenance_lines(data))
    return "\n".join(rows)


def _degree_rank_key(graph: nx.Graph, node_id: str) -> tuple[int, str]:
    return (-int(graph.degree(node_id)), str(node_id))


def select_labeled_nodes(
    graph: nx.Graph,
    *,
    top_n: int = DEFAULT_LABEL_TOP_N,
    show_all: bool = False,
) -> set[str]:
    """Return node IDs that should show permanent labels."""
    if graph.number_of_nodes() == 0:
        return set()
    if show_all:
        return {str(n) for n in graph.nodes()}
    capped = max(0, int(top_n))
    if capped <= 0:
        return set()
    ranked = sorted(graph.nodes(), key=lambda n: _degree_rank_key(graph, n))
    return {str(n) for n in ranked[: min(capped, len(ranked))]}


def build_overview_subgraph(graph: nx.Graph, *, limit: int = DEFAULT_OVERVIEW_LIMIT) -> nx.Graph:
    if graph.number_of_nodes() == 0:
        return graph.__class__()
    capped = max(1, min(int(limit), MAX_OVERVIEW_LIMIT))
    ranked = sorted(graph.nodes(), key=lambda n: _degree_rank_key(graph, n))
    selected = ranked[: min(capped, len(ranked))]
    return graph.subgraph(selected).copy()


def find_matching_nodes(graph: nx.Graph, query: str) -> list[str]:
    needle = (query or "").strip().casefold()
    if not needle:
        return []
    matches: list[str] = []
    for node_id, attrs in graph.nodes(data=True):
        name = get_node_display_name(str(node_id), attrs)
        zh = map_entity_zh(name) or ""
        candidates = [
            str(node_id),
            name,
            zh,
            bilingual_entity_label(name).replace("\n", " "),
            normalize_attribute_value(attrs.get("entity_id")),
            normalize_attribute_value(attrs.get("name")),
            normalize_attribute_value(attrs.get("label")),
        ]
        if any(needle in candidate.casefold() for candidate in candidates if candidate):
            matches.append(str(node_id))
    matches.sort(key=lambda n: _degree_rank_key(graph, n))
    return matches


def _neighbors_undirected(graph: nx.Graph, node_id: str) -> set[str]:
    return set(map(str, graph.neighbors(node_id)))


def _neighbors_directed(graph: nx.DiGraph, node_id: str) -> set[str]:
    preds = set(map(str, graph.predecessors(node_id)))
    succs = set(map(str, graph.successors(node_id)))
    return preds | succs


def build_neighborhood_subgraph(
    graph: nx.Graph,
    center_id: str,
    *,
    hops: int = 1,
    max_nodes: int = MAX_NEIGHBORHOOD_NODES,
) -> nx.Graph:
    if center_id not in graph:
        return graph.__class__()
    hop_limit = max(1, min(int(hops), 2))
    node_cap = max(1, min(int(max_nodes), MAX_NEIGHBORHOOD_NODES))

    # BFS layers with distance
    distance: dict[str, int] = {str(center_id): 0}
    frontier = [str(center_id)]
    for _ in range(hop_limit):
        next_frontier: list[str] = []
        for current in frontier:
            if graph.is_directed():
                neighbors = _neighbors_directed(graph, current)  # type: ignore[arg-type]
            else:
                neighbors = _neighbors_undirected(graph, current)
            for neighbor in neighbors:
                if neighbor not in distance:
                    distance[neighbor] = distance[current] + 1
                    next_frontier.append(neighbor)
        frontier = next_frontier

    candidates = list(distance.keys())
    candidates.sort(
        key=lambda n: (
            distance[n],
            -int(graph.degree(n)),
            str(n),
        )
    )
    # Always keep center first
    ordered = [str(center_id)] + [n for n in candidates if n != str(center_id)]
    selected = ordered[:node_cap]
    if str(center_id) not in selected:
        selected = [str(center_id), *selected[: max(0, node_cap - 1)]]
    return graph.subgraph(selected).copy()


def build_node_table(graph: nx.Graph) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for node_id, attrs in graph.nodes(data=True):
        name = get_node_display_name(str(node_id), attrs)
        bilingual = bilingual_entity_label(name).replace("\n", " ")
        entity_type = get_node_type(attrs)
        rows.append(
            {
                "name": bilingual,
                "type": map_type_zh(entity_type),
                "degree": int(graph.degree(node_id)),
                "id": str(node_id),
            }
        )
    rows.sort(key=lambda row: (-row["degree"], row["name"], row["id"]))
    return rows


def collect_type_legend(graph: nx.Graph) -> list[dict[str, str]]:
    types = sorted({get_node_type(attrs) for _, attrs in graph.nodes(data=True)})
    legend: list[dict[str, str]] = []
    for entity_type in types:
        legend.append(
            {
                "type": entity_type,
                "label": map_type_zh(entity_type),
                "color": color_for_type(entity_type),
            }
        )
    return legend


def _short_label(text: str, *, max_length: int = 24) -> str:
    value = text.strip()
    if len(value) <= max_length:
        return value
    return value[: max_length - 1] + "…"


def _node_canvas_label(english_name: str, *, show: bool) -> str:
    # PyVis falls back to node id when label is empty/falsy; use a space so
    # unlabeled nodes stay visually unlabeled while hover title still works.
    if not show:
        return " "
    zh = map_entity_zh(english_name)
    if zh:
        # Compact canvas label: Chinese primary, English secondary when short
        combined = f"{zh}\n({english_name})"
        if len(combined) <= 28:
            return combined
        return _short_label(zh, max_length=16)
    return _short_label(english_name)


_TOOLTIP_CSS = """
<style type="text/css">
  /* Force high-contrast plain-text tooltip: dark panel + light text. */
  div.vis-tooltip {
    position: absolute !important;
    padding: 10px 12px !important;
    background: #111827 !important;
    background-color: #111827 !important;
    border: 1px solid #4B5563 !important;
    border-radius: 8px !important;
    color: #F9FAFB !important;
    font-family: "Segoe UI", "Microsoft YaHei", sans-serif !important;
    font-size: 14px !important;
    line-height: 1.5 !important;
    white-space: pre-line !important;
    max-width: 380px !important;
    z-index: 10000 !important;
    pointer-events: none !important;
    box-shadow: 0 10px 28px rgba(0, 0, 0, 0.35) !important;
  }
  div.vis-tooltip,
  div.vis-tooltip * {
    color: #F9FAFB !important;
  }
  #mynetwork {
    border: 1px solid #E5E7EB;
    border-radius: 8px;
    background: #FFFFFF;
  }
</style>
"""

# Neighbor focus: dim unrelated nodes/edges; keep drag/zoom/physics-off intact.
_NEIGHBOR_HIGHLIGHT_SCRIPT = """
<script type="text/javascript">
  /* industrial-rag:node-click-highlight */
  (function () {
    if (typeof network === "undefined" || typeof nodes === "undefined") {
      return;
    }
    var highlightActive = false;
    var baseNodeColors = {};
    var baseEdgeColors = {};

    try {
      nodes.forEach(function (node) {
        baseNodeColors[node.id] = node.color;
      });
    } catch (e) {}
    try {
      if (typeof edges !== "undefined") {
        edges.forEach(function (edge) {
          baseEdgeColors[edge.id] = edge.color;
        });
      }
    } catch (e) {}

    function resetHighlight() {
      if (!highlightActive) {
        return;
      }
      var nodeUpdates = [];
      nodes.forEach(function (node) {
        nodeUpdates.push({
          id: node.id,
          color: baseNodeColors[node.id],
          opacity: 1.0,
          font: node.font
        });
      });
      if (nodeUpdates.length) {
        nodes.update(nodeUpdates);
      }
      if (typeof edges !== "undefined") {
        var edgeUpdates = [];
        edges.forEach(function (edge) {
          edgeUpdates.push({
            id: edge.id,
            color: baseEdgeColors[edge.id] || { color: "#60A5FA", opacity: 1.0 },
            opacity: 1.0
          });
        });
        if (edgeUpdates.length) {
          edges.update(edgeUpdates);
        }
      }
      highlightActive = false;
    }

    network.on("click", function (params) {
      if (!params.nodes || params.nodes.length === 0) {
        resetHighlight();
        return;
      }
      var selectedId = params.nodes[0];
      var connected = network.getConnectedNodes(selectedId) || [];
      var keep = {};
      keep[selectedId] = true;
      for (var i = 0; i < connected.length; i++) {
        keep[connected[i]] = true;
      }

      var nodeUpdates = [];
      nodes.forEach(function (node) {
        var isKeep = !!keep[node.id];
        var isCenter = node.id === selectedId;
        var base = baseNodeColors[node.id];
        var colorObj;
        if (typeof base === "string") {
          colorObj = { background: base, border: isCenter ? "#111827" : base };
        } else if (base && typeof base === "object") {
          colorObj = {
            background: base.background || base,
            border: isCenter ? "#111827" : (base.border || base.background || "#111827"),
            highlight: base.highlight,
            hover: base.hover
          };
        } else {
          colorObj = { background: "#60A5FA", border: isCenter ? "#111827" : "#2563EB" };
        }
        nodeUpdates.push({
          id: node.id,
          color: colorObj,
          opacity: isKeep ? 1.0 : 0.15,
          borderWidth: isCenter ? 3 : 1
        });
      });
      nodes.update(nodeUpdates);

      if (typeof edges !== "undefined") {
        var edgeUpdates = [];
        edges.forEach(function (edge) {
          var connectedEdge =
            keep[edge.from] && keep[edge.to] &&
            (edge.from === selectedId || edge.to === selectedId ||
             keep[edge.from] && keep[edge.to]);
          // Keep edges between selected neighborhood; dim others
          var inNeighborhood = keep[edge.from] && keep[edge.to];
          edgeUpdates.push({
            id: edge.id,
            color: {
              color: inNeighborhood ? "#1D4ED8" : "#CBD5E1",
              opacity: inNeighborhood ? 1.0 : 0.12
            },
            opacity: inNeighborhood ? 1.0 : 0.12
          });
        });
        edges.update(edgeUpdates);
      }
      highlightActive = true;
    });
  })();
</script>
"""

_STABILIZE_SCRIPT = """
<script type="text/javascript">
  /* industrial-rag:stable-layout */
  network.once("stabilizationIterationsDone", function () {
    network.setOptions({ physics: { enabled: false } });
    try {
      network.fit({ animation: false });
    } catch (e) {}
  });
</script>
"""


def render_pyvis_html(
    graph: nx.Graph,
    *,
    show_edge_labels: bool = False,
    show_all_labels: bool = False,
    label_top_n: int = DEFAULT_LABEL_TOP_N,
    height: str = "700px",
    width: str = "100%",
) -> str:
    if graph.number_of_nodes() == 0:
        return (
            "<div style='padding:1rem;font-family:sans-serif;color:#555;'>"
            "当前子图为空，没有可展示的节点。"
            "</div>"
        )

    try:
        from pyvis.network import Network
    except ImportError as error:
        raise RuntimeError("未安装 pyvis；请执行 pip install -r requirements.txt") from error

    labeled = select_labeled_nodes(graph, top_n=label_top_n, show_all=show_all_labels)

    net = Network(
        height=height,
        width=width,
        directed=graph.is_directed(),
        bgcolor="#FFFFFF",
        font_color="#111827",
        cdn_resources="in_line",
    )
    net.set_options(
        """
        {
          "nodes": {
            "font": {
              "size": 15,
              "face": "Segoe UI, Microsoft YaHei, sans-serif",
              "color": "#111827",
              "strokeWidth": 3,
              "strokeColor": "#FFFFFF",
              "background": "rgba(255,255,255,0.86)",
              "multi": true,
              "vadjust": 0
            },
            "borderWidth": 1,
            "borderWidthSelected": 3,
            "shadow": true,
            "scaling": { "min": 12, "max": 48 }
          },
          "edges": {
            "color": {"color": "#60A5FA", "highlight": "#1D4ED8", "hover": "#2563EB"},
            "font": {
              "size": 12,
              "face": "Segoe UI, Microsoft YaHei, sans-serif",
              "color": "#1F2937",
              "strokeWidth": 2,
              "strokeColor": "#FFFFFF",
              "background": "rgba(255,255,255,0.9)",
              "align": "middle"
            },
            "smooth": {"enabled": true, "type": "continuous", "roundness": 0.35}
          },
          "interaction": {
            "hover": true,
            "tooltipDelay": 80,
            "navigationButtons": true,
            "keyboard": true,
            "multiselect": false,
            "hideEdgesOnDrag": false,
            "dragNodes": true,
            "dragView": true,
            "zoomView": true
          },
          "physics": {
            "enabled": true,
            "stabilization": {
              "enabled": true,
              "iterations": 200,
              "updateInterval": 25,
              "fit": true
            },
            "barnesHut": {
              "gravitationalConstant": -14000,
              "centralGravity": 0.12,
              "springLength": 170,
              "springConstant": 0.03,
              "damping": 0.55,
              "avoidOverlap": 0.35
            }
          }
        }
        """
    )

    for node_id, attrs in graph.nodes(data=True):
        name = get_node_display_name(str(node_id), attrs)
        entity_type = get_node_type(attrs)
        degree = int(graph.degree(node_id))
        show_label = str(node_id) in labeled
        net.add_node(
            str(node_id),
            label=_node_canvas_label(name, show=show_label),
            title=build_node_tooltip(str(node_id), attrs),
            group=entity_type,
            size=compute_node_size(degree),
            color=color_for_type(entity_type),
        )

    if graph.is_multigraph():
        edge_iter = graph.edges(keys=True, data=True)
        for source, target, _key, attrs in edge_iter:
            relation = get_edge_relation(attrs)
            edge_kwargs: dict[str, Any] = {
                "title": build_edge_tooltip(attrs),
            }
            if show_edge_labels:
                edge_kwargs["label"] = _short_label(relation, max_length=18)
            net.add_edge(str(source), str(target), **edge_kwargs)
    else:
        for source, target, attrs in graph.edges(data=True):
            relation = get_edge_relation(attrs)
            edge_kwargs = {"title": build_edge_tooltip(attrs)}
            if show_edge_labels:
                edge_kwargs["label"] = _short_label(relation, max_length=18)
            net.add_edge(str(source), str(target), **edge_kwargs)

    # generate_html() returns a str in-memory. Avoid write_html on Windows:
    # it opens the file with the locale encoding (often GBK) and fails on
    # non-ASCII characters embedded in inlined CDN assets.
    content = net.generate_html(notebook=False)
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError("PyVis 生成了空 HTML")

    body_scripts = _STABILIZE_SCRIPT + _NEIGHBOR_HIGHLIGHT_SCRIPT
    if "</head>" in content:
        content = content.replace("</head>", _TOOLTIP_CSS + "</head>")
        if "</body>" in content:
            content = content.replace("</body>", body_scripts + "</body>")
        else:
            content += body_scripts
    elif "</body>" in content:
        content = content.replace("</body>", _TOOLTIP_CSS + body_scripts + "</body>")
    else:
        content = _TOOLTIP_CSS + body_scripts + content
    return content
