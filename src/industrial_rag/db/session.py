"""Database session management — creates engine and async session factory.

Defaults to SQLite (``DATA_DIR/db/industrial_rag.db``) with aiosqlite.
The same connection string syntax works for PostgreSQL when the time comes.
"""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from industrial_rag.db.models import Base

# Default database location relative to project root.
# Override via env DATABASE_URL (e.g. postgresql+asyncpg://...)
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB_DIR = PROJECT_ROOT / "data" / "db"
DEFAULT_DB_URL = f"sqlite+aiosqlite:///{DEFAULT_DB_DIR / 'industrial_rag.db'}"

_engine = None
_session_factory: async_sessionmaker[AsyncSession] | None = None
_trace_session_factory: async_sessionmaker[AsyncSession] | None = None


def _database_url() -> str:
    url = os.environ.get("DATABASE_URL", "").strip()
    if url:
        return url
    load_dotenv(PROJECT_ROOT / ".env", override=False)
    return os.environ.get("DATABASE_URL", DEFAULT_DB_URL)


def get_engine():
    global _engine
    if _engine is None:
        url = _database_url()
        # SQLite needs check_same_thread=False for async
        connect_args: dict = {}
        if url.startswith("sqlite"):
            connect_args["check_same_thread"] = False
            # Ensure the directory exists
            db_path = url.replace("sqlite+aiosqlite:///", "")
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        _engine = create_async_engine(
            url,
            echo=False,
            connect_args=connect_args,
        )
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            get_engine(),
            class_=AsyncSession,
            expire_on_commit=False,
        )
    return _session_factory


def get_trace_session_factory() -> async_sessionmaker[AsyncSession]:
    """Return a distinct factory so trace writes never reuse request sessions."""
    global _trace_session_factory
    if _trace_session_factory is None:
        _trace_session_factory = async_sessionmaker(
            get_engine(),
            class_=AsyncSession,
            expire_on_commit=False,
        )
    return _trace_session_factory


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency: yields an async DB session."""
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def init_db(*, drop_all: bool = False) -> None:
    """Create all tables (idempotent).  Use ``drop_all=True`` only in tests."""
    engine = get_engine()
    async with engine.begin() as conn:
        if drop_all:
            await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)


async def close_db() -> None:
    """Dispose the engine on shutdown."""
    global _engine, _session_factory, _trace_session_factory
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _session_factory = None
        _trace_session_factory = None


def reset_for_testing() -> None:
    """Reset globals so each test can reconfigure the engine."""
    global _engine, _session_factory, _trace_session_factory
    _engine = None
    _session_factory = None
    _trace_session_factory = None


# ---------------------------------------------------------------------------
# Import-time initialisation helpers (for alembic env.py and migrators)
# ---------------------------------------------------------------------------

def get_sync_url() -> str:
    """Return a synchronous URL for Alembic (swap aiosqlite → sqlite)."""
    url = _database_url()
    return url.replace("sqlite+aiosqlite:///", "sqlite:///")
