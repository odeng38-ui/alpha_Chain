import logging
from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import declarative_base, sessionmaker

from app.config import settings

logger = logging.getLogger(__name__)

# SQLite requires check_same_thread=False, PostgreSQL does not
connect_args = {"check_same_thread": False} if settings.DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(
    settings.DATABASE_URL,
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


@contextmanager
def postgres_advisory_lock(
    lock_key: int,
    *,
    bind: Engine = engine,
    allow_sqlite: bool = False,
) -> Iterator[bool]:
    """Hold a PostgreSQL session lock on a dedicated pooled connection."""
    dialect_name = bind.dialect.name
    if dialect_name == "sqlite" and allow_sqlite:
        yield True
        return
    if dialect_name != "postgresql":
        raise AdvisoryLockError(f"unsupported advisory lock backend: {dialect_name}")

    connection = None
    acquired = False
    body_failed = False
    try:
        connection = bind.connect()
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
