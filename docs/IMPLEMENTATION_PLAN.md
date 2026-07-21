# EnergyOps Copilot MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and verify a production-shaped EnergyOps Copilot MVP that combines traceable pump-manual evidence, deterministic UCI hydraulic-cycle evidence, safety-first LangGraph routing, FastAPI, and a Streamlit thin client.

**Architecture:** The application is a Python 3.11 modular monolith behind FastAPI, while LightRAG runs as an independent REST server. Boundary dependencies are injected and have offline fakes; SQLite persists manifests, conversations, ingestion jobs, work-order drafts, and review records. All equipment-control requests fail closed, and no path can execute a real industrial action.

**Tech Stack:** Conda Python 3.11, FastAPI, Pydantic v2, OpenAI-compatible `qwen3.7-plus` and `text-embedding-v4`, LightRAG REST, LangChain, LangGraph, Pandas/NumPy/PyArrow, PyMuPDF with optional MinerU, SQLite, Streamlit, pytest, Ruff, and mypy.

---

## Execution rules

- Work only in `D:\industrial_energy_agent`; create the bootstrap commit on `main`, then perform all subsequent implementation on branch `feat/energyops-mvp`. The user explicitly requested in-place development, so no additional worktree is created.
- Use the new Conda environment `energyops-copilot`; invoke deterministic commands with `D:\anaconda\Scripts\conda.exe run -n energyops-copilot ...` so PowerShell activation state cannot select Python 3.12 accidentally.
- For every behavioral change: write one focused failing test, run it and confirm the expected failure, implement the minimum behavior, rerun the focused test, then run the relevant suite before committing.
- Never read or print the value of `DASHSCOPE_API_KEY`. Tests default to fakes and no network. External calls require an explicit `external` marker or command flag.
- Never modify `data/raw_dataset/hydraulic_systems/**` or `data/manuals/**`. Generate before/after path-size-SHA-256 manifests and write all outputs outside protected directories.
- Do not add BM25 in the MVP. Retrieval starts with the locked LightRAG modes; ADR-018 defines the future evaluation trigger.
- A task is complete only after its specification review and code-quality review are both approved and its commit exists.

## Planned file map

```text
app/                              # Streamlit-only HTTP client and pages
config/                           # LightRAG contract and service configuration
data/evaluation/                  # Tracked golden questions
data/processed/                   # Generated and ignored processing artifacts
data/synthetic/                   # Small tracked synthetic_demo records
docs/compatibility/               # Verified external API contracts
scripts/                          # User-facing inspection, processing, start, and smoke commands
src/industrial_energy_agent/
├── agents/                       # State, prompts, routing, diagnosis, decisions, safety
├── api/                          # FastAPI app, dependencies, errors, routes
├── config/                       # Pydantic settings
├── data_processing/              # Manifests, hydraulic processing, synthetic data
├── domain/                       # Enums, models, errors, deterministic safety rules
├── evaluation/                   # Golden-set models, metrics, evaluator
├── persistence/                  # SQLite initialization and focused repositories
├── providers/                    # OpenAI-compatible and fake providers
├── rag/                          # Parser, chunks, citations, REST adapter, ingestion
├── tools/                        # Six structured LangChain tools and registry
└── workflow/                     # Guarded LangGraph nodes, routing, graph, failure terminal
tests/
├── api/
├── evaluation/
├── fixtures/
├── integration/
├── smoke/
├── ui/
└── unit/
```

## Task 1: Bootstrap Conda, packaging, settings, logging, and Git

**Files:**
- Create: `.gitignore`
- Create: `.env.example`
- Create: `environment.yml`
- Create: `requirements.lock.txt`
- Create: `pyproject.toml`
- Create: `src/industrial_energy_agent/__init__.py`
- Create: `src/industrial_energy_agent/config/__init__.py`
- Create: `src/industrial_energy_agent/config/settings.py`
- Create: `src/industrial_energy_agent/logging_config.py`
- Create: `scripts/inspect_environment.py`
- Create: `tests/unit/config/test_settings.py`
- Create: `tests/unit/test_logging_config.py`

- [x] **Step 1: Create the dedicated Conda environment**

Run:

```powershell
D:\anaconda\Scripts\conda.exe create -n energyops-copilot python=3.11 pip -y
D:\anaconda\Scripts\conda.exe run -n energyops-copilot python --version
```

Expected: the second command prints `Python 3.11.x`; `conda env list` contains exactly one `energyops-copilot` entry.

- [x] **Step 2: Add reproducible packaging and ignore rules**

`environment.yml` must name `energyops-copilot` and constrain `python=3.11`. `pyproject.toml` must set `requires-python = ">=3.11,<3.12"`, expose the `energyops` CLI, and declare bounded direct dependencies for FastAPI, Pydantic v2, HTTPX, OpenAI, LangChain/LangGraph, NumPy/Pandas/PyArrow, PyMuPDF, Streamlit, and Tenacity. Add a `dev` optional group. Generate a platform-specific `requirements.lock.txt` from the verified environment so exact transitive versions are recorded in addition to bounded source constraints. LightRAG is deliberately excluded from the application environment and pinned in `environment.lightrag.yml` during Task 8.

`.gitignore` must ignore `.env`, caches, coverage, logs, SQLite files, model/index directories, `data/raw_dataset/**`, `data/manuals/**`, and `data/processed/**`, while leaving `data/README.md`, `data/evaluation/**`, and `data/synthetic/**` trackable.

- [x] **Step 3: Write failing settings and logging tests**

```python
def test_settings_falls_back_to_beijing_shared_url(monkeypatch):
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-only")
    settings = Settings(_env_file=None)
    assert str(settings.llm_base_url) == "https://dashscope.aliyuncs.com/compatible-mode/v1"


def test_explicit_business_workspace_url_takes_precedence(monkeypatch):
    workspace_url = "https://workspace-test.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
    monkeypatch.setenv("LLM_BASE_URL", workspace_url)
    settings = Settings(_env_file=None)
    assert str(settings.llm_base_url) == workspace_url


def test_redactor_removes_secrets_and_authorization():
    event = redact_event({"api_key": "secret", "Authorization": "Bearer token"})
    assert event == {"api_key": "***", "Authorization": "***"}
```

Run:

```powershell
D:\anaconda\Scripts\conda.exe run -n energyops-copilot python -m pytest tests/unit/config/test_settings.py tests/unit/test_logging_config.py -q
```

Expected RED: collection fails because `Settings` and `redact_event` do not exist.

- [x] **Step 4: Implement settings and logging boundaries**

`Settings` must use `SecretStr`, prefer explicit `LLM_API_KEY`, fall back to `DASHSCOPE_API_KEY` without copying it to disk, reject Base URLs ending in `/chat/completions` or `/embeddings`, and expose `chat_model="qwen3.7-plus"`, `embedding_model="text-embedding-v4"`, and `embedding_dimension=1024`. `redact_event` must recursively redact key-like fields and never serialize `SecretStr` values.

- [x] **Step 5: Install and verify the project**

Run:

```powershell
D:\anaconda\Scripts\conda.exe run -n energyops-copilot python -m pip install --upgrade pip
D:\anaconda\Scripts\conda.exe run -n energyops-copilot python -m pip install -e ".[dev]"
D:\anaconda\Scripts\conda.exe run -n energyops-copilot python -m pip freeze --exclude-editable > requirements.lock.txt
D:\anaconda\Scripts\conda.exe run -n energyops-copilot python -m pytest tests/unit/config/test_settings.py tests/unit/test_logging_config.py -q
D:\anaconda\Scripts\conda.exe run -n energyops-copilot python scripts/inspect_environment.py
```

Expected GREEN: focused tests pass; environment report shows Python 3.11 and prints only `SET/UNSET` for secrets.

- [x] **Step 6: Initialize Git safely and create the feature branch**

Run:

```powershell
git init -b main
git check-ignore data/manuals/t1739cn.pdf data/raw_dataset/hydraulic_systems/PS1.txt .env
git add .gitignore .env.example environment.yml pyproject.toml docs src scripts tests
git commit -m "chore: bootstrap EnergyOps Python 3.11 project"
git switch -c feat/energyops-mvp
```

Expected: all three sensitive paths are reported as ignored; the initial commit succeeds; current branch is `feat/energyops-mvp`.

## Task 2: Define domain, error, trace, review, and citation contracts

**Files:**
- Create: `src/industrial_energy_agent/domain/__init__.py`
- Create: `src/industrial_energy_agent/domain/enums.py`
- Create: `src/industrial_energy_agent/domain/errors.py`
- Create: `src/industrial_energy_agent/domain/models.py`
- Create: `src/industrial_energy_agent/rag/__init__.py`
- Create: `src/industrial_energy_agent/rag/citations.py`
- Create: `tests/unit/domain/test_models.py`
- Create: `tests/unit/rag/test_citations.py`

- [x] **Step 1: Write failing discriminated-union and invariant tests**

```python
def test_sensor_citation_requires_artifact_and_units():
    citation = SensorCitation(
        citation_id="sensor:1200:PS1__mean",
        dataset="UCI hydraulic_systems",
        cycle_id=1200,
        artifact_version="sha256:abc",
        features={"PS1__mean": 160.0},
        units={"PS1__mean": "bar"},
    )
    assert citation.source_type is SourceType.SENSOR


def test_work_order_cannot_be_executed():
    with pytest.raises(ValidationError):
        WorkOrderDraft.model_validate({"status": "DRAFT", "executed": True})
```

Expected RED: imports fail because the models are absent.

- [x] **Step 2: Implement exact enums and Pydantic v2 models**

Create `Intent`, `SourceType`, `RiskLevel`, `ActionMode`, `EvidenceGrade`, `ReviewType`, `ReviewStatus`, `WorkOrderStatus`, and `IngestJobStatus`. Define manual, sensor, and synthetic citations as a `source_type` discriminated union. Define `TraceEvent`, `StructuredError`, `DiagnosisRecord`, `WorkOrderDraft`, `RiskReview`, and `WorkOrderReview`; enforce `DRAFT`, `PENDING_REVIEW`, and `executed=false` in validators.

- [x] **Step 3: Implement server-side citation formatting and validation hooks**

```python
def format_manual_citation(value: ManualCitation) -> str:
    return f"[{value.document_title}，第{value.page_number}页，{value.chunk_id}]"
```

The formatter must not accept free-form model strings. Invalid source-specific fields must raise `CitationValidationError`.

- [x] **Step 4: Run focused tests and commit**

Run:

```powershell
D:\anaconda\Scripts\conda.exe run -n energyops-copilot python -m pytest tests/unit/domain tests/unit/rag/test_citations.py -q
git add src/industrial_energy_agent/domain src/industrial_energy_agent/rag tests/unit/domain tests/unit/rag/test_citations.py
git commit -m "feat: define domain and citation contracts"
```

Expected GREEN: all domain/citation tests pass.

## Task 3: Protect sources and inspect the real hydraulic dataset

**Files:**
- Create: `src/industrial_energy_agent/data_processing/__init__.py`
- Create: `src/industrial_energy_agent/data_processing/manifest.py`
- Create: `src/industrial_energy_agent/data_processing/hydraulic_schema.py`
- Create: `scripts/inspect_dataset.py`
- Create: `tests/unit/data_processing/test_manifest.py`
- Create: `tests/unit/data_processing/test_hydraulic_schema.py`
- Create: `tests/integration/data_processing/test_real_input_contract.py`

- [x] **Step 1: Write failing manifest and schema tests**

```python
def test_manifest_records_relative_path_size_and_sha256(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "a.txt").write_bytes(b"abc")
    entry = build_manifest(source)[0]
    assert entry.relative_path == "a.txt"
    assert entry.size_bytes == 3
    assert entry.sha256 == hashlib.sha256(b"abc").hexdigest()


def test_sensor_registry_contains_exactly_seventeen_matrices():
    assert len(SENSOR_SPECS) == 17
    assert SENSOR_SPECS["PS1"].points_per_cycle == 6000
    assert SENSOR_SPECS["FS1"].points_per_cycle == 600
    assert SENSOR_SPECS["TS1"].points_per_cycle == 60
```

Expected RED: manifest and sensor registry modules are missing.

- [x] **Step 2: Implement immutable manifests and strict file inspection**

`build_manifest` must traverse files only, use normalized relative paths, stream SHA-256 reads, and never write under the source. `inspect_hydraulic_dataset` must inspect the 17 named matrices and `profile.txt`, explicitly exclude `description.txt` and `documentation.txt`, and return structured errors with file/cycle/column locations for nonnumeric, nonfinite, short, long, or inconsistent rows.

- [x] **Step 3: Run unit tests, then the real-data contract test**

Run:

```powershell
D:\anaconda\Scripts\conda.exe run -n energyops-copilot python -m pytest tests/unit/data_processing/test_manifest.py tests/unit/data_processing/test_hydraulic_schema.py -q
D:\anaconda\Scripts\conda.exe run -n energyops-copilot python -m pytest tests/integration/data_processing/test_real_input_contract.py -q
D:\anaconda\Scripts\conda.exe run -n energyops-copilot python scripts/inspect_dataset.py
```

Expected: the report confirms 17 matrices, 2,205 cycles, 100/10/1 Hz point counts, five profile labels, and writes `data/processed/manifests/source_before.json` atomically.

- [x] **Step 4: Verify protected files are unchanged and commit**

Run `python scripts/inspect_dataset.py --compare-manifest data/processed/manifests/source_before.json`; expect zero changes. Commit as `feat: add immutable source data inspection`.

## Task 4: Build deterministic hydraulic cycle features and repository

**Files:**
- Create: `src/industrial_energy_agent/data_processing/hydraulic_loader.py`
- Create: `src/industrial_energy_agent/data_processing/feature_engineering.py`
- Create: `src/industrial_energy_agent/data_processing/hydraulic_pipeline.py`
- Create: `src/industrial_energy_agent/data_processing/sensor_repository.py`
- Create: `scripts/preprocess_hydraulic_data.py`
- Create: `tests/unit/data_processing/test_hydraulic_loader.py`
- Create: `tests/unit/data_processing/test_feature_engineering.py`
- Create: `tests/integration/data_processing/test_hydraulic_pipeline.py`

- [ ] **Step 1: Write failing mathematical feature tests**

```python
def test_cycle_features_use_population_std_and_real_seconds():
    values = np.array([1.0, 3.0, 5.0], dtype=np.float64)
    result = compute_cycle_features(values, sample_rate_hz=1.0)
    assert result["std"] == pytest.approx(np.std(values, ddof=0))
    assert result["range"] == 4.0
    assert result["trend"] == 4.0
    assert result["slope"] == pytest.approx(2.0)


def test_hundred_hz_slope_uses_seconds_not_sample_indices():
    values = np.arange(6000, dtype=np.float64) / 100.0
    assert compute_cycle_features(values, 100.0)["slope"] == pytest.approx(1.0)
```

Expected RED: `compute_cycle_features` is missing.

- [ ] **Step 2: Implement streaming strict loading and the ten features**

Load one cycle row at a time with `np.fromstring`, validate exact counts before computing `mean`, `std`, `min`, `max`, `median`, `range`, `first`, `last`, `trend`, and least-squares `slope`. Use `float64`; reject NaN/Inf and never interpolate.

- [ ] **Step 3: Implement atomic CSV/Parquet/report/dictionary output**

The pipeline must produce exactly the four specified files under `data/processed/hydraulic`, write temporary siblings first, `replace` only after all validations pass, and generate a stable `artifact_version` from source and processing fingerprints.

- [ ] **Step 4: Implement the read-only sensor repository**

`SensorRepository.get_cycle(1..2205)` returns one cycle summary and warns when `stable_flag=1`; `compare_cycles` accepts at least two unique IDs and returns deltas with units. It must read only processed Parquet and never load raw 6,000-point arrays during API requests.

- [ ] **Step 5: Run RED/GREEN, the real pipeline, and commit**

Run:

```powershell
D:\anaconda\Scripts\conda.exe run -n energyops-copilot python -m pytest tests/unit/data_processing -q
D:\anaconda\Scripts\conda.exe run -n energyops-copilot python scripts/preprocess_hydraulic_data.py
D:\anaconda\Scripts\conda.exe run -n energyops-copilot python -m pytest tests/integration/data_processing/test_hydraulic_pipeline.py -q
```

Expected: `cycle_features.csv` and `.parquet` both contain 2,205 rows and 176 columns, with matching keys/labels and floats within `rtol=1e-9, atol=1e-12`. Commit as `feat: build deterministic hydraulic cycle features`.

## Task 5: Generate deterministic synthetic demo business data

**Files:**
- Create: `src/industrial_energy_agent/data_processing/synthetic_generator.py`
- Create: `scripts/generate_synthetic_data.py`
- Create: `tests/unit/data_processing/test_synthetic_generator.py`
- Generate: `data/synthetic/equipment_master.csv`
- Generate: `data/synthetic/alarm_events.csv`
- Generate: `data/synthetic/fault_cases.json`
- Generate: `data/synthetic/work_orders.json`

- [x] **Step 1: Write a failing fixed-seed and provenance test**

```python
def test_every_synthetic_entity_is_labeled_and_non_executing(tmp_path):
    generated = generate_synthetic_data(tmp_path, seed=20260721)
    assert generated == generate_synthetic_data(tmp_path, seed=20260721)
    for entity in read_all_business_entities(tmp_path):
        assert entity["data_type"] == "synthetic_demo"
        assert "真实电厂" not in json.dumps(entity, ensure_ascii=False)
    for order in read_json(tmp_path / "work_orders.json"):
        assert order["status"] == "DRAFT"
        assert order["executed"] is False
```

Expected RED: generator functions are missing.

- [x] **Step 2: Implement the smallest deterministic generator**

Generate only the five agreed demo assets (`PUMP-001`, `PUMP-002`, `VALVE-001`, `COOLER-001`, `ACC-001`), stable alarm/case IDs, a generator version, and no real-company claim. Preserve `synthetic_demo` on every independent CSV row and JSON business object.

- [x] **Step 3: Generate, test, and commit**

Run focused tests and `python scripts/generate_synthetic_data.py`; inspect the four output schemas, then commit as `feat: generate labeled synthetic demo data`.

## Task 6: Add SQLite persistence, migrations, leases, and review schemas

**Files:**
- Create: `src/industrial_energy_agent/persistence/__init__.py`
- Create: `src/industrial_energy_agent/persistence/database.py`
- Create: `src/industrial_energy_agent/persistence/migrations/0001_initial.sql`
- Create: `src/industrial_energy_agent/persistence/session_repository.py`
- Create: `src/industrial_energy_agent/persistence/work_order_repository.py`
- Create: `src/industrial_energy_agent/persistence/review_repository.py`
- Create: `src/industrial_energy_agent/persistence/ingest_job_repository.py`
- Create: `tests/integration/persistence/test_database.py`
- Create: `tests/integration/persistence/test_sessions.py`
- Create: `tests/integration/persistence/test_reviews.py`
- Create: `tests/integration/persistence/test_ingest_jobs.py`

- [ ] **Step 1: Write failing lifecycle tests**

```python
def test_expired_running_job_is_reclaimed_but_reconcile_job_is_not(repository, clock):
    expired = repository.create_pending("doc-a", "key-a")
    repository.claim(expired.job_id, owner="worker-a", lease_until=clock.now())
    assert repository.claim_next(owner="worker-b").job_id == expired.job_id
    reconcile = repository.create_pending("doc-b", "key-b")
    repository.mark_reconcile_required(reconcile.job_id, "remote outcome unknown")
    assert repository.claim_next(owner="worker-b") is None


def test_risk_review_cannot_reference_work_order(repository):
    with pytest.raises(DomainValidationError):
        repository.create_risk_review(request_id="r1", work_order_id="wo-1")
```

Expected RED: repositories and migration are absent.

- [ ] **Step 2: Implement database initialization and focused repositories**

Enable foreign keys, WAL, and busy timeout. Use explicit transactions and parameterized SQL. Keep risk and work-order review tables/schemas distinct, allow only `PENDING_REVIEW → REVIEWED | REJECTED`, and preserve `DRAFT/executed=false` regardless of review outcome.

- [ ] **Step 3: Implement atomic lease behavior**

The ingest repository must atomically claim `PENDING` or expired `RUNNING`, store owner/expiry/attempt count, validate owner on heartbeat/finalization, and never let the ordinary worker claim `RECONCILE_REQUIRED`.

- [ ] **Step 4: Run persistence tests and commit**

Run `python -m pytest tests/integration/persistence -q`; expected all SQLite lifecycle tests pass against temporary databases. Commit as `feat: add SQLite persistence boundaries`.

## Task 7: Implement the OpenAI-compatible BaiLian provider

**Files:**
- Create: `src/industrial_energy_agent/providers/__init__.py`
- Create: `src/industrial_energy_agent/providers/base.py`
- Create: `src/industrial_energy_agent/providers/openai_compatible.py`
- Create: `src/industrial_energy_agent/providers/fake.py`
- Create: `scripts/smoke_bailian.py`
- Create: `tests/unit/providers/test_chat_provider.py`
- Create: `tests/unit/providers/test_embedding_provider.py`
- Create: `tests/smoke/test_bailian_provider.py`
- Create: `docs/compatibility/BAILIAN_OPENAI_COMPATIBLE.md`

- [ ] **Step 1: Write failing request-shape and validation tests**

```python
def test_embedding_uses_confirmed_openai_compatible_parameters(fake_client):
    provider = OpenAIEmbeddingProvider(fake_client, model="text-embedding-v4")
    vector = provider.embed(["泵轴承"])[0]
    assert fake_client.last_request["dimensions"] == 1024
    assert fake_client.last_request["encoding_format"] == "float"
    assert len(vector) == 1024


def test_json_mode_is_non_thinking_and_pydantic_validated(fake_client):
    provider = OpenAIChatProvider(fake_client, model="qwen3.7-plus")
    result = provider.complete_json("Return JSON intent", IntentDecision)
    assert fake_client.last_request["response_format"] == {"type": "json_object"}
    assert fake_client.last_request["extra_body"]["enable_thinking"] is False
    assert isinstance(result, IntentDecision)
```

Expected RED: providers are missing.

- [ ] **Step 2: Implement dependency-injected chat and embedding providers**

Support text, JSON Mode, and tool calls; validate every structured result with Pydantic; apply bounded timeout/retry for retryable transport/429/5xx failures only; never log prompts containing secrets or authorization headers. Do not claim strict JSON Schema support.

- [ ] **Step 3: Add deterministic fakes and offline tests**

Fakes return configured Pydantic objects and record sanitized call summaries. Default pytest must never instantiate the real client.

- [ ] **Step 4: Add an explicit real smoke command**

`smoke_bailian.py` must read `LLM_API_KEY` or `DASHSCOPE_API_KEY` without printing it and exercise text Chat, non-thinking JSON Mode, one harmless Function Calling decision, and a 1024-dimensional embedding only when flags request them.

- [ ] **Step 5: Verify and commit**

Run unit tests first. Then, after all offline checks pass, run:

```powershell
D:\anaconda\Scripts\conda.exe run -n energyops-copilot python scripts/smoke_bailian.py --chat --json-mode --function-call --embedding
```

Expected: four `PASS` summaries with model IDs and dimensions but no key/token content. Commit as `feat: add validated OpenAI-compatible providers`.

## Task 8: Lock and implement the LightRAG Server REST contract

**Files:**
- Modify: `.env.example`
- Modify: `src/industrial_energy_agent/config/settings.py`
- Modify: `tests/unit/config/test_settings.py`
- Create: `environment.lightrag.yml`
- Create: `config/lightrag_contract.json`
- Create: `src/industrial_energy_agent/rag/base.py`
- Create: `src/industrial_energy_agent/rag/lightrag_adapter.py`
- Create: `src/industrial_energy_agent/rag/fake_adapter.py`
- Create: `scripts/probe_lightrag_contract.py`
- Create: `scripts/start_lightrag.ps1`
- Create: `tests/unit/rag/test_lightrag_adapter.py`
- Create: `tests/smoke/test_lightrag_server.py`
- Create: `docs/compatibility/LIGHTRAG_REST.md`

- [ ] **Step 1: Create an isolated LightRAG service environment and probe before coding the adapter**

Create `energyops-lightrag` from `environment.lightrag.yml` with Python 3.11 and `lightrag-hku[api]==1.5.4`; do not install the release candidate or an unpinned `latest` image. Record package/version/CLI help, start the server on `127.0.0.1` in a hidden process, and probe `GET /health`, `POST /documents/text`, `POST /documents/texts`, `GET /documents/track_status/{track_id}`, `POST /documents/paginated`, and `POST /query/data`. Configure a dedicated `LIGHTRAG_API_KEY` that is not the BaiLian key and send it only through `X-API-Key` on protected routes. Record exact response fields in `config/lightrag_contract.json`; do not infer them from historical examples.

- [ ] **Step 2: Write failing adapter contract tests from the captured contract**

```python
@pytest.mark.parametrize("mode", ["local", "global", "hybrid", "naive", "mix"])
def test_search_maps_only_verified_modes(mode, httpx_mock, adapter):
    httpx_mock.add_response(
        json={
            "status": "success",
            "message": "ok",
            "data": {
                "chunks": [],
                "entities": [],
                "relationships": [],
                "references": [],
            },
            "metadata": {},
        }
    )
    result = adapter.search("轴承温度", mode=mode, top_k=5)
    assert result.mode == mode


def test_adapter_sends_dedicated_x_api_key(httpx_mock, adapter):
    httpx_mock.add_response(json={"status": "healthy"})
    adapter.health_check()
    request = httpx_mock.get_request()
    assert request.headers["X-API-Key"] == "test-only-lightrag"
    assert "Authorization" not in request.headers


def test_http_200_failure_envelope_is_not_an_empty_success(httpx_mock, adapter):
    httpx_mock.add_response(status_code=200, json={"status": "failure", "message": "failed"})
    with pytest.raises(RAGApplicationError):
        adapter.search("轴承温度", mode="hybrid", top_k=5)


def test_reconciliation_uses_paginated_documents_and_references(adapter, httpx_mock):
    # The implementation must combine track status, /documents/paginated, and
    # /query/data references before it confirms an ambiguous remote insert.
    result = adapter.reconcile_file_source("manual-marker.txt", track_id="insert_test")
    assert result.probes == {"track_status", "documents_paginated", "query_references"}


def test_business_package_never_imports_lightrag():
    imports = scan_imports(Path("src/industrial_energy_agent"))
    assert "lightrag" not in imports
```

Expected RED: adapter and verified mapping are missing.

- [ ] **Step 3: Implement the stable REST adapter and fake**

Expose `health_check`, `ingest_documents`, `track_status`, `search`, and `get_sources`. Send the dedicated internal token as `X-API-Key` and never reuse or log `DASHSCOPE_API_KEY`. Use `/query/data` so retrieval returns chunks/references without a second generated answer, and parse its real nested shape under `data.entities`, `data.relationships`, `data.chunks`, and `data.references` rather than assuming flat top-level arrays. Treat HTTP 200 with `status="failure"` as an application failure. `get_sources` must resolve LightRAG references through the local manifest because 1.5.4 has no independent sources endpoint. Normalize empty result, unavailable, invalid request, duplicate `file_source` 409, unauthorized, rate limit, and timeout errors. Do not silently map an unsupported public mode to another mode.

- [ ] **Step 4: Verify all required modes against the locked server**

Run `python scripts/probe_lightrag_contract.py --require-all-modes`; verify `local`, `global`, `hybrid`, `naive`, and `mix` are accepted by `/query/data`. Record that 1.5.4 has no arbitrary metadata filter, client-defined document ID, idempotent upsert, path GET, or independent sources endpoint; the adapter must never pretend those capabilities exist.

- [ ] **Step 5: Commit the verified boundary**

Run unit and marked smoke tests; commit package pin, contract, adapter, script, and compatibility note as `feat: integrate locked LightRAG REST contract`.

## Task 9: Parse both manuals with whole-document MinerU fallback

**Files:**
- Create: `src/industrial_energy_agent/rag/document_parser.py`
- Create: `src/industrial_energy_agent/rag/chunking.py`
- Create: `src/industrial_energy_agent/rag/parsers/__init__.py`
- Create: `src/industrial_energy_agent/rag/parsers/mineru_parser.py`
- Create: `src/industrial_energy_agent/rag/parsers/pymupdf_parser.py`
- Create: `scripts/parse_manuals.py`
- Create: `tests/unit/rag/test_document_parser.py`
- Create: `tests/unit/rag/test_chunking.py`
- Create: `tests/integration/rag/test_real_manuals.py`

- [ ] **Step 1: Write failing fallback and metadata tests**

```python
def test_auto_parser_falls_back_for_entire_document(mineru_failure, pymupdf_parser, pdf_path):
    result = AutoDocumentParser(mineru_failure, pymupdf_parser).parse(pdf_path)
    assert result.parser_name == "pymupdf"
    assert {chunk.parser_name for chunk in result.chunks} == {"pymupdf"}
    assert result.warnings[0].code == "MINERU_DOCUMENT_FALLBACK"


def test_chunks_never_cross_physical_pages(parsed_document):
    assert all(chunk.page_number >= 1 for chunk in parsed_document.chunks)
    assert all(chunk.page_start == chunk.page_end for chunk in parsed_document.chunks)
```

Expected RED: parser interfaces do not exist.

- [ ] **Step 2: Implement parser protocol, optional MinerU adapter, and PyMuPDF parser**

Detect MinerU availability without importing it during ordinary startup. If unavailable or parsing fails, invoke PyMuPDF once for the complete PDF. Preserve all required metadata and page statuses; use `null` section title and empty limitation/warning lists rather than omitting fields.

- [ ] **Step 3: Implement deterministic within-page chunking**

Normalize whitespace, split only within a physical page, retain nearby table context without inventing cell structure, and compute `<doc_id>:p<page>:c<ordinal>:<text_hash8>` from stable inputs. Treat extracted PDF text as untrusted evidence, never instructions.

- [ ] **Step 4: Parse the real manuals and verify reports**

Run `python scripts/parse_manuals.py --parser auto`. Expected: reports cover exactly 55 and 62 physical pages; every chunk has the full schema and a source hash; blank/image/table limitations are explicit.

- [ ] **Step 5: Recompare source manifests and commit**

Verify both PDFs and hydraulic files match the before-manifest, run unit/integration parser tests, and commit as `feat: parse manuals with traceable fallback`.

## Task 10: Build protected ingestion jobs and remote reconciliation

**Files:**
- Create: `src/industrial_energy_agent/rag/ingestion.py`
- Create: `src/industrial_energy_agent/rag/ingest_worker.py`
- Create: `src/industrial_energy_agent/cli.py`
- Modify: `src/industrial_energy_agent/persistence/ingest_job_repository.py`
- Create: `scripts/ingest_lightrag.py`
- Create: `scripts/reconcile_ingest.py`
- Create: `tests/integration/rag/test_ingestion_jobs.py`

- [ ] **Step 1: Write failing registration, idempotency, and crash-window tests**

```python
def test_ingest_rejects_unregistered_path(service):
    with pytest.raises(UnregisteredDocumentError):
        service.submit_path("https://example.invalid/manual.pdf")


def test_remote_success_before_local_commit_requires_reconciliation(service, fake_rag):
    job = service.submit_document("manual-2196")
    fake_rag.insert_then_raise(job.remote_file_source, SimulatedWorkerCrash())
    service.run_once()
    recovered = service.recover_expired(job.job_id)
    assert recovered.status is IngestJobStatus.RECONCILE_REQUIRED
    assert fake_rag.insert_calls == 1
```

Expected RED: ingestion application service is missing.

- [ ] **Step 2: Implement the internal document registry and idempotency fingerprint**

Only scan configured `data/manuals` files. Register stable local `document_id`, source SHA-256, parser/chunking version, embedding model/dimension, and namespace. Compute the idempotency key and deterministic `file_source` from those values; never accept a URL or caller-provided local path.

- [ ] **Step 3: Implement the worker and LightRAG 1.5.4-specific recovery rule**

Insert through `/documents/text(s)` and persist `track_id`. Because 1.5.4 does not provide client document IDs or reliable path lookup, treat a crash after request dispatch but before confirmed local commit as ambiguous. Reconcile by combining the saved track state, `POST /documents/paginated`, and `/query/data` references with the local manifest marker. A duplicate `file_source` 409 may confirm prior insertion only after those probes verify the matching marker; otherwise set `RECONCILE_REQUIRED` and do not replay.

- [ ] **Step 4: Implement API/shared-service-only CLI paths**

`scripts/ingest_lightrag.py` must call `POST /api/v1/ingest`; `energyops ingest-worker` may reuse the same application service in-process. A source scan test must prove no script constructs raw LightRAG HTTP requests.

- [ ] **Step 5: Run fault-injection tests and commit**

Run `python -m pytest tests/integration/rag/test_ingestion_jobs.py -q`; expected all duplicate, lease, crash, and reconciliation cases pass. Commit as `feat: add recoverable idempotent ingestion jobs`.

## Task 11: Implement six structured LangChain tools

**Files:**
- Create: `src/industrial_energy_agent/tools/__init__.py`
- Create: `src/industrial_energy_agent/tools/common.py`
- Create: `src/industrial_energy_agent/tools/knowledge_tools.py`
- Create: `src/industrial_energy_agent/tools/sensor_tools.py`
- Create: `src/industrial_energy_agent/tools/fault_case_tools.py`
- Create: `src/industrial_energy_agent/tools/safety_tools.py`
- Create: `src/industrial_energy_agent/tools/work_order_tools.py`
- Create: `src/industrial_energy_agent/tools/registry.py`
- Create: `tests/unit/tools/test_knowledge_tools.py`
- Create: `tests/unit/tools/test_sensor_tools.py`
- Create: `tests/unit/tools/test_fault_case_tools.py`
- Create: `tests/unit/tools/test_safety_tools.py`
- Create: `tests/unit/tools/test_work_order_tools.py`

- [ ] **Step 1: Write failing structured-tool tests**

```python
def test_cycle_2206_returns_structured_range_error(sensor_tool):
    result = sensor_tool.invoke({"cycle_id": 2206})
    assert result["ok"] is False
    assert result["error"]["code"] == "CYCLE_OUT_OF_RANGE"
    assert result["error"]["details"]["valid_range"] == [1, 2205]


def test_work_order_tool_never_sets_execution_fields(work_order_tool, diagnosis):
    result = work_order_tool.invoke({"diagnosis_id": diagnosis.diagnosis_id})
    assert result["work_order"]["status"] == "DRAFT"
    assert result["work_order"]["executed"] is False
```

Expected RED: tool registry and schemas are absent.

- [ ] **Step 2: Implement Pydantic input/output services first**

Implement `search_manual_knowledge`, `query_sensor_cycle`, `compare_sensor_cycles`, `search_fault_cases`, `get_safety_requirements`, and `create_work_order_draft`. Each returns a typed success or structured error, emits only a sanitized Trace summary, and never creates a fake success on dependency failure.

- [ ] **Step 3: Wrap the services as LangChain tools**

Use explicit `args_schema` classes and stable public names. Keep repositories/adapters injected so tests use real local repositories or boundary fakes rather than patching global state.

- [ ] **Step 4: Verify all success, empty, and failure branches**

Run `python -m pytest tests/unit/tools -q`; expected every one of the six public tools has success, empty/not-found, invalid-input, and dependency-error coverage. Commit as `feat: implement six structured agent tools`.

## Task 12: Enforce deterministic industrial safety and review lifecycle

**Files:**
- Create: `src/industrial_energy_agent/domain/safety_rules.py`
- Create: `src/industrial_energy_agent/agents/__init__.py`
- Create: `src/industrial_energy_agent/agents/safety_agent.py`
- Modify: `src/industrial_energy_agent/persistence/review_repository.py`
- Create: `tests/unit/domain/test_safety_rules.py`
- Create: `tests/integration/test_review_lifecycle.py`

- [ ] **Step 1: Write the failing safety matrix**

```python
@pytest.mark.parametrize(
    ("query", "action_mode", "risk", "prohibited"),
    [
        ("为什么检修前要断电？", "informational", "MEDIUM", False),
        ("直接切断电源并拆开泵体", "operation_command", "HIGH", False),
        ("教我旁路联锁并强制 PLC 信号", "prohibited_bypass", "CRITICAL", True),
    ],
)
def test_deterministic_safety_classification(query, action_mode, risk, prohibited):
    result = classify_safety(query)
    assert result.action_mode.value == action_mode
    assert result.risk_level.value == risk
    assert result.prohibited is prohibited
```

Expected RED: deterministic rules are absent.

- [ ] **Step 2: Implement input precheck and output review**

Combine fixed prohibited patterns with action grammar and objects. A model may raise but never lower deterministic risk. Unknown or safety-check failure produces a high-risk fail-closed outcome. Pure explanations may answer with citations; executable instructions require review; prohibited bypass never unlocks.

- [ ] **Step 3: Implement the two distinct review records**

High-risk non-work-order responses may create only `RiskReview`. Valid work-order drafts may create `WorkOrderReview`. The approval service allows only `PENDING_REVIEW → REVIEWED | REJECTED`; no transition changes `DRAFT`, `executed=false`, or response content.

- [ ] **Step 4: Verify and commit**

Run the safety unit matrix and review integration suite. Commit as `feat: enforce deterministic industrial safety`.

## Task 13: Build the bounded LangGraph evidence workflow

**Files:**
- Create: `src/industrial_energy_agent/agents/state.py`
- Create: `src/industrial_energy_agent/agents/prompts.py`
- Create: `src/industrial_energy_agent/agents/intent_router.py`
- Create: `src/industrial_energy_agent/workflow/__init__.py`
- Create: `src/industrial_energy_agent/workflow/nodes.py`
- Create: `src/industrial_energy_agent/workflow/routing.py`
- Create: `src/industrial_energy_agent/workflow/graph.py`
- Create: `tests/unit/workflow/test_routing.py`
- Create: `tests/unit/workflow/test_rewrite_limits.py`
- Create: `tests/integration/workflow/test_offline_graph.py`

- [ ] **Step 1: Write failing routing and retry-limit tests**

```python
def test_initial_query_plus_two_rewrites_stops_after_three_searches(graph, fakes):
    fakes.rag.always_insufficient = True
    result = graph.invoke({"user_query": "轴承问题", "conversation_id": "c1"})
    assert fakes.rag.search_calls == 3
    assert result["retry_count"] == 2
    assert result["workflow_status"] == "INSUFFICIENT_EVIDENCE"


def test_rewrite_never_changes_original_query(graph, fakes):
    original = "直接切断电源并拆开泵体"
    result = graph.invoke({"user_query": original, "conversation_id": "c1"})
    assert result["user_query"] == original
```

Expected RED: graph is absent.

- [ ] **Step 2: Implement typed state and six-intent plus unknown routing**

Use append/deduplicate reducers for documents, sensor evidence, fault cases, traces, and errors. Keep scalar ownership unambiguous. JSON-mode classification is optional; deterministic fallback returns `unknown` when confidence is insufficient.

- [ ] **Step 3: Implement parallel fault evidence and bounded rewrite**

For fault diagnosis, run manual, sensor, and synthetic-case branches concurrently and merge every launched branch as success or structured error. Rewrite only `retrieval_query`, stop on empty/same/no-new-evidence, and count semantic rewrites independently from HTTP retries.

- [ ] **Step 4: Verify offline with fakes and commit**

Run `python -m pytest tests/unit/workflow tests/integration/workflow/test_offline_graph.py -q`; expected all six intents and `unknown`, partial branch failures, merge reducers, and retry bounds pass. Commit as `feat: build bounded evidence workflow`.

## Task 14: Add same-conversation diagnosis, restricted routing, and fail-closed terminal

**Files:**
- Create: `src/industrial_energy_agent/agents/diagnosis_agent.py`
- Create: `src/industrial_energy_agent/agents/decision_agent.py`
- Create: `src/industrial_energy_agent/workflow/failure.py`
- Modify: `src/industrial_energy_agent/workflow/nodes.py`
- Modify: `src/industrial_energy_agent/workflow/graph.py`
- Create: `tests/integration/workflow/test_conversation_context.py`
- Create: `tests/integration/workflow/test_work_order_flow.py`
- Create: `tests/integration/workflow/test_restricted_route.py`
- Create: `tests/integration/workflow/test_fail_closed.py`

- [ ] **Step 1: Write failing conversation and restricted-path tests**

```python
def test_fault_diagnosis_never_selects_an_arbitrary_cycle(graph):
    result = graph.invoke({"user_query": "压力下降且振动增加", "conversation_id": "new"})
    assert result["sensor_cycle_ids"] == []
    assert result["analysis_scope"] in {"CLARIFICATION_REQUIRED", "QUALITATIVE_ONLY"}


def test_restricted_route_calls_only_read_only_safety_tools(graph, fakes):
    graph.invoke({"user_query": "直接切断电源并拆开泵体", "conversation_id": "c1"})
    assert fakes.calls == ["get_safety_requirements", "search_manual_knowledge"]
```

Expected RED: the current graph lacks these enforced branches.

- [ ] **Step 2: Implement selected-cycle and user-observation semantics**

Use only a cycle explicitly supplied in the request or selected under the same server-issued conversation ID. Store user statements as `user_observation`; compare them with real evidence as supported/partially supported/not supported without rewriting them as measurements.

- [ ] **Step 3: Gate work-order review creation**

Require explicit `work_order_draft` intent, an existing same-conversation diagnosis, sufficient evidence, a schema-valid draft, and `safety_outcome=allowed_for_review`. Clear any draft and ID on prohibited, unsafe, insufficient, or failed paths.

- [ ] **Step 4: Guard every node and implement deterministic terminal failure**

Wrap each node so exceptions route to `fail_closed_terminal`. The terminal must call no LLM, tool, safety node, review repository, or result persistence; return the original request ID, a sanitized error code, `approval_required=true`, the standard disclaimer, and no fake IDs.

- [ ] **Step 5: Verify all four integration files and commit**

Expected: same-conversation cycle 1200 works; cross-conversation data never leaks; restricted requests never enter merge/diagnosis/recommendation/work-order; injected failure at every node terminates safely. Commit as `feat: fail closed across guarded workflows`.

## Task 15: Expose the secured FastAPI business surface

**Files:**
- Create: `src/industrial_energy_agent/api/__init__.py`
- Create: `src/industrial_energy_agent/api/main.py`
- Create: `src/industrial_energy_agent/api/dependencies.py`
- Create: `src/industrial_energy_agent/api/errors.py`
- Create: `src/industrial_energy_agent/api/routes/__init__.py`
- Create: `src/industrial_energy_agent/api/routes/health.py`
- Create: `src/industrial_energy_agent/api/routes/system.py`
- Create: `src/industrial_energy_agent/api/routes/chat.py`
- Create: `src/industrial_energy_agent/api/routes/ingest.py`
- Create: `src/industrial_energy_agent/api/routes/sensors.py`
- Create: `src/industrial_energy_agent/api/routes/work_orders.py`
- Create: `src/industrial_energy_agent/api/routes/approvals.py`
- Create: `tests/api/test_health.py`
- Create: `tests/api/test_chat.py`
- Create: `tests/api/test_ingest.py`
- Create: `tests/api/test_sensors.py`
- Create: `tests/api/test_work_orders.py`
- Create: `tests/api/test_approvals.py`
- Create: `tests/api/test_openapi.py`

- [ ] **Step 1: Write failing API contract tests**

```python
def test_chat_contract(client):
    response = client.post("/api/v1/chat", json={"query": "查询第1200周期", "conversation_id": None})
    assert response.status_code == 200
    assert set(response.json()) >= {"answer", "citations", "trace", "risk_level", "approval_required"}


def test_ingest_requires_service_token(client):
    response = client.post("/api/v1/ingest", json={"document_ids": ["manual-2196"]})
    assert response.status_code == 401
```

Expected RED: API modules are absent.

- [ ] **Step 2: Implement dependency injection, lifespan, and unified errors**

Startup initializes SQLite and the bounded ingestion worker without requiring external services to be healthy. `/health` reports liveness; `/api/v1/system/info` reports sanitized dependency readiness. Exception handlers return `code`, safe `message`, `retryable`, and `request_id` with no stack/path/key.

- [ ] **Step 3: Implement all nine required endpoint groups**

Provide the exact health, system, chat, ingest, sensor-cycle, sensor-compare, work-order list/draft, and approval paths from the acceptance document. Ingest returns `202 + job_id`; cycle 2206 returns a range error; approval selects schema by `review_type`.

- [ ] **Step 4: Apply local-security controls**

Default bind is `127.0.0.1`; CORS is an explicit allowlist; ingest/approval require a constant-time service-token check; request limits reject empty/oversized input before tools.

- [ ] **Step 5: Run API tests, start, probe, and commit**

Run API tests, start `python -m uvicorn industrial_energy_agent.api.main:app --host 127.0.0.1 --port 8000`, poll `/health`, then stop cleanly. Commit as `feat: expose secured FastAPI surface`.

## Task 16: Build the four-page Streamlit thin client

**Files:**
- Create: `app/__init__.py`
- Create: `app/api_client.py`
- Create: `app/streamlit_app.py`
- Create: `app/pages/1_chat.py`
- Create: `app/pages/2_sensor_data.py`
- Create: `app/pages/3_fault_analysis.py`
- Create: `app/pages/4_work_order_draft.py`
- Create: `scripts/smoke_streamlit.py`
- Create: `tests/ui/test_api_client.py`
- Create: `tests/ui/test_streamlit_app.py`

- [ ] **Step 1: Write failing thin-client and state tests**

```python
def test_selected_cycle_is_sent_in_same_conversation(fake_api, session_state):
    session_state.conversation_id = "c-1200"
    session_state.selected_cycle_ids = [1200]
    submit_fault("压力下降", api=fake_api, state=session_state)
    assert fake_api.last_chat["conversation_id"] == "c-1200"
    assert fake_api.last_chat["selected_cycle_ids"] == [1200]


def test_clear_conversation_removes_selected_cycle(session_state):
    clear_conversation(session_state)
    assert session_state.conversation_id is None
    assert session_state.selected_cycle_ids == []
```

Expected RED: UI client/state helpers are missing.

- [ ] **Step 2: Implement a single HTTP client and four clear pages**

All UI calls go through `EnergyOpsApiClient`; no module imports provider, RAG, SQLite, or processed files. Chat shows clickable examples, answer, citations, and sanitized Trace. Sensor data shows cycle summary and a line chart over at least two cycles or an explicit range, with units.

- [ ] **Step 3: Implement evidence and work-order displays**

Fault page separates user observation, manual evidence, sensor evidence, and synthetic cases. Work-order page displays ID, equipment, symptom, causes, checks, safety, and review state; never render a control or label implying an action was executed.

- [ ] **Step 4: Run Streamlit AppTest and headless smoke**

Run UI tests and `python scripts/smoke_streamlit.py --url http://127.0.0.1:8501`; expected page titles, examples, cycle chart, evidence sections, and disclaimer are found. Commit as `feat: add four-page Streamlit client`.

## Task 17: Add the fixed golden set and evidence-aware evaluator

**Files:**
- Create: `data/evaluation/golden_questions.jsonl`
- Create: `src/industrial_energy_agent/evaluation/__init__.py`
- Create: `src/industrial_energy_agent/evaluation/models.py`
- Create: `src/industrial_energy_agent/evaluation/metrics.py`
- Create: `src/industrial_energy_agent/evaluation/evaluator.py`
- Create: `scripts/evaluate.py`
- Create: `tests/evaluation/test_golden_schema.py`
- Create: `tests/evaluation/test_metrics.py`
- Create: `tests/evaluation/test_multiturn_cases.py`

- [ ] **Step 1: Write failing golden-schema tests**

```python
def test_fixed_golden_set_has_thirty_unique_questions(golden_records):
    assert len(golden_records) == 30
    assert len({record.id for record in golden_records}) == 30
    assert set(record.expected_intent for record in golden_records) >= set(Intent)


def test_questions_23_and_24_are_real_multiturn_cases(golden_records):
    for question_id in {23, 24}:
        record = by_id(golden_records, question_id)
        assert record.test_mode == "multi_turn"
        assert len(record.setup_turns) >= 1
        assert record.context_assertions["same_conversation"] is True
```

Expected RED: golden file and evaluator models are missing.

- [ ] **Step 2: Create the exact 30 reviewed questions and references**

Use the fixed list from `MVP_ACCEPTANCE_CRITERIA.md`. Populate every schema field, mark questions 23/24 as setup-turn sequences executed by the real offline graph, and store human reference keywords/chunk IDs after Task 9 creates the real manifest. Never inject an artificial diagnosis directly into the last turn.

- [ ] **Step 3: Implement metrics without misleading denominators**

Compute intent accuracy, refusal, high-risk blocking, tool success, and manual/sensor/synthetic citation completeness separately. Return `N/A` for a zero denominator, include counts and failures, and treat fabricated citation, prohibited bypass, execution claim, or secret leak as an unconditional critical failure.

- [ ] **Step 4: Run threshold evaluation and commit**

Run `python scripts/evaluate.py --mode offline --fail-on-threshold`. Expected: report includes sample sizes, formulas, each failed record, and satisfies the agreed thresholds. Commit as `test: add golden evaluation gates`.

## Task 18: Finish documentation and run staged real acceptance

**Files:**
- Create: `README.md`
- Create: `data/README.md`
- Create: `docs/DATA_DICTIONARY.md`
- Create: `docs/API.md`
- Create: `docs/DEMO_GUIDE.md`
- Create: `docs/TROUBLESHOOTING.md`
- Create: `docs/MVP_ACCEPTANCE_REPORT.md`
- Create: `scripts/smoke_test.py`
- Create: `scripts/run_acceptance.ps1`
- Create: `scripts/scan_secrets.py`
- Create: `tests/docs/test_documentation_contract.py`

- [ ] **Step 1: Write failing documentation-contract tests**

The test must assert that README contains exact Windows commands for both Conda environments, install, `.env`, data inspection, PDF parsing, hydraulic processing, synthetic generation, LightRAG startup/ingest, FastAPI, Streamlit, tests, evaluation, smoke, and troubleshooting. Every numbered step must carry both labels: `必做/可选` and `需要/不需要 API Key`. `data/README.md` must describe placement, read-only protection, generated directories, Git policy, and synthetic provenance.

- [ ] **Step 2: Write user-focused documentation and compatibility notes**

Keep commands copyable in PowerShell and explain expected output plus which log/error fragment to share. Record verified LightRAG 1.5.4 gaps, MinerU availability/fallback, Docker daemon state, Python interpreter selection, and BaiLian region/Base URL rules.

- [ ] **Step 3: Run the complete offline quality gate**

Run:

```powershell
D:\anaconda\Scripts\conda.exe run -n energyops-copilot python -m pytest
D:\anaconda\Scripts\conda.exe run -n energyops-copilot ruff check .
D:\anaconda\Scripts\conda.exe run -n energyops-copilot ruff format --check .
D:\anaconda\Scripts\conda.exe run -n energyops-copilot mypy src
D:\anaconda\Scripts\conda.exe run -n energyops-copilot python scripts/inspect_dataset.py --verify-processed
D:\anaconda\Scripts\conda.exe run -n energyops-copilot python scripts/evaluate.py --mode offline --fail-on-threshold
D:\anaconda\Scripts\conda.exe run -n energyops-copilot python scripts/smoke_test.py --offline
D:\anaconda\Scripts\conda.exe run -n energyops-copilot python scripts/scan_secrets.py --source --logs --git-history --redact
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/run_acceptance.ps1 -Mode Offline
```

`run_acceptance.ps1` must orchestrate the named offline sub-gates and return a non-zero exit code as soon as any required sub-gate fails. The secret scanner reports only redacted file/line locations, never matched content or environment values. Expected: zero test/lint/type/secret-scan failures; dataset contract, offline smoke, and evaluation thresholds pass without network.

- [ ] **Step 4: Run external calls in cost-bounded stages**

First run BaiLian Chat/JSON/Function/Embedding smoke. Then start LightRAG, import one small chunk, query `/query/data`, validate references, and estimate complete block/token/call volume. Only after the estimate is recorded, submit the controlled full two-manual ingestion and verify a real sourced query. Finally run `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/run_acceptance.ps1 -Mode External`; it must stop non-zero on any failed required external gate. Never echo the key.

- [ ] **Step 5: Run the real API and Streamlit demo**

Start LightRAG, FastAPI, and Streamlit; execute the ten-step demo in the acceptance document, including same-conversation cycle 1200 diagnosis, valid draft review, restricted power-off request, Trace/citation display, and absence of any execution edge.

- [ ] **Step 6: Recheck source integrity and publish acceptance evidence**

Compare final and initial manifests byte-for-byte by path/size/SHA-256. Populate `MVP_ACCEPTANCE_REPORT.md` with commands, versions, model IDs, sample sizes, pass/fail counts, known limits, and external evidence. Do not use the phrase “MVP 已完成” unless G0–G10 all pass.

- [ ] **Step 7: Commit and request final review**

Commit as `docs: add verified setup and acceptance evidence`, run a full repository code review from the bootstrap commit to HEAD, fix every Critical/Important finding through TDD, rerun the complete gate, then use `superpowers:finishing-a-development-branch` to present merge options.

## Dependency order and checkpoints

```text
T1 → T2 → T3 → T4
T1 → T5
T2 → T6
T1 → T7
T1 → T8
T2 + T3 → T9
T6 + T8 + T9 → T10
T2 + T4 + T5 + T8 + T10 → T11
T2 + T6 → T12
T7 + T11 + T12 → T13 → T14
T4 + T6 + T10 + T14 → T15 → T16
T13 + T14 → T17
all tasks → T18
```

Checkpoints requiring fresh full offline verification occur after T4 (real data), T10 (external ingestion boundary), T14 (complete offline graph), T16 (user workflow), and T18 (final acceptance). External Smoke never substitutes for offline tests, and Fake success never substitutes for the real BaiLian/LightRAG gates.
