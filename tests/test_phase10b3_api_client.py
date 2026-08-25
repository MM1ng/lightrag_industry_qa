import httpx
from app.api_client import ApiKnowledgeBase, KnowledgeApiClient
from app.components.knowledge_base_selector import queryable_knowledge_bases


def _client(handler):
    transport = httpx.MockTransport(handler)
    return KnowledgeApiClient(http_client=httpx.Client(transport=transport, base_url="http://test"), api_key="service")


def test_list_knowledge_bases_parses_queryable_summary():
    client = _client(lambda request: httpx.Response(200, json={"items": [{"id": "kb1", "name": "泵手册", "status": "ready", "document_count": 2, "chunk_count": 8}], "total": 1}))
    items = client.list_knowledge_bases()
    assert items[0].id == "kb1"
    assert items[0].chunk_count == 8


def test_query_knowledge_base_uses_scoped_path_and_preserves_partial_status():
    paths = []
    def handler(request: httpx.Request):
        paths.append(request.url.path)
        return httpx.Response(200, json={"request_id": "r1", "trace_id": "t1", "status": "partial_answer", "answer": "部分回答", "generation_id": "g1", "citations": [{"citation_id": "cite_1", "document_name": "a.pdf", "page": 2, "chunk_id": "c1", "evidence_id": "E1"}], "claims": [{"claim_id": "P1", "text": "部分", "citation_ids": ["cite_1"], "evidence_ids": ["E1"]}], "evidence": [{"evidence_id": "E1", "citation_id": "cite_1", "document_name": "a.pdf", "page": 2, "chunk_id": "c1", "excerpt": "证据"}], "latency_ms": 10})
    client = _client(handler)
    result = client.query_knowledge_base("kb1", "问题", [])
    assert paths == ["/v1/knowledge-bases/kb1/query"]
    assert result.status == "partial_answer"
    assert result.generation_id == "g1"
    assert result.claims[0].evidence_ids == ("E1",)
    assert result.evidence[0].excerpt == "证据"


def test_legacy_query_path_remains_available_for_compatibility():
    paths = []
    def handler(request: httpx.Request):
        paths.append(request.url.path)
        return httpx.Response(200, json={"request_id": "r1", "status": "success", "answer": "回答", "citations": [], "claims": [], "latency_ms": 1})
    client = _client(handler)
    client.query("问题")
    assert paths == ["/v1/query"]


def test_queryable_kb_filter_requires_ready_status():
    items = (
        ApiKnowledgeBase("ready", "Ready", "ready", 1, 1, 2, "gen-a"),
        ApiKnowledgeBase("building", "Building", "indexing", 1, 0, 2),
    )
    assert [item.id for item in queryable_knowledge_bases(items)] == ["ready"]
