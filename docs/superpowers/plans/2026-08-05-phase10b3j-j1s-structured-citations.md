# Phase 10B-3J-J1S Structured Citations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make one Qwen JSON-mode generation produce answer points and request-local source IDs, then deterministically construct precise citations without a second generation or retrieval.

**Architecture:** A focused policy module owns immutable Source/Requirement registries, Pydantic parsing, status derivation, atomic fallback classification, and public citation numbering. `LightRAGService` creates the registries only after unchanged retrieval/selection and maps a valid decision into existing `AnswerPoint`, `Citation`, and trace models.

**Tech Stack:** Python 3.11, Pydantic, FastAPI, existing LightRAG adapter, pytest, Ruff.

## Global Constraints

- `QA_STRUCTURED_CITATION_OUTPUT_ENABLED` defaults to false; all existing J1/J2/J3/J4 and legacy attribution controls are false.
- No Golden runtime data, no Validation/Holdout, Candidate mutation, TopK/chunk/embedding/rerank change, Supplemental Retrieval, second retrieval, or second generation.
- Normal responses expose no registry, raw provider output, generation ID, config SHA, or fallback diagnostics.
- Run J1S-0, J1S-1, and J1S-2 before the single Development 36-question run; Candidate activation and Phase 10C remain forbidden.

---

### Task 1: Build immutable registry and output decision types

**Files:**
- Create: `src/industrial_rag/structured_citation_output.py`
- Create: `tests/test_structured_citation_output.py`

**Interfaces:**
- Produces `SourceRegistry`, `RequirementRegistry`, `StructuredCitationDecision`, `validate_structured_citation_output`, and `render_public_citation_numbers`.
- Consumes ordered real child `EvidenceRef` and request requirement labels.

- [ ] **Step 1: Write failing tests**

```python
def test_source_ids_follow_child_provider_order() -> None:
    registry = SourceRegistry.from_evidence((_evidence("c2"), _evidence("c1")))
    assert registry.source_ids == ("S1", "S2")
    assert registry.resolve("S1").chunk_id == "c2"

def test_status_is_derived_from_points_and_unresolved_ids() -> None:
    decision = validate_structured_citation_output(
        '{"answer_points":[{"text":"答案","source_ids":["S1"]}],"unresolved_requirement_ids":["R1"]}',
        _registry(), _requirements("R1"), "g1")
    assert decision.status == "partial_answer"
```

- [ ] **Step 2: Verify red**

Run: `python -m pytest tests/test_structured_citation_output.py -q`

Expected: import failure because the contract module does not exist.

- [ ] **Step 3: Implement minimal contract**

```python
@dataclass(frozen=True, slots=True)
class SourceRegistry:
    entries: tuple[SourceEntry, ...]
    def resolve(self, source_id: str) -> EvidenceRef | None: ...

def validate_structured_citation_output(payload: str, registry: SourceRegistry,
    requirements: RequirementRegistry, generation_id: str) -> StructuredCitationDecision: ...
```

Use canonical JSON SHA-256, Pydantic fields `text`, `source_ids`, and unresolved IDs, and derive status from the approved invariant table.

- [ ] **Step 4: Verify green**

Run: `python -m pytest tests/test_structured_citation_output.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

Run: `git add src/industrial_rag/structured_citation_output.py tests/test_structured_citation_output.py; git commit -m "feat(phase10b3j): add structured citation contract"`

### Task 2: Classify atomic fallback and child identity

**Files:**
- Modify: `src/industrial_rag/structured_citation_output.py`
- Modify: `tests/test_structured_citation_output.py`

**Interfaces:**
- Produces `fallback_mode` of `None`, `fallback_to_j0_postprocessing`, or `safe_failure_no_second_generation`.

- [ ] **Step 1: Write failing tests**

```python
def test_unknown_source_uses_atomic_j0_postprocessing() -> None:
    assert _validate('{"answer_points":[{"text":"答案","source_ids":["S9"]}],"unresolved_requirement_ids":[]}').fallback_mode == "fallback_to_j0_postprocessing"

def test_missing_answer_points_uses_safe_failure() -> None:
    assert _validate('{"status":"success"}').fallback_mode == "safe_failure_no_second_generation"
```

- [ ] **Step 2: Verify red**

Run: `python -m pytest tests/test_structured_citation_output.py -q`

Expected: failure because initial parsing has no approved fallback split.

- [ ] **Step 3: Implement only approved fallback cases**

Core schema damage (invalid JSON/root/points/text) returns safe failure. Source identity, duplicate/excess source, wrong generation, Parent-to-Child failure, registry identity failure, or invalid Requirement IDs fallback atomically. Require one to two distinct sources and real child content SHA identity.

- [ ] **Step 4: Verify green**

Run: `python -m pytest tests/test_structured_citation_output.py -q`

Expected: PASS, including Parent-text and requirement cases.

- [ ] **Step 5: Commit**

Run: `git add src/industrial_rag/structured_citation_output.py tests/test_structured_citation_output.py; git commit -m "feat(phase10b3j): validate structured citation fallbacks"`

### Task 3: Add flag, config digest, version, and trace-only fields

**Files:**
- Modify: `src/industrial_rag/config.py`
- Modify: `src/industrial_rag/production_config.py`
- Modify: `src/industrial_rag/api.py`
- Modify: `src/industrial_rag/retrieval_trace.py`
- Modify: `tests/test_phase10b3i_feature_flags.py`
- Modify: `tests/test_api.py`

**Interfaces:**
- Adds `Settings.structured_citation_output_enabled` and sanitized `QA_STRUCTURED_CITATION_OUTPUT_ENABLED`.
- Adds only internal structured trace fields.

- [ ] **Step 1: Write failing tests**

```python
def test_structured_citation_flag_defaults_false_and_changes_digest() -> None:
    assert Settings.from_mapping(_values()).structured_citation_output_enabled is False
    assert Settings.from_mapping({**_values(), "QA_STRUCTURED_CITATION_OUTPUT_ENABLED": "true"}).phase10b3j_config_sha256 != Settings.from_mapping(_values()).phase10b3j_config_sha256

def test_normal_response_excludes_structured_registry_fields(client) -> None:
    assert "source_registry_sha256" not in client.post("/v1/query", json=_query()).json()
```

- [ ] **Step 2: Verify red**

Run: `python -m pytest tests/test_phase10b3i_feature_flags.py tests/test_api.py -q`

Expected: missing flag and trace assertions fail.

- [ ] **Step 3: Implement fail-closed configuration**

Parse the flag as false, add it to sanitized config/version/hash fields, and add non-secret trace defaults for registry counts/SHAs, raw/parsed output SHAs, validity, fallback, and generation-call count. Do not change public response models.

- [ ] **Step 4: Verify green**

Run: `python -m pytest tests/test_phase10b3i_feature_flags.py tests/test_api.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

Run: `git add src/industrial_rag/config.py src/industrial_rag/production_config.py src/industrial_rag/api.py src/industrial_rag/retrieval_trace.py tests/test_phase10b3i_feature_flags.py tests/test_api.py; git commit -m "feat(phase10b3j): add structured citation flag"`

### Task 4: Wire the one JSON-mode generation path

**Files:**
- Modify: `src/industrial_rag/lightrag_service.py`
- Create: `tests/test_phase10b3j_structured_runtime.py`

**Interfaces:**
- Extends `LightRAGBackend.generate` compatibly with optional JSON-mode options.
- Uses Task 1 decisions to build existing `AnswerPoint` and `Citation` values.

- [ ] **Step 1: Write failing runtime tests**

```python
async def test_valid_structured_output_calls_backend_once_and_maps_child() -> None:
    result = await _service(flag=True, reply=_valid_json()).query("问题", mode="mix")
    assert _backend.generate_calls == 1
    assert result.answer_points[0].evidence_ids == ("E1",)
    assert result.citations[0].chunk_id == "child-1"

async def test_unparseable_output_is_safe_without_second_call() -> None:
    result = await _service(flag=True, reply="not-json").query("问题", mode="mix")
    assert result.answer_status == "insufficient_evidence"
    assert _backend.generate_calls == 1
```

- [ ] **Step 2: Verify red**

Run: `python -m pytest tests/test_phase10b3j_structured_runtime.py -q`

Expected: missing structured runtime behavior fails.

- [ ] **Step 3: Implement minimal branch**

Build registries after unchanged selection; prompt sources in provider order; pass JSON-mode options to the existing one call; map valid decisions to existing models and first-use citation numbers. Citation-only invalid output runs J0 post-processing without re-generation. Core invalid output is safe insufficient evidence. Persist approved trace facts.

- [ ] **Step 4: Verify green**

Run: `python -m pytest tests/test_phase10b3j_structured_runtime.py tests/test_api.py -q`

Expected: PASS, including Active fixture and no second retrieval assertions.

- [ ] **Step 5: Commit**

Run: `git add src/industrial_rag/lightrag_service.py tests/test_phase10b3j_structured_runtime.py; git commit -m "feat(phase10b3j): generate structured source citations once"`

### Task 5: Gate evaluation execution and record result

**Files:**
- Create: `scripts/run_phase10b3j_j1s.py`
- Create: `tests/test_phase10b3j_j1s_runner.py`
- Modify: `evaluation/phase10b3j_goal/experiment_results.json`
- Modify: `evaluation/phase10b3j_goal/evaluation_manifest.json`

- [ ] **Step 1: Write a failing preflight guard**

```python
def test_development_rejected_without_three_passing_preflight_cases() -> None:
    with pytest.raises(RuntimeError, match="J1S-1 preflight"):
        assert_preflight_allows_development(_preflight(2, 3))
```

- [ ] **Step 2: Verify red**

Run: `python -m pytest tests/test_phase10b3j_j1s_runner.py -q`

Expected: missing runner gate fails.

- [ ] **Step 3: Implement guard and evaluate only in order**

The runner reads Development only, sets only the structured flag, records one-call/no-second-query facts, and refuses the 36-question run unless J1S-0/J1S-1/J1S-2 pass. Run all focused tests, `python -m pytest -q`, and `python -m ruff check .`; then execute J1S-0, J1S-1, J1S-2, and only if accepted one Development run.

- [ ] **Step 4: Commit and push evidence**

Run: `git add scripts/run_phase10b3j_j1s.py tests/test_phase10b3j_j1s_runner.py evaluation/phase10b3j_goal docs/phase-10b3j-goal-mode-final-report.md; git commit -m "test(phase10b3j): gate structured citation evaluation"; git push origin HEAD:codex/knowledge-qa-platform-design`
