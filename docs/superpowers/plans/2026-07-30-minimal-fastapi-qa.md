# Minimal FastAPI QA Service Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose the existing LightRAG knowledge-base question-answering flow through a minimal, secure FastAPI service that the Streamlit client can call.

**Architecture:** `industrial_rag.api` owns HTTP-only concerns: lifespan, request/response schemas, optional Bearer authentication, request IDs, and safe error translation. It creates exactly one existing `LightRAGRuntime`, which retains ownership of the LightRAG event loop and serializes all queries. The API accepts frontend history for compatibility but deliberately does not store it or pass it to LightRAG.

**Tech Stack:** Python 3.11, FastAPI, Uvicorn, Pydantic v2 (via FastAPI), existing LightRAGRuntime, pytest, httpx/TestClient, Ruff.

## Global Constraints

- Reuse `LightRAGRuntime`; never create a second asyncio loop or call `LightRAGService` directly from FastAPI.
- Query mode is fixed to `mix`; public clients cannot select LightRAG modes.
- `SERVICE_API_KEY` is optional: absent means local unauthenticated development; present means `/v1/query` requires an exact Bearer credential.
- Keep `GET /readyz` unauthenticated and never expose model configuration, paths, secrets, stack traces, or raw upstream errors.
- Accept bounded `history` solely for current frontend compatibility; do not persist it or pass it to `runtime.query`.
- Do not modify or stage pre-existing untracked `app/` client/UI files during this feature.
- Default tests must not load a real LightRAG index, require a model key, or make network calls.

---

## File Structure

| File | Responsibility |
| --- | --- |
| `src/industrial_rag/config.py` | Load and retain the optional service Bearer secret without exposing it in `repr`. |
| `src/industrial_rag/api.py` | API factory, lifespan, HTTP models, auth dependency, success serialization, and public error mapping. |
| `tests/test_api.py` | Isolated API contract tests using an injected synchronous fake runtime. |
| `pyproject.toml` | Add runtime dependencies and include the API tests in Ruff checks. |
| `.env.example` | Document the optional local service key. |
| `README.md` | Document API/Streamlit startup, endpoints, auth behavior, and the v1 history boundary. |

## Task 1: Add service configuration and runtime dependencies

**Files:**
- Modify: `src/industrial_rag/config.py:26-75`
- Modify: `tests/test_config.py`
- Modify: `pyproject.toml:8-39`
- Modify: `.env.example:1-8`

**Interfaces:**
- Consumes: `Settings.from_mapping(values: Mapping[str, str | None]) -> Settings`.
- Produces: `Settings.service_api_key: str | None`, with its value excluded from the dataclass representation.
- Produces: installed `fastapi` and `uvicorn` runtime dependencies.

- [ ] **Step 1: Write failing configuration tests**

```python
def test_service_api_key_is_optional_and_trimmed() -> None:
    settings = Settings.from_mapping({**_valid_values(), "SERVICE_API_KEY": "  local-key  "})
    assert settings.service_api_key == "local-key"


def test_service_api_key_is_none_when_blank_and_never_in_repr() -> None:
    settings = Settings.from_mapping({**_valid_values(), "SERVICE_API_KEY": "   "})
    assert settings.service_api_key is None
    assert "SERVICE_API_KEY" not in repr(settings)
```

- [ ] **Step 2: Run the focused tests and verify they fail**

Run: `C:\Users\12189\.conda\envs\industrial-rag\python.exe -m pytest tests/test_config.py -q`

Expected: FAIL because `Settings` does not define `service_api_key`.

- [ ] **Step 3: Add the optional secret and dependencies**

```python
@dataclass(frozen=True, slots=True)
class Settings:
    api_key: str = field(repr=False)
    service_api_key: str | None = field(default=None, repr=False)
    # Existing model fields stay unchanged.

    @classmethod
    def from_mapping(cls, values: Mapping[str, str | None]) -> Settings:
        service_api_key = (values.get("SERVICE_API_KEY") or "").strip() or None
        # Existing validation remains unchanged.
        return cls(api_key=api_key, service_api_key=service_api_key, ...)
```

Add these exact dependency bounds to `pyproject.toml`:

```toml
"fastapi>=0.115,<1",
"uvicorn>=0.30,<1",
```

Append `SERVICE_API_KEY=` and its explanatory comment to `.env.example`. Add `"tests/test_api.py"` to Ruff's `include` list.

- [ ] **Step 4: Run focused configuration and dependency import tests**

Run: `C:\Users\12189\.conda\envs\industrial-rag\python.exe -m pytest tests/test_config.py -q`

Expected: PASS; `service_api_key` is optional, trimmed, and absent from `repr`.

- [ ] **Step 5: Commit the configuration boundary**

```powershell
git add src/industrial_rag/config.py tests/test_config.py pyproject.toml .env.example
git commit -m "feat: add optional API service configuration"
```

## Task 2: Define the FastAPI contract with failing tests

**Files:**
- Create: `tests/test_api.py`

**Interfaces:**
- Consumes: `create_app(*, settings: Settings, runtime_factory: Callable[[Settings], RuntimeLike]) -> FastAPI`.
- Produces: tests for `GET /readyz` and `POST /v1/query`.
- Defines fake runtime shape: `query(question: str, *, mode: Literal["mix"], timeout: float) -> tuple[QueryResult, float]` and `close() -> None`.

- [ ] **Step 1: Write failing API contract tests**

```python
def test_readyz_and_lifespan_close_runtime() -> None:
    runtime = FakeRuntime()
    with TestClient(_app(runtime)) as client:
        assert client.get("/readyz").json() == {"status": "ready"}
    assert runtime.close_calls == 1


def test_query_returns_traceable_citations_and_fixed_mix_mode() -> None:
    runtime = FakeRuntime(result=_success_result())
    with TestClient(_app(runtime)) as client:
        response = client.post(
            "/v1/query", json={"query": "E102 如何处理？", "history": [{"role": "user", "content": "x"}]}
        )
    body = response.json()
    assert response.status_code == 200
    assert body["status"] == "success"
    assert body["citations"][0]["page"] == 12
    assert runtime.calls == [("E102 如何处理？", "mix")]


@pytest.mark.parametrize("header", [None, "Bearer wrong", "Basic expected-key"])
def test_query_rejects_missing_or_invalid_bearer_key(header: str | None) -> None:
    with TestClient(_app(FakeRuntime(), service_api_key="expected-key")) as client:
        response = client.post("/v1/query", json={"query": "问题"}, headers=_headers(header))
    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHORIZED"
```

Also include tests for: evidence-insufficient response; empty/too-long query and malformed history returning `INVALID_REQUEST`; no configured API key allowing the request; runtime `RuntimeError("Query timed out after 1.0s")` mapping to 504/`TIMEOUT`; other `RuntimeError` mapping to 502/`UPSTREAM_UNAVAILABLE`; no raw exception string in the body; runtime factory failure producing 503 readiness; and history not appearing in fake runtime calls.

- [ ] **Step 2: Run the focused API tests and verify they fail**

Run: `C:\Users\12189\.conda\envs\industrial-rag\python.exe -m pytest tests/test_api.py -q`

Expected: FAIL with `ModuleNotFoundError: No module named 'industrial_rag.api'`.

- [ ] **Step 3: Commit the red contract**

```powershell
git add tests/test_api.py
git commit -m "test: define FastAPI QA contract"
```

## Task 3: Implement the minimal FastAPI adapter

**Files:**
- Create: `src/industrial_rag/api.py`
- Modify: `tests/test_api.py`

**Interfaces:**
- Consumes: `Settings`, `LightRAGRuntime`, `QueryResult`, `Citation`, and `INSUFFICIENT_EVIDENCE_MESSAGE`.
- Produces: module-level `app = create_app()` for `uvicorn industrial_rag.api:app` and injectable `create_app` for tests.

- [ ] **Step 1: Implement application construction and lifespan**

```python
RuntimeFactory = Callable[[Settings], RuntimeLike]


def create_app(
    *,
    settings: Settings | None = None,
    runtime_factory: RuntimeFactory = LightRAGRuntime,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(application: FastAPI):
        try:
            resolved_settings = settings or Settings.from_env()
            application.state.runtime = runtime_factory(resolved_settings)
            application.state.runtime_error = None
        except Exception:
            application.state.runtime = None
            application.state.runtime_error = True
        yield
        runtime = application.state.runtime
        if runtime is not None:
            runtime.close()

    return FastAPI(lifespan=lifespan)
```

Do not create the runtime or resolve environment settings during module import. `app = create_app()` must defer `Settings.from_env()` until lifespan startup so importing the module for tests or tooling never needs a real model key.

- [ ] **Step 2: Implement models, request IDs, and error responses**

```python
class QueryRequest(BaseModel):
    query: Annotated[str, Field(min_length=1, max_length=4000)]
    history: list[HistoryMessage] = Field(default_factory=list, max_length=10)


def public_error(request_id: str, code: str, message: str, retryable: bool) -> JSONResponse:
    return JSONResponse(
        status_code={"INVALID_REQUEST": 422, "UNAUTHORIZED": 401,
                     "INDEX_NOT_READY": 503, "TIMEOUT": 504,
                     "UPSTREAM_UNAVAILABLE": 502}[code],
        content={"request_id": request_id, "code": code, "message": message,
                 "retryable": retryable},
    )
```

Install a `RequestValidationError` handler that returns `INVALID_REQUEST` with a generated request ID. Limit each history message to roles `user`/`assistant` and content length 1–2000; do not log it or pass it to the runtime.

- [ ] **Step 3: Implement routes, auth, and result serialization**

```python
@router.get("/readyz")
def readyz(request: Request) -> dict[str, str]:
    if request.app.state.runtime is None:
        raise HTTPException(status_code=503, detail="not ready")
    return {"status": "ready"}


@router.post("/v1/query")
def query(payload: QueryRequest, request: Request) -> QueryResponse:
    runtime = require_ready_runtime(request)
    result, elapsed = runtime.query(payload.query.strip(), mode="mix", timeout=180.0)
    return serialize_result(result, elapsed)
```

Replace direct `HTTPException` output with the public error shape using the same request ID. Authentication compares exact Bearer credentials with `secrets.compare_digest`. For non-empty answers with citations, return `status="success"`, numbered citation IDs, and one aggregate claim. For the fixed insufficient-evidence answer or no citations, return `status="insufficient_evidence"` and no claims. Map a timeout string from the existing runtime to `TIMEOUT`; map every other runtime exception to `UPSTREAM_UNAVAILABLE`. Log only request ID, outcome status, and latency.

- [ ] **Step 4: Run the focused API tests and verify they pass**

Run: `C:\Users\12189\.conda\envs\industrial-rag\python.exe -m pytest tests/test_api.py -q`

Expected: PASS; no fake runtime call receives history and all client-visible failures use the documented error schema.

- [ ] **Step 5: Run lint for changed Python modules**

Run: `C:\Users\12189\.conda\envs\industrial-rag\python.exe -m ruff check src/industrial_rag/api.py src/industrial_rag/config.py tests/test_api.py tests/test_config.py`

Expected: PASS.

- [ ] **Step 6: Commit the HTTP service**

```powershell
git add src/industrial_rag/api.py tests/test_api.py
git commit -m "feat: expose LightRAG QA through FastAPI"
```

## Task 4: Document and validate the user-facing service

**Files:**
- Modify: `README.md:15-79`
- Modify: `.env.example:1-10`

**Interfaces:**
- Consumes: `uvicorn industrial_rag.api:app --host 127.0.0.1 --port 8000`.
- Produces: reproducible local launch instructions and an API request example that does not contain a real secret.

- [ ] **Step 1: Document the launch sequence and API boundary**

Add a README section with these commands:

```powershell
uvicorn industrial_rag.api:app --host 127.0.0.1 --port 8000
streamlit run app/streamlit_app.py
```

Document `GET /readyz`, `POST /v1/query`, optional `SERVICE_API_KEY`, response statuses `success` and `insufficient_evidence`, and the fact that v1 validates but does not persist or use `history`. Include a `curl`/PowerShell request sample with a placeholder token only.

- [ ] **Step 2: Run the full offline verification suite**

Run: `C:\Users\12189\.conda\envs\industrial-rag\python.exe -m pytest -q`

Expected: PASS with no model or network dependency.

Run: `C:\Users\12189\.conda\envs\industrial-rag\python.exe -m ruff check .`

Expected: PASS.

- [ ] **Step 3: Inspect the staged diff for accidental user UI changes**

Run: `git diff --cached --name-only`

Expected: only `README.md`, `.env.example`, `pyproject.toml`, `src/industrial_rag/config.py`, `src/industrial_rag/api.py`, `tests/test_config.py`, and `tests/test_api.py` from this feature; no existing untracked `app/` files.

- [ ] **Step 4: Commit documentation and final verification**

```powershell
git add README.md .env.example
git commit -m "docs: document FastAPI QA service"
```

## Plan Self-Review

- Spec coverage: Tasks 1–3 cover optional auth, lifespan ownership, safe errors, fixed `mix` retrieval, citations, request IDs, and history isolation. Task 4 covers local use and all required final checks.
- Placeholder scan: no 待补充项或跨任务的隐含步骤；测试用例、接口、预期结果和命令均已明确。
- Type consistency: `Settings.service_api_key`, `create_app`, fake runtime `query`/`close`, request `history`, and response status values use the same names throughout.
