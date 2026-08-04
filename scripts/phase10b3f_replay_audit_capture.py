"""Replay only audit-captured raw answers against the current Candidate registry."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from industrial_rag.answer_grounding import build_answer_plan
from industrial_rag.citation_formatter import Citation
from industrial_rag.evidence_policy import EvidenceCandidate

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "evaluation" / "phase10b3f"
REGISTRY = ROOT / "runtime" / "phase10b3c" / "kb_data" / "8fce4626859d44abb70a9ae5b0372cea" / "g10b3c20260803" / "context_registry"
CAPTURES = [OUT / "development_audit_capture.jsonl", OUT / "validation_audit_capture.jsonl"]
GENERATION_ID = "5bca792c08fcf2f7b08cbaed09b6d525"
GENERATION_NAME = "g10b3c20260803"


def jsonl(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def main() -> int:
    chunks = {row["chunk_id"]: row for row in jsonl(REGISTRY / "chunks.jsonl")}
    rows = [row for path in CAPTURES for row in jsonl(path)]
    integrity_rows: list[dict[str, object]] = []
    replay_rows: list[dict[str, object]] = []
    for row in rows:
        trace = row.get("trace") or {}
        audit = trace.get("grounding_audit") or {}
        selected = trace.get("final_selected_chunks") or []
        candidates: list[EvidenceCandidate] = []
        evidence_identity: list[dict[str, object]] = []
        mismatch = False
        for item in selected:
            chunk_id = item.get("chunk_id")
            chunk = chunks.get(chunk_id)
            if chunk is None or trace.get("generation_id") != GENERATION_ID or chunk.get("generation_id") != GENERATION_NAME or item.get("document_name") != chunk.get("document_name") or (int(item.get("page_number") or 0) != int(chunk.get("page_start") or 0) and int(item.get("page_number") or 0) != int(chunk.get("page_end") or 0)):
                mismatch = True
                continue
            content = str(chunk.get("content") or "")
            evidence_identity.append({
                "evidence_id": next((eid for decision in audit.get("point_decisions", []) for eid in decision.get("evidence_ids", []) if eid), None),
                "chunk_id": chunk_id,
                "document_id": chunk.get("document_id"),
                "generation_id": GENERATION_ID,
                "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            })
            candidates.append(EvidenceCandidate(Citation(str(chunk["document_name"]), int(chunk.get("page_start") or chunk.get("page_end") or 0), str(chunk_id)), content, int(item.get("initial_rank") or item.get("final_rank") or 0)))
        integrity_rows.append({
            "question_id": row["question_id"],
            "split": row["split"],
            "generation_id": trace.get("generation_id"),
            "context_registry_mismatch": mismatch,
            "evidence_identities": evidence_identity,
            "replay_eligible": bool(audit.get("replay_eligible")) and not mismatch,
            "replay_ineligible_reason": "context_registry_mismatch" if mismatch else audit.get("replay_ineligible_reason"),
        })
        response = row.get("response") or {}
        raw = str(audit.get("pre_grounding_answer") or "")
        if not audit.get("replay_eligible") or mismatch:
            replay_rows.append({"question_id": row["question_id"], "split": row["split"], "original_status": response.get("status"), "replayable": False, "replayed_status": response.get("status"), "reason": audit.get("replay_ineligible_reason") or "context_registry_mismatch", "recovered": False})
            continue
        citations = tuple(candidate.citation for candidate in candidates)
        grounded = build_answer_plan(raw, candidates, citations)
        replay_rows.append({"question_id": row["question_id"], "split": row["split"], "original_status": response.get("status"), "replayable": True, "replayed_status": grounded.status, "reason": "deterministic replay of captured pre-grounding answer", "recovered": response.get("status") in {"insufficient_evidence", "safety_blocked"} and grounded.status in {"success", "partial_answer"}, "unsupported_emitted_point_count": sum(point.support_status == "unsupported" for point in grounded.answer_points), "answer_points": [point.to_payload() for point in grounded.answer_points]})
    replayable = [row for row in replay_rows if row["replayable"]]
    recovered = [row for row in replay_rows if row["recovered"]]
    summary = {
        "total_count": len(replay_rows),
        "replayable_false_rejection_count": sum(row["replayable"] and row["original_status"] in {"insufficient_evidence", "safety_blocked"} for row in replay_rows),
        "recovered_false_rejection_count": len(recovered),
        "replayable_count": len(replayable),
        "unsupported_emitted_point_count": sum(row.get("unsupported_emitted_point_count", 0) for row in replay_rows),
        "context_registry_sha_mismatch_count": sum(row["context_registry_mismatch"] for row in integrity_rows),
        "final_metrics_valid": len(replay_rows) == 52 and not any(row["context_registry_mismatch"] for row in integrity_rows),
        "eligible_for_phase10b3e": len(recovered) > 0 and not any(row["context_registry_mismatch"] for row in integrity_rows),
    }
    (OUT / "replay_input_integrity.json").write_text(json.dumps({"generation_id": GENERATION_ID, "rows": integrity_rows, "context_registry_sha_mismatch_count": summary["context_registry_sha_mismatch_count"]}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (OUT / "replay_results.json").write_text(json.dumps({"summary": summary, "rows": replay_rows}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if summary["final_metrics_valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
