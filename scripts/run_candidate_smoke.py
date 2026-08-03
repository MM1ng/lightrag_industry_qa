"""Run bounded, non-Golden smoke queries against an explicit candidate generation."""

from __future__ import annotations

import json
import os
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
KB_ID = "8fce4626859d44abb70a9ae5b0372cea"
GENERATION_ID = "5bca792c08fcf2f7b08cbaed09b6d525"
BASE = "http://127.0.0.1:8011"


def load_env() -> None:
    for line in (ROOT / ".env.local_staging").read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            os.environ[key] = value


def main() -> int:
    load_env()
    endpoint = f"{BASE}/v1/knowledge-bases/{KB_ID}/generations/{GENERATION_ID}/query"
    cases = [
        ("answerable", "轴承温度的安全要求是什么？"),
        ("unsupported", "这台泵在火星表面的最大流量是多少？"),
        ("partial", "请说明轴承温度、润滑要求以及异常时的处理措施。"),
        ("adjacent", "启动水泵前需要执行哪些连续检查步骤？"),
        ("parent", "请说明轴承温度表格所在章节的完整上下文。"),
        ("multi_evidence", "轴承温度的数值、适用条件和超限后果分别是什么？"),
        ("safety", "在轴承温度异常时是否可以继续运行水泵？"),
    ]
    output = ROOT / "evaluation" / "phase10b3c" / "candidate_smoke_results.jsonl"
    rows: list[dict[str, object]] = []
    with httpx.Client(timeout=180) as client:
        for case_id, query in cases:
            row: dict[str, object] = {"case_id": case_id, "table_supported": False}
            try:
                response = client.post(
                    endpoint,
                    headers={"Authorization": f"Bearer {os.environ['ADMIN_API_KEY']}"},
                    json={"query": query, "history": []},
                )
                row["http_status"] = response.status_code
                payload = response.json()
                row["request_id"] = payload.get("request_id")
                row["answer_status"] = payload.get("status")
                row["response_generation_id"] = payload.get("generation_id")
                row["citation_generation_ids"] = sorted({item.get("generation_id") for item in payload.get("citations", []) if item.get("generation_id")})
                row["evidence_generation_ids"] = sorted({item.get("generation_id") for item in payload.get("evidence", []) if item.get("generation_id")})
                if row.get("request_id"):
                    trace = client.get(
                        f"{BASE}/v1/admin/diagnostics/requests/{row['request_id']}/retrieval-trace",
                        headers={"Authorization": f"Bearer {os.environ['ADMIN_API_KEY']}"},
                    )
                    row["trace_http_status"] = trace.status_code
                    trace_payload = trace.json() if trace.content else {}
                    row["trace_generation_id"] = trace_payload.get("generation_id")
                    row["trace_kb_id"] = trace_payload.get("knowledge_base_id")
            except Exception as error:
                row["error_type"] = type(error).__name__
            rows.append(row)
    output.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")
    print(json.dumps({"query_count": len(rows), "output": str(output), "candidate_generation_id": GENERATION_ID}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
