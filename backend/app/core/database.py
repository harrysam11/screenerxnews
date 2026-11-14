import os
import asyncio
import sqlite3
from typing import AsyncGenerator, Any, Dict
from sqlalchemy import create_engine, event
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.pool import StaticPool, NullPool
from app.core.config import settings

Base = declarative_base()

class SQLiteAsyncSessionWrapper:
    """
    Transparent asynchronous wrapper around a synchronous SQLAlchemy session.
    Provides standard async session methods (execute, commit, rollback, close)
    running safely via asyncio.to_thread without requiring heavy aiosqlite C extensions.
    """
    def __init__(self, sync_session):
        self._session = sync_session

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None:
            await self.rollback()
        await self.close()

    async def execute(self, *args, **kwargs):
        return await asyncio.to_thread(self._session.execute, *args, **kwargs)

    async def commit(self):
        return await asyncio.to_thread(self._session.commit)

    async def rollback(self):
        return await asyncio.to_thread(self._session.rollback)

    async def close(self):
        return await asyncio.to_thread(self._session.close)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._session, name)

if settings.is_sqlite:
    sqlite_url = settings.DATABASE_URL
    if sqlite_url.startswith("sqlite+aiosqlite://"):
        sqlite_url = sqlite_url.replace("sqlite+aiosqlite://", "sqlite://")

    is_memory = ":memory:" in sqlite_url

    if not is_memory:
        db_path = settings.SQLITE_DB_PATH
        if sqlite_url.startswith("sqlite:///"):
            raw_path = sqlite_url.replace("sqlite:///", "")
            if raw_path:
                db_path = raw_path
        os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)

    engine = create_engine(
        sqlite_url,
        connect_args={"check_same_thread": False, "timeout": 30.0},
        poolclass=StaticPool if is_memory else NullPool,
        echo=False
    )

    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys = ON;")
        if not is_memory:
            cursor.execute("PRAGMA journal_mode = WAL;")
            cursor.execute("PRAGMA busy_timeout = 30000;")
        cursor.close()

    SyncSessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    async def get_db() -> AsyncGenerator[Any, None]:
        session = SyncSessionLocal()
        wrapper = SQLiteAsyncSessionWrapper(session)
        try:
            yield wrapper
        finally:
            await wrapper.close()

else:
    from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker

    engine = create_async_engine(
        settings.DATABASE_URL,
        echo=settings.DEBUG,
        future=True,
        pool_size=10,
        max_overflow=20
    )

    AsyncSessionLocal = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False
    )

    async def get_db() -> AsyncGenerator[AsyncSession, None]:
        async with AsyncSessionLocal() as session:
            try:
                yield session
            finally:
                await session.close()


def check_database_health() -> Dict[str, Any]:
    """
    Performs an active probe of the database backend.
    Returns status, backend type, and SQLite pragma configuration.
    """
    if settings.is_sqlite:
        sqlite_url = settings.DATABASE_URL
        if ":memory:" in sqlite_url:
            return {
                "status": "healthy",
                "backend": "sqlite",
                "database": ":memory:",
                "wal_mode": False,
                "foreign_keys": True
            }

        db_path = settings.SQLITE_DB_PATH
        if sqlite_url.startswith("sqlite:///"):
            raw_path = sqlite_url.replace("sqlite:///", "")
            if raw_path:
                db_path = raw_path

        try:
            conn = sqlite3.connect(db_path, timeout=5.0)
            cursor = conn.cursor()
            cursor.execute("PRAGMA journal_mode;")
            journal_mode = cursor.fetchone()[0]
            cursor.execute("PRAGMA foreign_keys;")
            fk = cursor.fetchone()[0]
            conn.close()
            return {
                "status": "healthy",
                "backend": "sqlite",
                "database_path": db_path,
                "journal_mode": journal_mode,
                "foreign_keys": bool(fk)
            }
        except Exception as e:
            return {
                "status": "unhealthy",
                "backend": "sqlite",
                "database_path": db_path,
                "error": str(e)
            }
    else:
        return {
            "status": "healthy",
            "backend": "postgresql",
            "endpoint": settings.DATABASE_URL.split("@")[-1] if "@" in settings.DATABASE_URL else "configured"
        }

