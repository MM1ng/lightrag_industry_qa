"""Persist candidate identity, registration, vector, and route gate evidence."""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
from pathlib import Path

import httpx
from qdrant_client import AsyncQdrantClient

ROOT = Path(__file__).resolve().parents[1]
KB_ID = "8fce4626859d44abb70a9ae5b0372cea"
GENERATION_NAME = "g10b3c20260803"
GENERATION_ID = "5bca792c08fcf2f7b08cbaed09b6d525"
DB_PATH = ROOT / "runtime" / "phase10b3c" / "industrial_rag_candidate.db"
BASE = "http://127.0.0.1:8011"


def load_env() -> None:
    for line in (ROOT / ".env.local_staging").read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            os.environ[key] = value


async def main() -> int:
    load_env()
    db = sqlite3.connect(DB_PATH)
    row = db.execute(
        "select id,knowledge_base_id,generation,backend,status,workspace_path,collections,created_at,activated_at from vector_index_generations where id=?",
        (GENERATION_ID,),
    ).fetchone()
    active = db.execute("select active_vector_generation_id from knowledge_bases where id=?", (KB_ID,)).fetchone()[0]
    if row is None:
        raise RuntimeError("candidate registration missing")
    collections = json.loads(row[6])
    identity = {
        "knowledge_base_id": row[1],
        "candidate_generation_id": row[0],
        "candidate_generation_name": row[2],
        "candidate_workspace": str(Path(row[5]).relative_to(ROOT)).replace("\\", "/"),
        "candidate_backend": row[3],
        "candidate_qdrant_collection": collections["chunks"],
        "candidate_status": row[4],
        "active": active == row[0],
    }
    (ROOT / "evaluation" / "phase10b3c" / "candidate_identity.json").write_text(json.dumps(identity, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    registration = {
        **identity,
        "created_at": row[7],
        "activated_at": row[8],
        "artifact_manifest": "runtime/phase10b3c/kb_data/8fce4626859d44abb70a9ae5b0372cea/g10b3c20260803/context_registry/manifest.json",
        "active_generation_id": active,
        "workspace_is_stable_runtime": str(row[5]).startswith(str(ROOT / "runtime")),
        "belongs_to_kb": row[1] == KB_ID,
        "queryable_status": row[4] in {"ready", "active"},
    }
    (ROOT / "evaluation" / "phase10b3c" / "candidate_registration_check.json").write_text(json.dumps(registration, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    qdrant = AsyncQdrantClient(url="http://127.0.0.1:17333")
    info = await qdrant.get_collection(collections["chunks"])
    active_collections = json.loads(db.execute("select collections from vector_index_generations where id=?", (active,)).fetchone()[0])
    vector_check = {
        "collection_exists": True,
        "collection_name": collections["chunks"],
        "point_count": (await qdrant.count(collections["chunks"], exact=True)).count,
        "vector_dimension": info.config.params.vectors.size,
        "distance_metric": str(info.config.params.vectors.distance),
        "generation_match": GENERATION_NAME in collections["chunks"],
        "knowledge_base_match": KB_ID in collections["chunks"],
        "active_collection_unchanged": (await qdrant.count(active_collections["chunks"], exact=True)).count,
        "embedding_model": "text-embedding-v4",
    }
    await qdrant.close()
    (ROOT / "evaluation" / "phase10b3c" / "candidate_vector_index_check.json").write_text(json.dumps(vector_check, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    headers_admin = {"Authorization": f"Bearer {os.environ['ADMIN_API_KEY']}"}
    headers_service = {"Authorization": f"Bearer {os.environ['SERVICE_API_KEY']}"}
    endpoint = f"{BASE}/v1/knowledge-bases/{KB_ID}/generations/{GENERATION_ID}/query"
    route = {"openapi_route": endpoint.replace(GENERATION_ID, "{generation_id}"), "method": "POST", "response_schema": "QueryResponse"}
    with httpx.Client(timeout=60) as client:
        route["no_credentials"] = client.post(endpoint, json={"query": "测试", "history": []}).status_code
        route["service_forbidden"] = client.post(endpoint, headers=headers_service, json={"query": "测试", "history": []}).status_code
        route["admin_valid_candidate"] = client.post(endpoint, headers=headers_admin, json={"query": "轴承温度的安全要求是什么？", "history": []}).status_code
        route["admin_unknown_generation"] = client.post(f"{BASE}/v1/knowledge-bases/{KB_ID}/generations/{'0' * 32}/query", headers=headers_admin, json={"query": "测试", "history": []}).status_code
        route["admin_other_kb"] = client.post(f"{BASE}/v1/knowledge-bases/{'0' * 32}/generations/{GENERATION_ID}/query", headers=headers_admin, json={"query": "测试", "history": []}).status_code
    (ROOT / "evaluation" / "phase10b3c" / "candidate_query_route_check.json").write_text(json.dumps(route, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    db.close()
    print(json.dumps({"candidate_generation_id": GENERATION_ID, "route": route, "vector": vector_check}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
