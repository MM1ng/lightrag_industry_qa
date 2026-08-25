r"""Synchronous bridge between Streamlit and LightRAG's async API.

Owns a daemon background thread that hosts a single asyncio event loop.
The event loop, the LightRAGService, and all asyncio primitives (Lock, etc.)
are created INSIDE that background thread.  The main thread holds zero
asyncio synchronization primitives — it only uses :class:`threading.Event`
(wait) and :class:`concurrent.futures.Future` (result) for cross-thread
communication.

Invariant (never violated):
    The main thread NEVER calls :func:`asyncio.new_event_loop`,
    :func:`asyncio.Future`, :func:`asyncio.Lock`, :func:`asyncio.Event`,
    or :func:`asyncio.get_event_loop`.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextlib
import threading
import time
from collections.abc import Callable
from typing import Any

from industrial_rag.config import Settings
from industrial_rag.lightrag_service import LightRAGService, QueryMode, QueryResult


class LightRAGRuntime:
    """Thread-safe bridge between synchronous Streamlit and async LightRAG.

    Owns a daemon background thread that hosts a single asyncio event loop.
    Exactly one instance per Streamlit process (enforced by ``st.cache_resource``).
    """

    def __init__(
        self,
        settings: Settings,
        *,
        service_factory: Callable[[Settings], Any] | None = None,
        init_timeout: float = 180.0,
    ) -> None:
        self._settings = settings
        self._service_factory: Callable[[Settings], Any] = service_factory or LightRAGService
        self._init_timeout = init_timeout

        # Cross-thread synchronization — ONLY threading primitives on main thread.
        self._init_done = threading.Event()
        self._stopped = threading.Event()
        self._error_box: list[BaseException] = []

        # Set by background thread after event loop creation.
        self._loop: asyncio.AbstractEventLoop | None = None
        self._svc: Any = None
        self._query_lock: asyncio.Lock | None = None
        self._init_task: asyncio.Task[None] | None = None
        self._bg_thread: threading.Thread
        self._closed: bool = False

        # Start the background thread and wait for init to complete.
        self._bg_thread = threading.Thread(
            target=self._bg_entry,
            daemon=False,  # joinable — not silently abandoned
            name="lightrag-runtime",
        )
        self._bg_thread.start()

        # Block until init succeeds, fails, or times out.
        if not self._init_done.wait(timeout=self._init_timeout):
            self._handle_init_timeout()
        if self._error_box:
            self._propagate_init_error()

    # ------------------------------------------------------------------
    # Public API (callable from the main / Streamlit thread)
    # ------------------------------------------------------------------

    def query(
        self,
        question: str,
        *,
        mode: QueryMode = "mix",
        top_k: int = 12,
        chunk_top_k: int = 20,
        timeout: float = 180.0,
    ) -> tuple[QueryResult, float]:
        """Submit a query to the background loop and block for the result."""
        loop = self._loop
        if self._closed or loop is None or loop.is_closed():
            raise RuntimeError("LightRAG runtime is closed or not running")

        coro = self._query_async(
            question, mode=mode, top_k=top_k, chunk_top_k=chunk_top_k
        )
        future = asyncio.run_coroutine_threadsafe(coro, loop)
        start = time.perf_counter()
        try:
            result = future.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            future.cancel()
            raise RuntimeError(f"Query timed out after {timeout:.1f}s") from None
        elapsed = time.perf_counter() - start
        return result, elapsed

    def close(self, *, timeout: float = 30.0) -> None:
        """Shut down the background loop and release resources.  Idempotent."""
        loop = self._loop
        if self._closed or loop is None or loop.is_closed():
            return

        # 1. Best-effort service shutdown on bg loop.
        try:
            shutdown_fut = asyncio.run_coroutine_threadsafe(self._shutdown_service(), loop)
            shutdown_fut.result(timeout=min(timeout / 2, 10.0))
        except Exception:
            pass  # best-effort

        # 2. Stop the event loop — triggers the finally block in _bg_entry.
        #    THIS MUST ALWAYS RUN, even if service.close() raised.
        with contextlib.suppress(Exception):
            loop.call_soon_threadsafe(loop.stop)

        # 3. Wait for the background thread to exit.
        shutdown_timeout = max(timeout - 10.0, 5.0) if timeout > 10 else timeout
        self._bg_thread.join(timeout=shutdown_timeout)

        # 4. Verify thread actually stopped.  Do NOT silently report success.
        if self._bg_thread.is_alive():
            # References and _closed stay as-is — no silent success.
            raise RuntimeError(
                "LightRAG background thread did not stop in time. Restart the Streamlit process."
            )

        # 5. Only mark closed after confirmed thread exit.
        self._loop = None
        self._svc = None
        self._query_lock = None
        self._closed = True

    def __repr__(self) -> str:
        initialized = self._svc is not None
        return (
            f"LightRAGRuntime(initialized={initialized}, "
            f"model={self._settings.llm_model}, "
            f"embedding={self._settings.embedding_model})"
        )

    # ------------------------------------------------------------------
    # Background thread — the ONLY place asyncio objects are created
    # ------------------------------------------------------------------

    def _bg_entry(self) -> None:
        """Entry point for the background thread.

        Single outer try/finally covering init + run_forever + cleanup.
        All asyncio objects are created HERE, inside this thread.
        """
        loop: asyncio.AbstractEventLoop | None = None
        try:
            # ---- CREATE EVENT LOOP ON THIS THREAD ----
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self._loop = loop

            # ---- INIT ----
            self._init_task = loop.create_task(self._do_init())
            loop.run_until_complete(self._init_task)

        except BaseException as exc:
            self._error_box.append(exc)

        # ---- RUN FOREVER (only if init succeeded) ----
        if not self._error_box:
            try:
                loop.run_forever()
            except BaseException:
                pass  # expected after loop.stop()
            finally:
                # Normal-close cleanup: cancel pending, shutdown asyncgens.
                try:
                    pending = asyncio.all_tasks(loop)
                    for task in pending:
                        task.cancel()
                    if pending:
                        loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                except Exception:
                    pass
                with contextlib.suppress(Exception):
                    loop.run_until_complete(loop.shutdown_asyncgens())
                with contextlib.suppress(Exception):
                    loop.run_until_complete(loop.shutdown_default_executor())

        # ---- FINAL CLEANUP (always runs, including after init failure) ----
        # Init failure path: cancel pending tasks and shutdown before closing.
        if self._error_box and loop is not None and not loop.is_closed():
            try:
                pending = asyncio.all_tasks(loop)
                for task in pending:
                    task.cancel()
                if pending:
                    loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            except Exception:
                pass
            with contextlib.suppress(Exception):
                loop.run_until_complete(loop.shutdown_asyncgens())
            with contextlib.suppress(Exception):
                loop.run_until_complete(loop.shutdown_default_executor())

        # Close the loop on this bg thread.  Main thread never calls loop.close().
        if loop is not None and not loop.is_closed():
            loop.close()

        # Clear the thread-local event loop reference.
        asyncio.set_event_loop(None)

        # Signal that the background thread has fully stopped.
        self._stopped.set()

        # NOW signal the main thread.  For the success path this is
        # redundant (we already set it before run_forever) but harmless.
        # For the failure path this is the ONLY signal — and it fires
        # AFTER cleanup is complete, eliminating the race condition.
        self._init_done.set()

    # ------------------------------------------------------------------
    # Coroutines — run exclusively on the background loop
    # ------------------------------------------------------------------

    async def _do_init(self) -> None:
        """Create LightRAGService and query lock — all on the bg loop."""
        svc = self._service_factory(self._settings)
        await svc.initialize()
        self._svc = svc
        self._query_lock = asyncio.Lock()
        # Signal success BEFORE entering run_forever.
        self._init_done.set()

    async def _query_async(
        self,
        question: str,
        *,
        mode: QueryMode,
        top_k: int,
        chunk_top_k: int,
    ) -> QueryResult:
        """Execute a query under the serialization lock."""
        async with self._query_lock:  # type: ignore[union-attr]
            if top_k == 12 and chunk_top_k == 20:
                return await self._svc.query(question, mode=mode)
            return await self._svc.query(
                question, mode=mode, top_k=top_k, chunk_top_k=chunk_top_k
            )

    async def _shutdown_service(self) -> None:
        """Close the LightRAG service gracefully."""
        if self._svc is not None:
            await self._svc.close()

    # ------------------------------------------------------------------
    # Error / timeout handling (main thread)
    # ------------------------------------------------------------------

    def _handle_init_timeout(self) -> None:
        """Best-effort cleanup when init takes too long.

        Cancel the init task on the bg loop, request loop stop, and
        join the bg thread.  If the thread won't stop, raise with a
        clear instruction to restart the process.
        """
        loop = self._loop
        if loop is not None and not loop.is_closed():
            init_task = self._init_task
            if init_task is not None:
                loop.call_soon_threadsafe(init_task.cancel)
            loop.call_soon_threadsafe(loop.stop)

        self._bg_thread.join(timeout=5.0)

        if self._bg_thread.is_alive():
            raise RuntimeError(
                f"LightRAG runtime initialization timed out after "
                f"{self._init_timeout:.0f}s. "
                "The background thread did not stop. "
                "Restart the Streamlit process."
            )
        raise RuntimeError(
            f"LightRAG runtime initialization timed out after {self._init_timeout:.0f}s"
        )

    def _propagate_init_error(self) -> None:
        """Re-raise the exception that occurred during background init.

        The background thread guarantees that self._init_done is set ONLY
        after cleanup (including loop.close()) is complete, so there is no
        race between receiving the error and accessing partially-cleaned-up
        state.
        """
        exc = self._error_box[0]

        # Join the background thread first — it may still be running cleanup.
        if self._bg_thread.is_alive():
            self._bg_thread.join(timeout=5.0)

        raise exc
