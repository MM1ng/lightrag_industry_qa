# Asyncio Event Loop Bug — Root Cause Analysis

## Symptom

```
asyncio.locks.Lock object at 0x... [locked] is bound to a different event loop
```

The error occurs during consecutive LightRAG queries from the Streamlit app. The first query may succeed, but subsequent queries (especially after switching query modes or clicking example questions) trigger the error.

## Working Root-Cause Hypothesis

### What is confirmed

1. LightRAG 1.5.4 internally creates `asyncio` synchronization primitives (Lock, Event, etc.) inside its storage managers and embedding/LLM pipeline.
2. `asyncio` synchronization primitives become associated with a running event loop when they are first **used** (e.g., `await lock.acquire()`), not merely at construction time. A primitive first used on loop A records that loop as its owner. Subsequent use on loop B triggers: `"is bound to a different event loop"`.
3. Five previous commits attempted to keep LightRAG on a single persistent loop — all failed because they created the loop on the **main thread** and then handed it to a **background thread**.

### What is NOT yet confirmed (requires diagnostic logging)

- Which specific LightRAG internal lock is triggering the mismatch
- Whether Streamlit rerun creates a second module-level state (`_MODULE._bg_state`) path
- Whether `_get_bg_state()` returning `None` (loop closed) triggers creation of a second background loop

### How the current code works (and why it can fail)

`app/streamlit_app.py` lines 82-117 (`_ensure_bg_state`):

```python
loop = asyncio.new_event_loop()         # ← line 89: created on MAIN thread

def _bg_thread():
    asyncio.set_event_loop(loop)         # ← line 97: loop handed to bg thread
    loop.run_until_complete(_do_init())
    loop.run_forever()                   # ← line 101
```

In CPython:
- `BaseEventLoop.__init__` sets `self._thread_id = None`
- `BaseEventLoop._check_running()` sets `self._thread_id` during `run_forever()`
- But `asyncio` primitives track their owning loop by the loop object identity, not by `_thread_id`

The critical issue: the event loop is created on the **main thread**, then transferred to a **background thread**. While `asyncio.set_event_loop(loop)` in the bg thread does update the thread-local default loop, any asyncio primitive that was already associated with the loop (or any default-loop lookup that happened before `set_event_loop`) may capture a reference to a different loop.

Additionally, LightRAG internally may call `asyncio.get_event_loop()` during `__init__`, which on Python 3.11 creates a new default loop on whatever thread it runs on. If `LightRAG.__init__` was called before `asyncio.set_event_loop(loop)` on the bg thread, the constructor may create primitives bound to a temporary loop.

### Rejected hypotheses

1. **"`_thread_id` is set to main thread at `new_event_loop()` time"** — FALSIFIED. CPython sets `_thread_id = None` in `__init__` and only sets it during `_check_running()` inside `run_forever()`.
2. **"Windows `ProactorEventLoop` is the cause"** — PARTIALLY TRUE. The `ProactorEventLoop` does not support `asyncio.Lock` properly, but switching to `SelectorEventLoop` (commit `8d9b616`) only changed the loop type, not the lifecycle architecture.
3. **"Using `loop.run_forever()` is sufficient"** — FALSIFIED. It keeps the loop alive but doesn't fix where the loop and service objects are created.

## Root Cause (Working Hypothesis)

**LightRAG internal asyncio primitives are created/used on a different event loop than the one they were originally associated with.** This can happen when:

1. `asyncio.new_event_loop()` runs on the main thread (different thread context)
2. `LightRAG.__init__` is called inside `loop.run_until_complete()`, which means the loop IS running at that point — so primitives SHOULD be correctly bound. BUT if any code path inside `LightRAG.__init__` calls `asyncio.get_event_loop()` before `set_event_loop()` took effect, it gets a wrong loop.
3. Streamlit rerun causes `_get_bg_state()` to return `None` (e.g., `loop.is_closed()` check), triggering a second `_ensure_bg_state()` call that creates a second loop. Stale asyncio objects from the first loop still have locks bound to the old loop.
4. Module-level `@lru_cache` inside `lightrag` package caches LLM/embedding functions with internal primitives bound to whatever loop existed at cache time.

## Fix Architecture

### Core Invariant

**All asyncio objects (event loop, LightRAGService, LightRAG instance, asyncio.Lock) are created inside a single dedicated background thread. The main thread never creates or directly operates on any asyncio synchronization primitive.**

### What changes

1. **`src/industrial_rag/runtime.py`** (NEW) — `LightRAGRuntime` class
2. **`app/streamlit_app.py`** (MODIFY) — replaces `_MODULE`/`_ensure_bg_state`/`_ask_sync` with `st.cache_resource` + `LightRAGRuntime`

### What does NOT change

- `lightrag_service.py` — already correct (pure async service, no loop management)
- `config.py`, `document_parser.py`, `citation_formatter.py` — no changes

## Test Strategy

22 unit tests with a FakeLightRAGService that records:
- Construction thread ID and loop ID
- Initialize thread ID and loop ID
- Query thread IDs and loop IDs (per call)
- Close thread ID and loop ID
- Call counts (initialize, query, close)

### Required Proof

```
initialize_loop_id == query_calls[0].loop_id == ... == query_calls[9].loop_id
initialize_thread_id == query_calls[0].thread_id == ... == query_calls[9].thread_id
initialize_count == 1
close_loop_id == initialize_loop_id
```

## Reproduction Steps

```bash
# Start Streamlit
streamlit run app/streamlit_app.py

# Query 1 (succeeds)
离心泵启动前需要检查什么？  →  OK

# Query 2 (fails with current code)
轴承温度过高可能是什么原因？  →  "is bound to a different event loop"
```

## Rejected Fixes (5 previous commits)

| Commit | Attempt | Why insufficient |
|--------|---------|------------------|
| `d22cf01` | Dedicated bg thread | Loop still created on main thread |
| `8d9b616` | WindowsSelectorEventLoopPolicy | Changed loop type, not lifecycle |
| `e6b77b9` | 4-mode smoke test | Test infra, not a fix |
| `5642a1d` | `run_forever()` | Kept loop alive but didn't fix creation location |
| `0e6ca90` | `threading.Event` init sync | Better sync but same creation-location bug |
