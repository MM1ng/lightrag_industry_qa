"""Phase 9 local-staging rehearsal: add -> validate -> promote -> query ->
replace -> validate -> promote -> query -> delete -> validate -> promote ->
query -> rollback -> query, with Qdrant/DB/log evidence collection."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

import pymupdf

API = "http://127.0.0.1:8110"
STAGING = Path(r"D:\industrial_energy_agent_staging")
REPO = Path(r"D:\industrial_energy_agent")
OUT = REPO / "evaluation" / "experiments" / "phase9" / "staging"
OUT.mkdir(parents=True, exist_ok=True)


def now() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%dT%H:%M:%S%z")


def load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    for line in (STAGING / "runtime" / "staging.env").read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.strip().startswith("#"):
            key, value = line.split("=", 1)
            env[key.strip()] = value.strip()
    return env


ENV = load_env()
HEADERS = {"Content-Type": "application/json", "x-debug-audit": "1"}
if ENV.get("SERVICE_API_KEY"):
    HEADERS["Authorization"] = "Bearer " + ENV["SERVICE_API_KEY"]


def call(method: str, path: str, *, data=None, files=None, timeout: float = 300.0) -> dict:
    import httpx

    with httpx.Client(base_url=API, timeout=timeout) as client:
        if files:
            resp = client.request(
                method, path, files=files, headers={"Authorization": HEADERS.get("Authorization", "")}
            )
        else:
            resp = client.request(method, path, json=data, headers=HEADERS)
        try:
            body = resp.json() if resp.content else {}
        except Exception:
            body = {"raw": resp.text[:500]}
        if isinstance(body, dict):
            body["_http"] = resp.status_code
        return body


def make_pdf(path: Path, text: str) -> bytes:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_textbox(pymupdf.Rect(50, 50, 540, 790), text, fontsize=11, fontname="china-s")
    doc.save(str(path))
    doc.close()
    return path.read_bytes()


def qdrant_snapshot() -> dict[str, int]:
    import urllib.request

    base = "http://127.0.0.1:16333"
    cols = json.loads(urllib.request.urlopen(base + "/collections", timeout=10).read().decode())
    out: dict[str, int] = {}
    for item in cols["result"]["collections"]:
        info = json.loads(
            urllib.request.urlopen(base + "/collections/" + item["name"], timeout=10).read().decode()
        )
        out[item["name"]] = info["result"]["points_count"]
    return out


def db_integrity() -> str:
    con = sqlite3.connect(str(STAGING / "runtime" / "industrial_rag_staging.db"))
    try:
        return con.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        con.close()


def query_kb(kb_id: str, question: str, timeout: float = 180.0) -> dict:
    return call(
        "POST",
        f"/v1/knowledge-bases/{kb_id}/query",
        data={"query": question},
        timeout=timeout,
    )


def kb_state(kb_id: str) -> dict:
    con = sqlite3.connect(str(STAGING / "runtime" / "industrial_rag_staging.db"))
    try:
        row = con.execute(
            "SELECT workspace_path, active_vector_generation_id FROM knowledge_bases WHERE id=?",
            (kb_id,),
        ).fetchone()
        return {"workspace_path": row[0] if row else None, "active_generation_id": row[1] if row else None}
    finally:
        con.close()


def main() -> int:
    record: dict = {
        "phase": "Phase 9-LS",
        "started_at": now(),
        "steps": [],
        "qdrant_snapshots": {},
    }
    steps = record["steps"]

    def step(name: str, fn):
        started = time.perf_counter()
        entry = {"step": name, "started_at": now()}
        try:
            result = fn()
            entry.update({"status": "passed", "result": result})
        except Exception as error:
            entry.update(
                {"status": "failed", "sanitized_error": str(error)[:800]}
            )
            raise
        finally:
            entry["finished_at"] = now()
            entry["duration_seconds"] = round(time.perf_counter() - started, 2)
            steps.append(entry)

    record["qdrant_snapshots"]["before"] = qdrant_snapshot()

    # 1. Create independent staging test KB
    def create_kb():
        resp = call("POST", "/v1/knowledge-bases", data={"name": "Phase9-Staging-Incremental-Test"})
        if resp.get("_http") != 201:
            raise RuntimeError(f"create kb failed: {resp}")
        return resp

    step("create_test_kb", create_kb)
    kb = steps[-1]["result"]
    kb_id = kb["id"]

    # 2. Point the test KB at the Qdrant backend in the staging DB copy.
    def set_backend():
        con = sqlite3.connect(str(STAGING / "runtime" / "industrial_rag_staging.db"))
        try:
            con.execute(
                "UPDATE knowledge_bases SET vector_backend='qdrant' WHERE id=?",
                (kb_id,),
            )
            con.commit()
        finally:
            con.close()
        return {"vector_backend": "qdrant", "kb_id": kb_id}

    step("set_kb_backend_qdrant", set_backend)

    pdf_dir = STAGING / "work" / "phase9_docs"
    pdf_dir.mkdir(parents=True, exist_ok=True)

    # 3. Add document v1
    v1_path = pdf_dir / "phase9_test_doc_v1.pdf"
    v1_bytes = make_pdf(v1_path, "P9-100 泵的最高工作温度为 99 摄氏度。")

    def add_v1():
        return call(
            "POST",
            f"/v1/knowledge-bases/{kb_id}/documents",
            files={"file": ("phase9_test_doc_v1.pdf", v1_bytes, "application/pdf")},
        )

    step("add_document_v1", add_v1)
    add1 = steps[-1]["result"]
    v1_doc_id = add1["document_id"]
    gen1_id = add1["candidate_generation_id"]
    record["document_v1_id"] = v1_doc_id
    record["generation_v1_id"] = gen1_id
    record["add_v1_status"] = add1["status"]

    # 4. Active must not see v1 yet (no active generation).
    def check_active_before_promote():
        result = query_kb(kb_id, "P9-100 泵的最高工作温度是多少？")
        return {
            "http": result.get("_http"),
            "status": result.get("status"),
            "answer_has_marker": "99 摄氏度" in (result.get("answer") or ""),
        }

    step("active_query_before_promote_v1", check_active_before_promote)

    # 5. Validate + promote v1
    def validate_v1():
        return call(
            "POST",
            f"/v1/knowledge-bases/{kb_id}/generations/{gen1_id}/validate",
            timeout=600,
        )

    step("validate_v1", validate_v1)
    record["validation_v1_passed"] = steps[-1]["result"].get("passed")

    def promote_v1():
        return call(
            "POST",
            f"/v1/knowledge-bases/{kb_id}/generations/{gen1_id}/promote",
            timeout=60,
        )

    step("promote_v1", promote_v1)
    record["kb_state_after_promote_v1"] = kb_state(kb_id)

    # 6. Active query returns v1 content
    def query_v1():
        return query_kb(kb_id, "P9-100 泵的最高工作温度是多少？")

    step("active_query_v1", query_v1)
    record["active_query_v1_has_marker"] = "99 摄氏度" in (
        steps[-1]["result"].get("answer") or ""
    )

    record["qdrant_snapshots"]["after_v1_promote"] = qdrant_snapshot()

    # 7. Replace with v2
    v2_path = pdf_dir / "phase9_test_doc_v2.pdf"
    v2_bytes = make_pdf(v2_path, "P9-100 泵的最高工作温度为 120 摄氏度。")

    def replace_v2():
        return call(
            "PUT",
            f"/v1/knowledge-bases/{kb_id}/documents/{v1_doc_id}",
            files={"file": ("phase9_test_doc_v2.pdf", v2_bytes, "application/pdf")},
        )

    step("replace_document_v2", replace_v2)
    replace1 = steps[-1]["result"]
    gen2_id = replace1["candidate_generation_id"]
    record["generation_v2_id"] = gen2_id
    record["replace_v2_status"] = replace1["status"]

    # 8. Active still v1 until promote
    def active_still_v1():
        return query_kb(kb_id, "P9-100 泵的最高工作温度是多少？")

    step("active_query_before_promote_v2", active_still_v1)
    record["active_before_v2_promote_has_old"] = "99 摄氏度" in (
        steps[-1]["result"].get("answer") or ""
    )
    record["active_before_v2_promote_has_new"] = "120 摄氏度" in (
        steps[-1]["result"].get("answer") or ""
    )

    def validate_v2():
        return call(
            "POST",
            f"/v1/knowledge-bases/{kb_id}/generations/{gen2_id}/validate",
            timeout=600,
        )

    step("validate_v2", validate_v2)
    record["validation_v2_passed"] = steps[-1]["result"].get("passed")

    def promote_v2():
        return call(
            "POST",
            f"/v1/knowledge-bases/{kb_id}/generations/{gen2_id}/promote",
            timeout=60,
        )

    step("promote_v2", promote_v2)
    record["kb_state_after_promote_v2"] = kb_state(kb_id)

    # 9. Only new version is returned (second query after runtime eviction is
    # the authoritative steady-state check).
    def query_v2():
        return query_kb(kb_id, "P9-100 泵的最高工作温度是多少？")

    step("active_query_v2_warmup", query_v2)
    step("active_query_v2", query_v2)
    v2_answer = steps[-1]["result"].get("answer") or ""
    record["query_v2_retrieved"] = (steps[-1]["result"].get("retrieved_chunk_ids") or [])[:5]
    record["query_v2_citations"] = (steps[-1]["result"].get("citations") or [])
    record["active_query_v2_has_new"] = "120 摄氏度" in v2_answer
    record["active_query_v2_has_old"] = "99 摄氏度" in v2_answer
    record["qdrant_snapshots"]["after_v2_promote"] = qdrant_snapshot()

    # 10. Delete document
    def delete_doc():
        return call("DELETE", f"/v1/knowledge-bases/{kb_id}/documents/{v1_doc_id}", timeout=600)

    step("delete_document", delete_doc)
    delete1 = steps[-1]["result"]
    gen3_id = delete1["candidate_generation_id"]
    record["generation_v3_id"] = gen3_id
    record["delete_status"] = delete1["status"]

    def validate_v3():
        return call(
            "POST",
            f"/v1/knowledge-bases/{kb_id}/generations/{gen3_id}/validate",
            timeout=600,
        )

    step("validate_v3", validate_v3)
    record["validation_v3_passed"] = steps[-1]["result"].get("passed")

    def promote_v3():
        return call(
            "POST",
            f"/v1/knowledge-bases/{kb_id}/generations/{gen3_id}/promote",
            timeout=60,
        )

    step("promote_v3", promote_v3)
    record["kb_state_after_promote_v3"] = kb_state(kb_id)

    # 11. Query must refuse after delete publish
    def query_deleted():
        return query_kb(kb_id, "P9-100 泵的最高工作温度是多少？")

    step("active_query_after_delete", query_deleted)
    deleted_answer = steps[-1]["result"].get("answer") or ""
    record["after_delete_refused"] = "未检索到充分依据" in deleted_answer
    record["qdrant_snapshots"]["after_delete_promote"] = qdrant_snapshot()

    # 12. Rollback to generation v1 (no re-parse/embedding)
    def rollback_v1():
        return call(
            "POST",
            f"/v1/knowledge-bases/{kb_id}/generations/{gen1_id}/rollback",
            timeout=60,
        )

    step("rollback_to_v1", rollback_v1)
    record["kb_state_after_rollback_v1"] = kb_state(kb_id)

    def query_after_rollback():
        return query_kb(kb_id, "P9-100 泵的最高工作温度是多少？")

    step("active_query_after_rollback", query_after_rollback)
    rollback_answer = steps[-1]["result"].get("answer") or ""
    record["after_rollback_has_v1"] = "99 摄氏度" in rollback_answer
    record["after_rollback_has_v2"] = "120 摄氏度" in rollback_answer
    record["qdrant_snapshots"]["after_rollback"] = qdrant_snapshot()

    # 13. DB integrity + generations summary
    record["db_integrity"] = db_integrity()
    generations = call("GET", f"/v1/knowledge-bases/{kb_id}/generations")
    record["generations"] = [
        {k: g[k] for k in ("id", "generation", "status")} for g in generations
    ]
    jobs = call("GET", f"/v1/knowledge-bases/{kb_id}/update-jobs")
    record["update_jobs"] = [
        {
            k: j[k]
            for k in (
                "job_id",
                "operation",
                "status",
                "candidate_generation_id",
                "metrics",
                "error_code",
            )
        }
        for j in jobs.get("items", [])
    ]
    record["finished_at"] = now()

    (OUT / "rehearsal_live.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({k: v for k, v in record.items() if k != "steps"}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
