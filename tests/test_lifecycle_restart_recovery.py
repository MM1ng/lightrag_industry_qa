from __future__ import annotations

import pytest
from industrial_rag.config import Settings
from industrial_rag.db.models import Base, KnowledgeBase, LifecycleTask, TaskStatus, TaskType
from industrial_rag.repositories.task_repository import TaskRepository
from industrial_rag.services.lifecycle_task_executor import LifecycleTaskExecutor
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


@pytest.mark.asyncio
async def test_executor_recovers_all_running_tasks_immediately_on_restart() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            kb = KnowledgeBase(
                id="a" * 32,
                name="restart",
                workspace_path="C:/tmp/nano/workspace",
                upload_path="C:/tmp/uploads",
                parsed_path="C:/tmp/parsed",
            )
            session.add(kb)
            await session.flush()
            for _ in range(11):
                session.add(
                    LifecycleTask(
                        knowledge_base_id=kb.id,
                        task_type=TaskType.rebuild,
                        status=TaskStatus.running,
                    )
                )
            await session.commit()

        executor = LifecycleTaskExecutor(factory, settings=Settings(api_key="test"), poll_interval=60)
        await executor._recover_stale_tasks()
        async with factory() as session:
            tasks = await TaskRepository(session).list_by_kb("a" * 32, limit=20)
            assert len(tasks) == 11
            assert {task.status for task in tasks} == {TaskStatus.retrying}
    finally:
        await engine.dispose()
