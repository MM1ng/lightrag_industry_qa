from app.chat_state import ChatClaim, ChatEvidence
from app.components.claims_panel import claim_models
from app.components.evidence_panel import evidence_panel_models
from app.utils.text_highlight import highlight_terms


def test_evidence_panel_is_bounded_and_hides_scores():
    item = ChatEvidence("E1", "cite_1", "manual.pdf", 3, "c1", excerpt="x" * 900)
    model = evidence_panel_models([item])[0]
    assert len(model["excerpt"]) == 600
    assert "score" not in model


def test_claim_panel_keeps_exact_evidence_mapping_and_safe_highlight():
    models = claim_models([ChatClaim("P1", "结论", ("cite_1",), ("E1",))])
    assert models[0]["citation_ids"] == ["cite_1"]
    assert highlight_terms("<script>温度</script>", ["温度"]) == "&lt;script&gt;**温度**&lt;/script&gt;"
