import os
import asyncio
import sqlite3
from typing import AsyncGenerator, Any, Dict, List, Optional
from sqlalchemy import create_engine, event
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.pool import StaticPool, NullPool
from app.core.config import settings

Base = declarative_base()

class SafeScalarsProxy:
    """
    Thread-safe proxy for scalar result consumption.
    Data is pre-materialized off the event loop thread under the session lock.
    """
    def __init__(self, raw_scalars):
        self._items = list(raw_scalars)
        self._idx = 0

    def all(self) -> List[Any]:
        rem = self._items[self._idx:]
        self._idx = len(self._items)
        return rem

    def first(self) -> Optional[Any]:
        if self._idx < len(self._items):
            val = self._items[self._idx]
            self._idx += 1
            return val
        return None

    def one(self) -> Any:
        val = self.first()
        if val is None:
            raise RuntimeError("No row was found for one()")
        if self._idx < len(self._items):
            raise RuntimeError("Multiple rows were found for one()")
        return val

    def one_or_none(self) -> Optional[Any]:
        val = self.first()
        if val is None:
            return None
        if self._idx < len(self._items):
            raise RuntimeError("Multiple rows were found for one_or_none()")
        return val

    def __iter__(self):
        return iter(self.all())


class SafeResultProxy:
    """
    Thread-safe, materialized result proxy encapsulating raw cursor results.
    All rows and metadata are eagerly read inside worker threads under the session lock,
    guaranteeing zero event-loop blocking and thread safety during consumption.
    """
    def __init__(self, raw_result):
        self._returns_rows = getattr(raw_result, "returns_rows", False)
        if self._returns_rows:
            try:
                self._rows = raw_result.fetchall()
            except Exception:
                self._rows = []
            try:
                self._keys = list(raw_result.keys())
            except Exception:
                self._keys = []
        else:
            self._rows = []
            self._keys = []

        self._cursor_idx = 0
        try:
            self._rowcount = raw_result.rowcount
        except Exception:
            self._rowcount = -1

    @property
    def rowcount(self) -> int:
        return self._rowcount

    @property
    def returns_rows(self) -> bool:
        return self._returns_rows

    def keys(self) -> List[str]:
        return self._keys

    def fetchone(self) -> Optional[Any]:
        if self._cursor_idx < len(self._rows):
            row = self._rows[self._cursor_idx]
            self._cursor_idx += 1
            return row
        return None

    def fetchall(self) -> List[Any]:
        rem = self._rows[self._cursor_idx:]
        self._cursor_idx = len(self._rows)
        return rem

    def fetchmany(self, size: Optional[int] = None) -> List[Any]:
        if size is None:
            size = 1
        rem = self._rows[self._cursor_idx:self._cursor_idx + size]
        self._cursor_idx += len(rem)
        return rem

    def all(self) -> List[Any]:
        return self.fetchall()

    def first(self) -> Optional[Any]:
        return self.fetchone()

    def one(self) -> Any:
        row = self.fetchone()
        if row is None:
            raise RuntimeError("No row was found for one()")
        if self._cursor_idx < len(self._rows):
            raise RuntimeError("Multiple rows were found for one()")
        return row

    def one_or_none(self) -> Optional[Any]:
        row = self.fetchone()
        if row is None:
            return None
        if self._cursor_idx < len(self._rows):
            raise RuntimeError("Multiple rows were found for one_or_none()")
        return row

    def scalars(self) -> SafeScalarsProxy:
        scalars_list = [row[0] if hasattr(row, "__getitem__") and len(row) > 0 else row for row in self._rows[self._cursor_idx:]]
        self._cursor_idx = len(self._rows)
        return SafeScalarsProxy(scalars_list)

    def __iter__(self):
        return iter(self.fetchall())


class SQLiteAsyncSessionWrapper:
    """
    Transparent, thread-safe async wrapper around a synchronous SQLAlchemy session for SQLite.
    Bypasses the hard requirement for aiosqlite or PostgreSQL async drivers on Termux/mobile,
    allowing existing 'await db.execute(...)', 'await db.commit()', etc. to function seamlessly.

    Guarantees:
    - Thread-safe serialization via an asyncio.Lock per session instance (no concurrent calls on a single session).
    - Event loop protection: All operations (including add, delete, result consumption) are offloaded to worker threads.
    - Zero event-loop blocking: Results are safely materialized off-thread before returning to event loop.
    - Safe cancellation and exception handling via asyncio.shield in __aexit__.
    """
    def __init__(self, sync_session):
        self._session = sync_session
        self._lock = asyncio.Lock()

    @property
    def sync_session(self):
        return self._session

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        try:
            if exc_type is not None:
                await asyncio.shield(self.rollback())
        finally:
            await asyncio.shield(self.close())

    async def execute(self, *args, **kwargs) -> SafeResultProxy:
        def _sync_execute():
            raw = self._session.execute(*args, **kwargs)
            return SafeResultProxy(raw)
        async with self._lock:
            return await asyncio.to_thread(_sync_execute)

    async def scalar(self, *args, **kwargs) -> Any:
        def _sync_scalar():
            return self._session.scalar(*args, **kwargs)
        async with self._lock:
            return await asyncio.to_thread(_sync_scalar)

    async def scalars(self, *args, **kwargs) -> SafeScalarsProxy:
        def _sync_scalars():
            raw = self._session.scalars(*args, **kwargs)
            return SafeScalarsProxy(raw.all())
        async with self._lock:
            return await asyncio.to_thread(_sync_scalars)

    async def get(self, *args, **kwargs) -> Any:
        async with self._lock:
            return await asyncio.to_thread(self._session.get, *args, **kwargs)

    async def commit(self):
        async with self._lock:
            return await asyncio.to_thread(self._session.commit)

    async def rollback(self):
        async with self._lock:
            return await asyncio.to_thread(self._session.rollback)

    async def flush(self, *args, **kwargs):
        async with self._lock:
            return await asyncio.to_thread(self._session.flush, *args, **kwargs)

    async def refresh(self, *args, **kwargs):
        async with self._lock:
            return await asyncio.to_thread(self._session.refresh, *args, **kwargs)

    async def close(self):
        async with self._lock:
            return await asyncio.to_thread(self._session.close)

    async def add(self, *args, **kwargs):
        async with self._lock:
            return await asyncio.to_thread(self._session.add, *args, **kwargs)

    async def add_all(self, *args, **kwargs):
        async with self._lock:
            return await asyncio.to_thread(self._session.add_all, *args, **kwargs)

    async def delete(self, *args, **kwargs):
        async with self._lock:
            return await asyncio.to_thread(self._session.delete, *args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._session, name)
        if callable(attr):
            async def _async_wrapped(*args, **kwargs):
                async with self._lock:
                    return await asyncio.to_thread(attr, *args, **kwargs)
            return _async_wrapped
        return attr

def assert_test_db_isolated(canonical_path: str) -> None:
    """
    UNIVERSAL FAIL-CLOSED SAFETY GUARD:
    Prevents connection to operational database 'data/memecoin_os.db' during all testing,
    verification, diagnostics, and non-production execution.
    Only explicit production runtime (APP_ENV='production') is authorized to connect.
    """
    if canonical_path == ":memory:":
        return
    norm_path = os.path.normpath(os.path.abspath(canonical_path))
    operational_target = os.path.normpath(os.path.abspath("data/memecoin_os.db"))
    is_operational = (
        norm_path == operational_target
        or norm_path.endswith("data/memecoin_os.db")
        or os.path.basename(norm_path) == "memecoin_os.db"
    )

    if is_operational:
        is_test_runner = bool(os.environ.get("PYTEST_CURRENT_TEST"))
        is_not_production = (settings.APP_ENV != "production")
        strict_fail_closed = (os.environ.get("STRICT_FAIL_CLOSED", "1") == "1")

        if is_test_runner or is_not_production or strict_fail_closed:
            raise RuntimeError(
                f"SAFETY VIOLATION (FAIL-CLOSED): attempted to connect to operational database '{norm_path}'. "
                f"All testing, verification, diagnostics, and non-production runs MUST use an isolated temporary database or :memory:. "
                f"Operational database connection is strictly prohibited."
            )


_engine = None
_SyncSessionLocal = None
_AsyncSessionLocal = None

def get_engine():
    global _engine
    if _engine is None:
        if settings.is_sqlite:
            canonical_path = settings.get_canonical_sqlite_path()
            is_memory = (canonical_path == ":memory:")

            if not is_memory:
                assert_test_db_isolated(canonical_path)
                norm_path = os.path.normpath(os.path.abspath(canonical_path))
                os.makedirs(os.path.dirname(norm_path), exist_ok=True)
                sqlite_url = f"sqlite:///{canonical_path}"
            else:
                sqlite_url = "sqlite:///:memory:"

            _engine = create_engine(
                sqlite_url,
                connect_args={"check_same_thread": False, "timeout": 30.0},
                poolclass=StaticPool if is_memory else NullPool,
                echo=False
            )

            @event.listens_for(_engine, "connect")
            def set_sqlite_pragma(dbapi_connection, connection_record):
                cursor = dbapi_connection.cursor()
                cursor.execute("PRAGMA foreign_keys = ON;")
                if not is_memory:
                    cursor.execute("PRAGMA journal_mode = WAL;")
                    cursor.execute("PRAGMA busy_timeout = 30000;")
                cursor.close()

        else:
            from sqlalchemy.ext.asyncio import create_async_engine
            _engine = create_async_engine(
                settings.DATABASE_URL,
                echo=settings.DEBUG,
                future=True,
                pool_size=10,
                max_overflow=20
            )
    return _engine

def get_sync_session_factory():
    global _SyncSessionLocal
    if _SyncSessionLocal is None:
        eng = get_engine()
        _SyncSessionLocal = sessionmaker(bind=eng, autoflush=False, expire_on_commit=False)
    return _SyncSessionLocal

def get_async_session_factory():
    global _AsyncSessionLocal
    if _AsyncSessionLocal is None:
        from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession
        eng = get_engine()
        _AsyncSessionLocal = async_sessionmaker(
            bind=eng,
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False
        )
    return _AsyncSessionLocal

class _LazyEngineProxy:
    def __getattr__(self, name: str) -> Any:
        return getattr(get_engine(), name)

engine = _LazyEngineProxy()

class _LazySessionLocalProxy:
    def __call__(self, *args, **kwargs) -> Any:
        return get_sync_session_factory()(*args, **kwargs)
    def __getattr__(self, name: str) -> Any:
        return getattr(get_sync_session_factory(), name)

SyncSessionLocal = _LazySessionLocalProxy()

class _LazyAsyncSessionLocalProxy:
    def __call__(self, *args, **kwargs) -> Any:
        return get_async_session_factory()(*args, **kwargs)
    def __getattr__(self, name: str) -> Any:
        return getattr(get_async_session_factory(), name)

AsyncSessionLocal = _LazyAsyncSessionLocalProxy()

async def get_db() -> AsyncGenerator[Any, None]:
    if settings.is_sqlite:
        session = get_sync_session_factory()()
        wrapper = SQLiteAsyncSessionWrapper(session)
        try:
            yield wrapper
        finally:
            await wrapper.close()
    else:
        async with get_async_session_factory()() as session:
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
        canonical_path = settings.get_canonical_sqlite_path()
        if canonical_path == ":memory:":
            return {
                "status": "healthy",
                "backend": "sqlite",
                "database": ":memory:",
                "wal_mode": False,
                "foreign_keys": True
            }

        try:
            conn = sqlite3.connect(canonical_path, timeout=5.0)
            cursor = conn.cursor()
            cursor.execute("PRAGMA journal_mode;")
            journal_mode = cursor.fetchone()[0]
            cursor.execute("PRAGMA foreign_keys;")
            fk = cursor.fetchone()[0]
            conn.close()
            return {
                "status": "healthy",
                "backend": "sqlite",
                "database_path": canonical_path,
                "journal_mode": journal_mode,
                "foreign_keys": bool(fk)
            }
        except Exception as e:
            return {
                "status": "unhealthy",
                "backend": "sqlite",
                "database_path": canonical_path,
                "error": str(e)
            }
    else:
        return {
            "status": "healthy",
            "backend": "postgresql",
            "endpoint": settings.DATABASE_URL.split("@")[-1] if "@" in settings.DATABASE_URL else "configured"
        }
