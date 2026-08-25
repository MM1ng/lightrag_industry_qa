from pathlib import Path

from scripts.phase12c1_answer_omission_analysis import (
    AUDIT_IDS,
    build_analysis,
)


ROOT = Path(__file__).resolve().parents[1]


def test_phase12c1_audits_the_six_phase12a_generation_samples():
    analysis = build_analysis(ROOT)

    assert tuple(item["question_id"] for item in analysis["cases"]) == AUDIT_IDS
    assert analysis["gate_a"]["confirmed_generation_omission_count"] == 0
    assert analysis["gate_a"]["status"] == "ROOT_CAUSE_RECLASSIFIED"


def test_phase12c1_confirms_runtime_evidence_is_hydrated_and_points_are_present():
    analysis = build_analysis(ROOT)

    assert all(case["runtime_evidence"]["missing_chunk_ids"] == [] for case in analysis["cases"])
    assert all(case["runtime_evidence"]["truncated_chunk_ids"] == [] for case in analysis["cases"])
    assert all(case["semantic_coverage"]["answerable_covered"] for case in analysis["cases"])


def test_phase12c1_d015_marks_hs_value_as_unanswerable_knowledge_gap():
    analysis = build_analysis(ROOT)
    case = next(item for item in analysis["cases"] if item["question_id"] == "D015")

    hs_point = next(
        point for point in case["semantic_coverage"]["points"] if point["point_id"] == "D015-p2-hs-value"
    )
    assert hs_point["answerable"] is False
    assert case["primary_root_cause"] == "knowledge_gap"
    assert case["generation_omission_confirmed"] is False


def test_phase12c1_surfaces_conflicts_between_funnel_flags_and_canonical_raw_text():
    analysis = build_analysis(ROOT)
    conflicts = [
        (case["question_id"], item["expected_point_id"])
        for case in analysis["cases"]
        for item in case["phase12a_funnel_consistency"]
        if item["flag_conflict"]
    ]

    assert len(conflicts) == 4
