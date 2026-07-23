"""End-to-end test: simulate the exact code path used by streamlit_app.py.

Creates a background-thread event loop + LightRAGService, then queries two
different questions in a row to reproduce the "locked to a different event
loop" bug.  Uses the same `run_coroutine_threadsafe` pattern as the app.
"""

import asyncio
import platform
import sys
import threading
import time
from pathlib import Path

if platform.system() == "Windows":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from industrial_rag.config import Settings
from industrial_rag.lightrag_service import LightRAGService

# --- Exact same logic as streamlit_app.py ---

_MODULE = sys.modules[__name__]


def _get_bg_state():
    state = getattr(_MODULE, "_bg_state", None)
    if state is None:
        return None
    loop = state["loop"]
    if loop.is_closed():
        return None
    return state


def _ensure_bg_state(settings):
    existing = _get_bg_state()
    if existing is not None:
        return existing
    loop = asyncio.new_event_loop()
    init_done = threading.Event()
    svc_box: list[LightRAGService] = []

    def _bg_thread():
        asyncio.set_event_loop(loop)
        loop.run_until_complete(_do_init())
        loop.run_forever()

    async def _do_init():
        svc = LightRAGService(settings)
        await svc.initialize()
        svc_box.append(svc)
        init_done.set()

    threading.Thread(target=_bg_thread, daemon=True, name="lightrag-loop").start()
    if not init_done.wait(timeout=180):
        raise RuntimeError("init failed")
    svc = svc_box[0]
    state = {"loop": loop, "svc": svc}
    _MODULE._bg_state = state
    return state


def ask_sync(settings, question, mode="mix"):
    state = _ensure_bg_state(settings)
    loop = state["loop"]
    svc = state["svc"]

    async def _query():
        return await svc.query(question, mode=mode)

    start = time.perf_counter()
    future = asyncio.run_coroutine_threadsafe(_query(), loop)
    result = future.result(timeout=180)
    elapsed = time.perf_counter() - start
    return result, elapsed


# --- Test ---
if __name__ == "__main__":
    settings = Settings.from_env()
    questions = [
        "离心泵启动前需要检查什么？",
        "机械密封失效有哪些可能原因？",
    ]

    for i, q in enumerate(questions):
        result, elapsed = ask_sync(settings, q, mode="mix")
        preview = result.answer[:80].replace("\n", " ")
        print(f"[OK] query #{i+1} -> citations={len(result.citations)} time={elapsed:.2f}s -> {preview}...")

    # Consecutive
    for i in range(2):
        result, elapsed = ask_sync(settings, "水泵不输送液体应该如何排查？", mode="local")
        preview = result.answer[:80].replace("\n", " ")
        print(f"[OK] consecutive #{i+1} -> citations={len(result.citations)} time={elapsed:.2f}s -> {preview}...")

    print("\nAll queries passed — no event-loop errors.")
