from __future__ import annotations

import hashlib
import json
from pathlib import Path

import httpx
from pydantic import SecretStr

from industrial_energy_agent.rag.ingestion import DocumentRegistry
from industrial_energy_agent.rag.lightrag_adapter import LightRAGRestAdapter
from industrial_energy_agent.tools.knowledge_tools import build_search_manual_knowledge_tool


def _registry(tmp_path: Path) -> tuple[DocumentRegistry, str]:
    manual_dir = tmp_path / "manuals"
    processed_dir = tmp_path / "processed/manuals"
    document_dir = processed_dir / "manual-2196"
    manual_dir.mkdir(parents=True)
    document_dir.mkdir(parents=True)
    source = manual_dir / "manual-2196.pdf"
    source.write_bytes(b"test-only-pdf-content")
    source_sha256 = hashlib.sha256(source.read_bytes()).hexdigest()
    chunk_text = "泵启动前应检查联轴器防护罩。"
    chunk = {
        "text": chunk_text,
        "source_file": source.name,
        "document_title": "2196 Pump Manual",
        "page_number": 3,
        "page_start": 3,
        "page_end": 3,
        "section_title": "启动前检查",
        "chunk_id": "manual-2196:p3:c1:12345678",
        "document_type": "operation_maintenance_manual",
        "equipment_type": "centrifugal_pump",
        "parser_name": "pymupdf",
        "parser_version": "1.28.0",
        "source_sha256": source_sha256,
        "limitations": [],
        "extraction_warnings": [],
    }
    (document_dir / "chunks.jsonl").write_text(
        json.dumps(chunk, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    manifest = {
        "schema_version": 1,
        "requested_parser": "auto",
        "document_count": 1,
        "documents": [
            {
                "source_file": source.name,
                "document_id": "manual-2196",
                "document_title": "2196 Pump Manual",
                "source_sha256": source_sha256,
                "parser_name": "pymupdf",
                "parser_version": "1.28.0",
                "page_count": 1,
                "chunk_count": 1,
                "chunks_file": "manual-2196/chunks.jsonl",
                "report_file": "manual-2196/parse_report.json",
            }
        ],
    }
    (processed_dir / "manuals_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False),
        encoding="utf-8",
    )
    registry = DocumentRegistry.from_processed_manuals(
        manual_dir=manual_dir,
        processed_dir=processed_dir,
        embedding_model="text-embedding-v4",
        embedding_dimension=1024,
        namespace="energyops-manuals-test",
    )
    return registry, chunk_text


def test_real_lightrag_reference_file_path_resolves_through_local_registry(
    tmp_path: Path,
) -> None:
    registry, chunk_text = _registry(tmp_path)
    registered = registry.get("manual-2196")
    upstream_path = f"D:\\lightrag-private\\{registered.remote_file_source}"
    response = {
        "status": "success",
        "message": "ok",
        "data": {
            "chunks": [
                {
                    "content": (
                        f"{registered.manifest_marker}\n\n"
                        "[chunk_id=manual-2196:p3:c1:12345678;page=3]\n"
                        f"{chunk_text}"
                    )
                }
            ],
            "entities": [],
            "relationships": [],
            "references": [{"reference_id": "1", "file_path": upstream_path}],
        },
        "metadata": {},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, request=request, json=response)

    client = httpx.Client(
        base_url="http://127.0.0.1:9621",
        transport=httpx.MockTransport(handler),
    )
    adapter = LightRAGRestAdapter(
        base_url="http://127.0.0.1:9621",
        api_key=SecretStr("test-only-lightrag"),
        max_retries=0,
        source_resolver=registry,
        client=client,
    )
    tool = build_search_manual_knowledge_tool(adapter)

    result = tool.invoke({"query": "泵启动前检查", "request_id": "req-real-reference"})
    rendered = json.dumps(result, ensure_ascii=False)

    assert result["ok"] is True
    assert result["items"][0]["excerpt"] == chunk_text
    assert result["items"][0]["citation"]["source_file"] == "manual-2196.pdf"
    assert "lightrag-private" not in rendered
