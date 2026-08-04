"""Offline Phase 10B-3H coverage funnel audit (Development + Validation only)."""
# The audit is intentionally compact and data-only; keep its output logic
# readable without applying production-module formatting constraints.
# ruff: noqa
from __future__ import annotations
import hashlib, json, re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "evaluation" / "phase10b3h"
RESULT_FILES = (ROOT / "evaluation/phase10b3g/development_results.jsonl", ROOT / "evaluation/phase10b3g/validation_results.jsonl")
MAPPING = ROOT / "evaluation/phase10b3c/golden_evidence_mapping_g10b3c20260803.json"
FAILURE_STAGES = {"retrieval_missing","recalled_not_selected","completion_not_triggered","completion_rejected","provider_context_missing","generation_omitted","generation_refusal","grounding_false_negative","grounding_false_positive","citation_wrong_evidence","evaluation_mapping_error","unknown"}

def token_set(text: str) -> set[str]:
    return {x.lower() for x in re.findall(r"[A-Za-z0-9]+|[\u4e00-\u9fff]{2,}", text or "")}
def text_sha(text: str) -> str:
    return hashlib.sha256((text or "").encode()).hexdigest()
def load_rows() -> list[dict[str, Any]]:
    rows=[]
    for p in RESULT_FILES:
        for line in p.open(encoding="utf-8"):
            d=json.loads(line)
            if d.get("split") not in {"development","validation"}: raise ValueError(f"unexpected_split:{d.get('split')}")
            rows.append(d)
    if len(rows)!=52: raise ValueError(f"expected_52_rows:{len(rows)}")
    return rows
def rank_for(ids, initial):
    return next((x.get("initial_rank") for x in initial if x.get("chunk_id") in ids), None)
def mapped_ids(qid, expected, mapping):
    return {e["evidence_id"]:{mapping.get((qid,e["evidence_id"]),{}).get("candidate_chunk_id") or e.get("chunk_id")} for e in expected}

def point_state(point, expected_text, expected_ids, row):
    trace=row.get("trace") or {}; response=row.get("response") or {}
    initial=trace.get("initial_results",[]); initial_ids={x.get("chunk_id") for x in initial}
    selected_ids={x.get("chunk_id") for x in trace.get("final_selected_chunks",[])}
    completed_ids={x.get("chunk_id") for x in trace.get("completed_evidence",[])}
    initial_hit=bool(expected_ids & initial_ids); selected_hit=bool(expected_ids & selected_ids); completed_hit=bool(expected_ids & completed_ids); available=selected_hit or completed_hit
    citations={c.get("citation_id"):c for c in response.get("citations",[])}
    cited=set(); generated=False; retained=False
    for claim in response.get("claims",[]):
        chunks={citations[cid].get("chunk_id") for cid in claim.get("citation_ids",[]) if cid in citations and citations[cid].get("chunk_id")}
        cited |= chunks
        if chunks & expected_ids:
            generated=True; retained=bool(claim.get("evidence_ids"))
        elif token_set(claim.get("text","")) & token_set(expected_text):
            generated=True; retained=bool(claim.get("evidence_ids"))
    if not generated and token_set(response.get("answer","")) & token_set(expected_text): generated=True
    citation_ok=bool(cited & expected_ids)
    audit=trace.get("grounding_audit") or {}; status=response.get("status","")
    if not initial_hit: stage="retrieval_missing"
    elif not selected_hit and not completed_hit: stage="recalled_not_selected"
    elif not available: stage="provider_context_missing"
    elif not generated: stage="generation_refusal" if audit.get("generation_returned_refusal") or status=="insufficient_evidence" else "generation_omitted"
    elif not retained: stage="grounding_false_negative"
    elif not citation_ok: stage="citation_wrong_evidence"
    else: stage="unknown"
    reasons={"retrieval_missing":"mapped expected candidate chunk absent from initial results","recalled_not_selected":"initial candidate was not selected","generation_refusal":"provider refusal or insufficient evidence","generation_omitted":"available evidence did not become a claim","grounding_false_negative":"claim emitted without retained evidence mapping","citation_wrong_evidence":"claim citation did not match expected evidence"}
    return dict(initial_recalled=initial_hit, initial_best_rank=rank_for(expected_ids,initial), selected=selected_hit, completed=completed_hit, available_to_provider=available, generated=generated, grounding_retained=retained, final_emitted=generated and status in {"success","partial_answer"}, citation_correct=citation_ok, final_failure_stage=stage, final_failure_reason=reasons.get(stage,"no point-level failure observed"), candidate_expected_chunk_ids=sorted(x for x in expected_ids if x), provider_cited_chunk_ids=sorted(cited))

def build_matrix(rows,mapping_rows):
    mapping={(x["question_id"],x["evidence_id"]):x for x in mapping_rows}; out=[]
    for row in rows:
        g=row["golden"]; expected=g.get("expected_evidence",[])
        if not expected: continue
        by_eid=mapped_ids(row["question_id"],expected,mapping)
        for point in g.get("expected_answer_points",[]):
            ids={cid for eid in point.get("supported_by",[]) for cid in by_eid.get(eid,set()) if cid}
            state=point_state(point,point.get("text",""),ids,row)
            out.append({"question_id":row["question_id"],"split":row["split"],"expected_point_id":point["point_id"],"expected_point_text_sha256":text_sha(point.get("text","")),"expected_evidence_ids":point.get("supported_by",[]),**state,"response_status":row.get("response",{}).get("status")})
    return out

def disagreement(rows,mapping_rows):
    mapping={(x["question_id"],x["evidence_id"]):x for x in mapping_rows}; out=[]
    for row in rows:
        if row["question_id"] not in {"S006","S020","A001"} or not row["golden"].get("answerable"): continue
        g=row["golden"]; expected={mapping.get((row["question_id"],e["evidence_id"]),{}).get("candidate_chunk_id") or e.get("chunk_id") for e in g.get("expected_evidence",[])}; cited={c.get("chunk_id") for c in row["response"].get("citations",[])}
        if expected & cited: continue
        bound=[]
        for claim in row["response"].get("claims",[]):
            for cid in claim.get("citation_ids",[]):
                c=next((x for x in row["response"].get("citations",[]) if x.get("citation_id")==cid),None)
                if c: bound.append({"claim_id":claim.get("claim_id"),"claim_text":claim.get("text"),"citation_id":cid,"evidence_id":c.get("evidence_id"),"document_name":c.get("document_name"),"page":c.get("page"),"chunk_id":c.get("chunk_id")})
        cats=["mapping_correct_support_wrong","page_or_chunk_mismatch"]
        cats += ["numeric_mismatch","condition_mismatch"] if row["question_id"] in {"S006","A001"} else ["object_mismatch","lexical_false_positive"]
        public_evidence = row["response"].get("evidence", [])
        evidence_by_id = {x.get("evidence_id"): x for x in public_evidence}
        out.append({"question_id":row["question_id"],"split":row["split"],"external_failure":"unsupported_or_citation","classification":cats,"claims":bound,"response_evidence":public_evidence,"evidence_comparison":{"objects_and_models":{"claim_texts":[x.get("claim_text") for x in bound],"golden_texts":[x.get("evidence_text") for x in g.get("expected_evidence",[])]},"parameters_values_units_conditions":{"claim_texts":[x.get("claim_text") for x in bound],"evidence_excerpts":[evidence_by_id.get(x.get("evidence_id"),{}).get("excerpt") for x in bound],"golden_texts":[x.get("evidence_text") for x in g.get("expected_evidence",[])]},"pages_and_chunks":{"cited":[{k:x.get(k) for k in ("document_name","page","chunk_id")} for x in bound],"golden":[{k:x.get(k) for k in ("document_name","page_number","chunk_id")} for x in g.get("expected_evidence",[])]}},"golden_expected_evidence":g.get("expected_evidence",[]),"golden_expected_answer_points":g.get("expected_answer_points",[]),"response_answer":row["response"].get("answer"),"response_citations":row["response"].get("citations",[]),"comparison_note":"Claim-citation mapping is structurally present, but cited candidate chunk differs from fixed Candidate Golden mapping; support requires object/parameter/value/unit/condition review."})
    return out

def main():
    OUT.mkdir(parents=True,exist_ok=True); rows=load_rows(); mapping_rows=json.loads(MAPPING.read_text(encoding="utf-8"))["mapped_records"]; matrix=build_matrix(rows,mapping_rows)
    (OUT/"coverage_funnel_matrix.jsonl").write_text("\n".join(json.dumps(x,ensure_ascii=False) for x in matrix)+"\n",encoding="utf-8")
    counts=Counter(x["final_failure_stage"] for x in matrix); summary={"record_count":len(matrix),"question_count":len({x["question_id"] for x in matrix}),"point_count":len(matrix),"split_counts":dict(Counter(x["split"] for x in matrix)),"failure_stage_counts":dict(counts),"failure_stage_enum":sorted(FAILURE_STAGES),"holdout_used":False,"candidate_generation_id":"5bca792c08fcf2f7b08cbaed09b6d525"}
    (OUT/"coverage_funnel_summary.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    cases=disagreement(rows,mapping_rows); (OUT/"support_disagreement_cases.jsonl").write_text("\n".join(json.dumps(x,ensure_ascii=False) for x in cases)+"\n",encoding="utf-8"); (OUT/"support_disagreement_summary.json").write_text(json.dumps({"case_count":len(cases),"question_ids":[x["question_id"] for x in cases],"classification_counts":dict(Counter(c for x in cases for c in x["classification"])),"review_scope":"S006,S020,A001 only; no Holdout"},ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    buckets=defaultdict(list)
    for x in matrix: buckets[x["final_failure_stage"]].append({"question_id":x["question_id"],"expected_point_id":x["expected_point_id"],"reason":x["final_failure_reason"]})
    (OUT/"phase10b3h_failure_buckets.json").write_text(json.dumps({"failure_stage_enum":sorted(FAILURE_STAGES),"buckets":dict(buckets),"holdout_used":False},ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"matrix_points":len(matrix),"disagreements":len(cases),"failure_stages":dict(counts)},ensure_ascii=False))
if __name__=="__main__": main()
