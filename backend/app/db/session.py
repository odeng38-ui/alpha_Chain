import logging
from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.pool import NullPool

from app.config import settings

logger = logging.getLogger(__name__)

# SQLite requires check_same_thread=False, PostgreSQL does not
database_url = settings.effective_database_url
connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}

engine = create_engine(
    database_url,
    connect_args=connect_args,
    pool_pre_ping=True
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


class AdvisoryLockError(RuntimeError):
    pass


_advisory_lock_engine: Engine | None = None


def create_advisory_lock_engine(database_direct_url: str) -> Engine:
    if not database_direct_url:
        raise AdvisoryLockError("DATABASE_DIRECT_URL is required for advisory locks")
    url = make_url(database_direct_url)
    host = (url.host or "").lower()
    if url.get_backend_name() != "postgresql" or "pooler" in host:
        raise AdvisoryLockError("DATABASE_DIRECT_URL must be a direct PostgreSQL URL")
    return create_engine(
        database_direct_url,
        pool_pre_ping=True,
        poolclass=NullPool,
    )


def get_advisory_lock_engine() -> Engine:
    global _advisory_lock_engine
    if _advisory_lock_engine is None:
        _advisory_lock_engine = create_advisory_lock_engine(
            settings.effective_database_direct_url
        )
    return _advisory_lock_engine


@contextmanager
def postgres_advisory_lock(
    lock_key: int,
    *,
    bind: Engine | None = None,
    allow_sqlite: bool = False,
) -> Iterator[bool]:
    """Hold a PostgreSQL session lock on one dedicated direct connection."""
    lock_engine = bind or get_advisory_lock_engine()
    dialect_name = lock_engine.dialect.name
    if dialect_name == "sqlite" and allow_sqlite:
        yield True
        return
    if dialect_name != "postgresql":
        raise AdvisoryLockError(f"unsupported advisory lock backend: {dialect_name}")

    connection = None
    acquired = False
    body_failed = False
    try:
        connection = lock_engine.connect()
        acquired = bool(connection.execute(
            text("SELECT pg_try_advisory_lock(:lock_key)"),
            {"lock_key": lock_key},
        ).scalar())
    except SQLAlchemyError as exc:
        if connection is not None:
            connection.close()
        raise AdvisoryLockError("failed to connect or acquire advisory lock") from exc

    try:
        yield acquired
    except BaseException:
        body_failed = True
        raise
    finally:
        release_error = None
        if acquired:
            try:
                released = connection.execute(
                    text("SELECT pg_advisory_unlock(:lock_key)"),
                    {"lock_key": lock_key},
                ).scalar()
                if not released:
                    raise AdvisoryLockError("advisory lock was not held during release")
            except (SQLAlchemyError, AdvisoryLockError) as exc:
                connection.invalidate()
                release_error = exc
        connection.close()
        if release_error is not None:
            if body_failed:
                logger.error("Failed to release advisory lock: %s", release_error)
            else:
                raise AdvisoryLockError("failed to release advisory lock") from release_error
