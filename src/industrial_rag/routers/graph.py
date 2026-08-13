"""Read-only, user-safe GraphML projection for the Vue workbench."""

from __future__ import annotations

from typing import Any

import networkx as nx
from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from industrial_rag import graph_visualizer as graph_tools
from industrial_rag.errors import AppError
from industrial_rag.graph_display_mapping import map_type_zh

router = APIRouter(prefix="/v1/graph", tags=["graph"])


class GraphNodeResponse(BaseModel):
    id: str
    label: str
    type: str
    x: float
    y: float
    degree: int = Field(ge=0)


class GraphEdgeResponse(BaseModel):
    id: str
    source: str
    target: str
    label: str


class GraphStatsResponse(BaseModel):
    node_count: int
    edge_count: int
    mode: str
    query: str | None = None


class GraphResponse(BaseModel):
    nodes: list[GraphNodeResponse]
    edges: list[GraphEdgeResponse]
    stats: GraphStatsResponse


def _graph_path(request: Request):
    settings = getattr(request.app.state, "resolved_settings", None)
    if settings is None:
        raise AppError("graph_not_ready", "图谱服务尚未就绪。", status_code=503)
    workspace = settings.vector_workspace
    if workspace is None and settings.qdrant_generation:
        workspace = f"{settings.vector_backend.value}-{settings.qdrant_generation}"
    graph_path = graph_tools.locate_graph_file(settings.working_dir, workspace=workspace)
    if graph_path is None and workspace is not None:
        graph_path = graph_tools.locate_graph_file(settings.working_dir)
    if graph_path is None:
        raise AppError("graph_not_found", "当前知识库没有可用的知识图谱。", status_code=404)
    return graph_path


def _load_graph(request: Request) -> nx.Graph:
    graph_path = _graph_path(request)
    try:
        return graph_tools.load_graph(graph_path)
    except FileNotFoundError:
        raise AppError("graph_not_found", "当前知识库没有可用的知识图谱。", status_code=404) from None
    except Exception as error:
        raise AppError("graph_unreadable", "知识图谱暂时无法读取。", status_code=503) from error


def _serialize_graph(graph: nx.Graph, *, mode: str, query: str | None) -> GraphResponse:
    if graph.number_of_nodes() == 0:
        positions: dict[Any, tuple[float, float]] = {}
    else:
        positions = nx.spring_layout(graph, seed=17, dim=2, scale=280)

    nodes = []
    for node_id, attrs in graph.nodes(data=True):
        identifier = str(node_id)
        label = graph_tools.get_node_display_name(identifier, attrs)
        nodes.append(GraphNodeResponse(
            id=identifier,
            label=label,
            type=map_type_zh(graph_tools.get_node_type(attrs)),
            x=round(float(positions[node_id][0]), 2),
            y=round(float(positions[node_id][1]), 2),
            degree=int(graph.degree(node_id)),
        ))
    nodes.sort(key=lambda node: node.id)

    edges: list[GraphEdgeResponse] = []
    if graph.is_multigraph():
        edge_rows = graph.edges(data=True, keys=True)
        for index, (source, target, _key, attrs) in enumerate(edge_rows):
            edges.append(_edge_response(source, target, attrs, index))
    else:
        for index, (source, target, attrs) in enumerate(graph.edges(data=True)):
            edges.append(_edge_response(source, target, attrs, index))

    return GraphResponse(
        nodes=nodes,
        edges=edges,
        stats=GraphStatsResponse(node_count=len(nodes), edge_count=len(edges), mode=mode, query=query),
    )


def _edge_response(source: Any, target: Any, attrs: dict[str, Any], index: int) -> GraphEdgeResponse:
    source_id, target_id = str(source), str(target)
    return GraphEdgeResponse(
        id=f"edge-{index}-{source_id}-{target_id}",
        source=source_id,
        target=target_id,
        label=graph_tools.get_edge_relation(attrs),
    )


@router.get("/overview", response_model=GraphResponse)
async def graph_overview(request: Request, limit: int = Query(50, ge=1, le=150)) -> GraphResponse:
    graph = _load_graph(request)
    return _serialize_graph(graph_tools.build_overview_subgraph(graph, limit=limit), mode="overview", query=None)


@router.get("/neighborhood", response_model=GraphResponse)
async def graph_neighborhood(
    request: Request,
    query: str = Query(min_length=1, max_length=200),
    hops: int = Query(1, ge=1, le=2),
) -> GraphResponse:
    graph = _load_graph(request)
    matches = graph_tools.find_matching_nodes(graph, query)
    subgraph = graph_tools.build_neighborhood_subgraph(graph, matches[0], hops=hops) if matches else graph.__class__()
    return _serialize_graph(subgraph, mode="neighborhood", query=query)


@router.get("/native", response_class=HTMLResponse)
async def graph_native(
    request: Request,
    query: str | None = Query(default=None, min_length=1, max_length=200),
    hops: int = Query(1, ge=1, le=2),
    limit: int = Query(50, ge=1, le=150),
    show_node_labels: bool = Query(True),
    show_edge_labels: bool = Query(False),
) -> HTMLResponse:
    graph = _load_graph(request)
    if query:
        matches = graph_tools.find_matching_nodes(graph, query)
        graph = (
            graph_tools.build_neighborhood_subgraph(graph, matches[0], hops=hops)
            if matches
            else graph.__class__()
        )
    else:
        graph = graph_tools.build_overview_subgraph(graph, limit=limit)
    try:
        html = graph_tools.render_pyvis_html(
            graph,
            show_edge_labels=show_edge_labels,
            show_all_labels=False,
            label_top_n=15 if show_node_labels else 0,
            height="700px",
            width="100%",
        )
    except Exception as error:
        raise AppError("graph_unreadable", "知识图谱暂时无法读取。", status_code=503) from error
    return HTMLResponse(content=html)
