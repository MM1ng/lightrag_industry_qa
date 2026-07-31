"""Persistent task executor: polls DB for pending tasks and dispatches them.

Single-process, async, with KB-level mutual exclusion.
Runs inside the FastAPI lifespan.  Shuts down gracefully on SIGTERM/SIGINT.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import async_sessionmaker

from industrial_rag.config import Settings
from industrial_rag.db.models import TaskStatus
from industrial_rag.repositories.document_repository import DocumentRepository
from industrial_rag.repositories.knowledge_base_repository import (
    KnowledgeBaseRepository,
)
from industrial_rag.repositories.task_repository import TaskRepository
from industrial_rag.services.task_context import TaskExecutionContext
from industrial_rag.services.task_handlers import get_builtin_registry

logger = logging.getLogger(__name__)


class LifecycleTaskExecutor:
    """Polls for pending tasks and executes them one-at-a-time per KB.

    Not distributed — single-process only.  Uses asyncio.Lock per KB
    to serialise destructive operations.
    """

    def __init__(
        self,
        session_factory: async_sessionmaker,
        *,
        settings: Settings | None = None,
        runtime_manager: Any = None,
        poll_interval: float = 1.0,
        max_concurrency: int = 2,
        stale_running_seconds: float = 600.0,
        shutdown_timeout: float = 30.0,
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings
        self._runtime_manager = runtime_manager
        self._poll_interval = poll_interval
        self._max_concurrency = max_concurrency
        self._stale_running_seconds = stale_running_seconds
        self._shutdown_timeout = shutdown_timeout

        self._kb_locks: dict[str, asyncio.Lock] = {}
        self._active_tasks: set[str] = set()
        self._running = False
        self._poll_task: asyncio.Task[None] | None = None
        self._semaphore = asyncio.Semaphore(max_concurrency)

    # ------------------------------------------------------------------
    # Lifespan hooks
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Begin polling.  Called during FastAPI startup."""
        self._running = True
        await self._recover_stale_tasks()
        self._poll_task = asyncio.create_task(self._poll_loop(), name="task-executor-poll")
        logger.info(
            "Task executor started (poll=%.1fs, max_concurrency=%d)",
            self._poll_interval,
            self._max_concurrency,
        )

    async def stop(self) -> None:
        """Gracefully stop the executor.  Called during FastAPI shutdown."""
        self._running = False
        if self._poll_task is not None:
            self._poll_task.cancel()
            try:
                await asyncio.wait_for(self._poll_task, timeout=2.0)
            except (TimeoutError, asyncio.CancelledError):
                pass

        # Wait for running tasks
        timeout = self._shutdown_timeout
        deadline = asyncio.get_event_loop().time() + timeout
        while self._active_tasks and asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(0.5)

        if self._active_tasks:
            logger.warning(
                "Shutdown: %d tasks still running after %.0fs, abandoning",
                len(self._active_tasks),
                timeout,
            )
        logger.info("Task executor stopped")

    # ------------------------------------------------------------------
    # Poll loop
    # ------------------------------------------------------------------

    async def _poll_loop(self) -> None:
        while self._running:
            try:
                await self._poll_once()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Task executor poll iteration failed")
            await asyncio.sleep(self._poll_interval)

    async def _poll_once(self) -> None:
        async with self._session_factory() as session:
            task_repo = TaskRepository(session)

            # 1. Claim a pending task
            pending = await task_repo.find_pending(limit=1)
            for task in pending:
                claimed = await task_repo.mark_running(task.id)
                if claimed is None:
                    continue  # race — another poller claimed it
                # Submit for execution
                asyncio.create_task(self._execute_task(task.id))

    # ------------------------------------------------------------------
    # Task execution
    # ------------------------------------------------------------------

    async def _execute_task(self, task_id: str) -> None:
        self._active_tasks.add(task_id)
        try:
            async with self._session_factory() as session:
                task_repo = TaskRepository(session)
                kb_repo = KnowledgeBaseRepository(session)
                doc_repo = DocumentRepository(session)

                task = await task_repo.get(task_id)
                if task is None:
                    return

                # Acquire KB lock
                kb_id = task.knowledge_base_id
                lock = self._acquire_kb_lock(kb_id)
                async with lock:
                    await self._run_handler(task, kb_repo, doc_repo, task_repo)
        except Exception:
            logger.exception("Task %s execution failed", task_id)
        finally:
            self._active_tasks.discard(task_id)

    async def _run_handler(
        self,
        task: Any,
        kb_repo: KnowledgeBaseRepository,
        doc_repo: DocumentRepository,
        task_repo: TaskRepository,
    ) -> None:
        registry = get_builtin_registry()
        handler = registry.get(task.task_type)
        ctx = TaskExecutionContext(
            task=task,
            kb_repo=kb_repo,
            doc_repo=doc_repo,
            task_repo=task_repo,
            runtime_manager=self._runtime_manager,
            settings=self._settings,
        )

        if handler is None:
            await task_repo.mark_failed(
                task.id,
                error_code="no_handler",
                error_message=f"No handler registered for task type {task.task_type.value}",
            )
            return

        try:
            result = await handler(ctx)
            if result.success:
                await task_repo.mark_succeeded(task.id, result=result.result)
            else:
                if task.attempt < task.max_attempts:
                    await task_repo.mark_retrying(task.id)
                else:
                    await task_repo.mark_failed(
                        task.id,
                        error_code=result.error_code,
                        error_message=result.error_message,
                    )
        except Exception:
            if task.attempt < task.max_attempts:
                await task_repo.mark_retrying(task.id)
            else:
                await task_repo.mark_failed(
                    task.id,
                    error_code="handler_exception",
                    error_message="Handler raised an unhandled exception",
                )

    # ------------------------------------------------------------------
    # KB locks
    # ------------------------------------------------------------------

    def _acquire_kb_lock(self, kb_id: str) -> asyncio.Lock:
        if kb_id not in self._kb_locks:
            self._kb_locks[kb_id] = asyncio.Lock()
        return self._kb_locks[kb_id]

    # ------------------------------------------------------------------
    # Recovery
    # ------------------------------------------------------------------

    async def _recover_stale_tasks(self) -> None:
        """On startup, retry any running task whose updated_at is too old."""
        async with self._session_factory() as session:
            task_repo = TaskRepository(session)
            # Find running tasks
            from sqlalchemy import select

            from industrial_rag.db.models import LifecycleTask

            stmt = select(LifecycleTask).where(
                LifecycleTask.status == TaskStatus.running
            ).limit(10)
            result = await session.execute(stmt)
            stale_tasks = result.scalars().all()

            cutoff = datetime.now(tz=UTC).timestamp() - self._stale_running_seconds
            recovered = 0
            for t in stale_tasks:
                updated_ts = t.updated_at.timestamp() if t.updated_at else 0
                if updated_ts < cutoff:
                    await task_repo.mark_retrying(t.id)
                    recovered += 1

            if recovered:
                logger.info("Recovered %d stale running tasks", recovered)
