from __future__ import annotations

from pathlib import Path

import networkx as nx
from fastapi.testclient import TestClient
from industrial_rag.api import create_app
from industrial_rag.config import Settings
from industrial_rag.vector_collections import VectorBackend


def _app(tmp_path: Path, *, service_api_key: str | None = None):
    storage = tmp_path / 'graph-storage'
    storage.mkdir()
    graph = nx.Graph()
    graph.add_node('Pump', entity_id='离心泵', entity_type='artifact')
    graph.add_node('Bearing', entity_id='轴承', entity_type='component')
    graph.add_node('Seal', entity_id='机械密封', entity_type='component')
    graph.add_edge('Pump', 'Bearing', keywords='contains')
    graph.add_edge('Bearing', 'Seal', keywords='connected_to')
    nx.write_graphml(graph, storage / 'graph_chunk_entity_relation.graphml')
    settings = Settings(api_key='offline-test-key', working_dir=storage, service_api_key=service_api_key)
    return create_app(settings=settings, runtime_factory=lambda _: _Runtime())


class _Runtime:
    def query(self, question, *, mode, timeout):
        raise AssertionError('graph routes must not call the runtime')

    def close(self):
        pass


def test_graph_overview_is_bounded_and_user_safe(tmp_path: Path):
    with TestClient(_app(tmp_path)) as client:
        response = client.get('/v1/graph/overview?limit=2')
    assert response.status_code == 200
    body = response.json()
    assert len(body['nodes']) == 2
    assert body['stats']['mode'] == 'overview'
    assert {'id', 'label', 'type', 'x', 'y', 'degree'} == set(body['nodes'][0])
    assert 'entity_id' not in body['nodes'][0]


def test_graph_neighborhood_matches_entity_and_hops(tmp_path: Path):
    with TestClient(_app(tmp_path)) as client:
        response = client.get('/v1/graph/neighborhood', params={'query': '轴承', 'hops': 2})
    assert response.status_code == 200
    assert {node['label'] for node in response.json()['nodes']} == {'轴承', '离心泵', '机械密封'}


def test_graph_missing_file_returns_public_404(tmp_path: Path):
    settings = Settings(api_key='offline-test-key', working_dir=tmp_path / 'missing')
    app = create_app(settings=settings, runtime_factory=lambda _: _Runtime())
    with TestClient(app) as client:
        response = client.get('/v1/graph/overview')
    assert response.status_code == 404
    assert response.json()['code'] == 'graph_not_found'


def test_graph_overview_finds_qdrant_generation_workspace(tmp_path: Path):
    storage = tmp_path / 'generation-root'
    workspace = storage / 'qdrant-g12345678'
    workspace.mkdir(parents=True)
    graph = nx.Graph()
    graph.add_node('Pump', entity_id='离心泵', entity_type='artifact')
    nx.write_graphml(graph, workspace / 'graph_chunk_entity_relation.graphml')
    settings = Settings(
        api_key='offline-test-key',
        working_dir=storage,
        vector_backend=VectorBackend.qdrant,
        qdrant_url='http://127.0.0.1:17333',
        qdrant_generation='g12345678',
    )
    with TestClient(create_app(settings=settings, runtime_factory=lambda _: _Runtime())) as client:
        response = client.get('/v1/graph/overview')
    assert response.status_code == 200
    assert response.json()['nodes'][0]['label'] == '离心泵'


def test_graph_native_returns_lightrag_pyvis_document(tmp_path: Path):
    with TestClient(_app(tmp_path)) as client:
        response = client.get('/v1/graph/native?limit=2')
    assert response.status_code == 200
    assert response.headers['content-type'].startswith('text/html')
    assert 'vis-network' in response.text
    assert 'Pump' in response.text


def test_graph_is_public_for_browser_but_admin_document_reads_are_protected(tmp_path: Path):
    with TestClient(_app(tmp_path, service_api_key='service-secret')) as client:
        response = client.get('/v1/graph/overview')
        protected = client.get('/v1/knowledge-bases/kb-1/documents')
    assert response.status_code == 200
    assert protected.status_code == 401


def test_graph_unreadable_file_returns_public_503(tmp_path: Path):
    storage = tmp_path / 'broken-storage'
    storage.mkdir()
    (storage / 'graph_chunk_entity_relation.graphml').write_text('not graphml', encoding='utf-8')
    app = create_app(settings=Settings(api_key='offline-test-key', working_dir=storage), runtime_factory=lambda _: _Runtime())
    with TestClient(app) as client:
        response = client.get('/v1/graph/overview')
    assert response.status_code == 503
    assert response.json()['code'] == 'graph_unreadable'
