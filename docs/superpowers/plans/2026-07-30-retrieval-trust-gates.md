# Retrieval Trust Gates Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make answers abstain when evidence is weak, route uniquely identified manual questions to their source document, and return at most three traceable citations.

**Architecture:** A pure `evidence_policy` module converts LightRAG's structured evidence into ranked, document-routed candidates and a deterministic allow/refuse decision. `LightRAGService` runs this policy after `aquery_data`, builds generation context solely from the selected chunks, and only then calls a backend generation method. Evaluation reports add citation-count and document-routing metrics for the same 50-question golden set.

**Tech Stack:** Python 3.11, existing LightRAG 1.5.4/OpenAI-compatible BaiLian adapter, Kimi K2.6, pytest, Ruff.

## Global Constraints

- Do not add Reranker, Ragas, LangGraph, Agent orchestration, a database, or another model call.
- Keep PDF parsing, Embedding model `text-embedding-v4` (1024 dimensions), and graph construction unchanged.
- A successful answer has no more than three citations; a refusal has none and uses `INSUFFICIENT_EVIDENCE_MESSAGE` exactly.
- Route only when exactly one document is identified; otherwise retain cross-document candidates.
- Every generation prompt must contain only selected candidate chunks, never discarded evidence.
- Regression uses the existing 50-question `data/evaluation/industrial_pump_golden_set_50.jsonl` and Kimi K2.6 with the same root index.

---

## File Structure

| File | Responsibility |
| --- | --- |
| `src/industrial_rag/evidence_policy.py` | Pure document-alias routing, candidate extraction, token matching, ranking, and deterministic refusal decision. |
| `src/industrial_rag/lightrag_service.py` | Adapt official backend generation and call the policy between retrieval and generation. |
| `tests/test_evidence_policy.py` | Exhaustive no-network policy tests. |
| `tests/test_lightrag_service.py` | Verify selected-only context, top-three citations, and refusal without model generation. |
| `src/industrial_rag/evaluation.py` | Add citation-count and routing metrics to report data. |
| `tests/test_evaluation.py` | Verify the new aggregate metrics deterministically. |
| `README.md` | Document trust-gate behavior and acceptance command. |

## Task 1: Build the deterministic evidence policy

**Files:**
- Create: `src/industrial_rag/evidence_policy.py`
- Create: `tests/test_evidence_policy.py`

**Interfaces:**
- Produces `EvidenceCandidate(citation: Citation, text: str, rank: int)`.
- Produces `EvidenceDecision(allowed: bool, routed_document: str | None, selected: tuple[EvidenceCandidate, ...])`.
- Produces `select_evidence(question: str, payload: object, *, limit: int = 3) -> EvidenceDecision`.

- [ ] **Step 1: Write failing policy tests**

```python
def test_unique_sumit_alias_routes_and_returns_only_three_best_chunks() -> None:
    decision = select_evidence("SUMMIT 2196 长期存放要求？", _payload(sumit_chunks + desmi_chunks))
    assert decision.allowed is True
    assert decision.routed_document == "2196-ANSI-Manual-Chinese.pdf"
    assert len(decision.selected) == 3
    assert {item.citation.source_file for item in decision.selected} == {
        "2196-ANSI-Manual-Chinese.pdf"
    }


def test_unknown_question_with_unshared_terms_refuses() -> None:
    decision = select_evidence("火星基地零重力维护周期？", _payload(sumit_chunks))
    assert decision == EvidenceDecision(False, None, ())
```

Include tests for `DESMI`/`t1739` aliases, ambiguous cross-document questions, source metadata encoded in `file_path` or chunk header, stable original-rank tie-breaking, deduplication by full citation identity, and candidate text without two meaningful normalized question tokens refusing.

- [ ] **Step 2: Run the focused tests to confirm red state**

Run: `python -m pytest tests/test_evidence_policy.py -q`

Expected: FAIL because `industrial_rag.evidence_policy` does not exist.

- [ ] **Step 3: Implement the pure policy**

```python
DOCUMENT_ALIASES = {
    "2196-ANSI-Manual-Chinese.pdf": frozenset({"2196", "summit"}),
    "t1739cn.pdf": frozenset({"desmi", "t1739"}),
}


def select_evidence(question: str, payload: object, *, limit: int = 3) -> EvidenceDecision:
    routed_document = _unique_document_route(_tokens(question))
    candidates = _extract_candidates(payload)
    if routed_document is not None:
        candidates = [item for item in candidates if item.citation.source_file == routed_document]
    ranked = sorted(candidates, key=lambda item: (-_overlap(question, item.text), item.rank))
    selected = tuple(item for item in ranked if _overlap(question, item.text) >= 2)[:limit]
    return EvidenceDecision(bool(selected), routed_document, selected if selected else ())
```

`_tokens` must preserve Chinese runs, ASCII words, numeric values and model identifiers; it removes a small explicit stopword set. `_extract_candidates` reads `references` then `chunks`, resolves trusted metadata using the existing citation decoder, and preserves first appearance rank.

- [ ] **Step 4: Run policy tests and lint**

Run: `python -m pytest tests/test_evidence_policy.py -q`

Expected: PASS.

Run: `python -m ruff check src/industrial_rag/evidence_policy.py tests/test_evidence_policy.py`

Expected: PASS.

- [ ] **Step 5: Commit the pure decision boundary**

```powershell
git add src/industrial_rag/evidence_policy.py tests/test_evidence_policy.py
git commit -m "feat: add deterministic evidence trust policy"
```

## Task 2: Gate generation and expose selected citations

**Files:**
- Modify: `src/industrial_rag/lightrag_service.py:51-100,224-245`
- Modify: `tests/test_lightrag_service.py`

**Interfaces:**
- Extend `LightRAGBackend` with `async def generate(self, question: str, context: str, system_prompt: str) -> str`.
- `LightRAGService.query` consumes `EvidenceDecision` and returns `QueryResult` with only `decision.selected` citations.

- [ ] **Step 1: Write failing service tests**

```python
async def test_query_refuses_before_generation_when_policy_rejects() -> None:
    backend = FakeBackend(evidence=_no_overlap_payload())
    service = await _initialized_service(backend)
    result = await service.query("火星基地维护周期？")
    assert result.answer == INSUFFICIENT_EVIDENCE_MESSAGE
    assert result.citations == ()
    assert backend.generate_calls == []


async def test_query_generates_from_selected_chunks_and_returns_three_citations() -> None:
    backend = FakeBackend(evidence=_four_matching_chunks())
    service = await _initialized_service(backend)
    result = await service.query("SUMMIT 2196 入口管路如何布置？")
    assert len(result.citations) == 3
    assert "desmi" not in backend.generate_calls[0].context.casefold()
```

- [ ] **Step 2: Run the service tests to confirm red state**

Run: `python -m pytest tests/test_lightrag_service.py -q`

Expected: FAIL because the backend has no selected-context generation boundary.

- [ ] **Step 3: Add selected-context generation**

Implement `_OfficialBackend.generate` by calling the same `llm_model_func` used to construct LightRAG, with `question` as prompt and a system prompt containing the policy-selected chunk text. Do not call `aquery` after `aquery_data`, because it re-retrieves discarded context. Format context as repeated trusted headers from `encode_chunk_header(candidate.citation)` followed by each selected chunk's text.

```python
decision = select_evidence(question, evidence)
if not decision.allowed:
    return QueryResult(INSUFFICIENT_EVIDENCE_MESSAGE, (), mode)
context = _selected_context(decision.selected)
answer = (await self._backend.generate(question.strip(), context, system_prompt)).strip()
return QueryResult(answer or INSUFFICIENT_EVIDENCE_MESSAGE,
                   tuple(item.citation for item in decision.selected), mode)
```

The system prompt must preserve the current “only answer from evidence / fixed refusal” instructions and append the supplied selected context. If generation returns an empty answer, return the fixed refusal with empty citations.

- [ ] **Step 4: Run service tests and regression suite**

Run: `python -m pytest tests/test_lightrag_service.py tests/test_runtime.py tests/test_api.py -q`

Expected: PASS.

- [ ] **Step 5: Commit service gating**

```powershell
git add src/industrial_rag/lightrag_service.py tests/test_lightrag_service.py
git commit -m "feat: gate answers on selected evidence"
```

## Task 3: Extend evaluation and establish the post-change benchmark

**Files:**
- Modify: `src/industrial_rag/evaluation.py:37-205`
- Modify: `tests/test_evaluation.py`
- Modify: `README.md`

**Interfaces:**
- Add `average_citations_per_answer: float | None`, `max_citations_per_answer: int | None`, and `document_route_accuracy: float | None` to `EvaluationReport` and JSON output.
- Add `routed_document: str | None` to `CaseResult`, populated from returned citations when all citations share one source file.

- [ ] **Step 1: Write failing aggregation tests**

```python
def test_report_counts_citations_and_routes() -> None:
    report = evaluate_cases(_cases(), _query_with_two_same_document_citations)
    assert report.average_citations_per_answer == 2.0
    assert report.max_citations_per_answer == 2
    assert report.document_route_accuracy == 1.0
```

Include a mixed-source case and assert it lowers route accuracy only when a GoldenCase has all expected citations in exactly one document.

- [ ] **Step 2: Run test to confirm red state**

Run: `python -m pytest tests/test_evaluation.py -q`

Expected: FAIL because the new report fields do not exist.

- [ ] **Step 3: Implement metrics and document acceptance**

Compute citation counts only for completed non-refusal answers. Route accuracy denominator is evidence cases whose expected citations name exactly one document; numerator requires nonempty returned citations all from that document. Document the real benchmark command, thresholds, baseline, and Recall@5 rollback guard in README.

- [ ] **Step 4: Run complete offline verification**

Run: `python -m pytest -q`

Expected: PASS.

Run: `python -m ruff check .`

Expected: PASS.

- [ ] **Step 5: Run the controlled real benchmark**

Run:

```powershell
$env:LIGHTRAG_WORKING_DIR='D:\industrial_energy_agent\lightrag_storage'
python scripts\evaluate.py --real --golden data\evaluation\industrial_pump_golden_set_50.jsonl --output dist\industrial_pump_trust_gates_report.json
```

Expected: refusal rate at least 0.90, traceability at least 0.95, max citations at most 3, success rate at least 1.0, and Recall@5 no more than 0.05 below the Kimi baseline (0.70).

- [ ] **Step 6: Commit evaluation and documentation**

```powershell
git add src/industrial_rag/evaluation.py tests/test_evaluation.py README.md
git commit -m "feat: measure retrieval trust gates"
```

## Plan Self-Review

- Coverage: Task 1 implements deterministic routing, ranking, filtering and refusal; Task 2 makes generation consume selected-only context; Task 3 measures every agreed acceptance threshold and the Recall rollback guard.
- Scope: no parser, embedding, graph, Agent, Ragas or Reranker changes are included.
- Interface consistency: `EvidenceDecision.selected` feeds both selected-context generation and the returned `QueryResult.citations`; the same fields drive evaluation metrics.
