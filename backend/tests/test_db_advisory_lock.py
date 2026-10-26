import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.exc import SQLAlchemyError

from app.db.session import AdvisoryLockError, postgres_advisory_lock


class FakeDialect:
    name = "postgresql"


class FakeResult:
    def __init__(self, value):
        self.value = value

    def scalar(self):
        return self.value


class FakeConnection:
    def __init__(self, engine):
        self.engine = engine
        self.invalidated = False
        self.closed = False

    def execute(self, statement, parameters):
        sql = str(statement)
        if "pg_try_advisory_lock" in sql:
            if self.engine.acquire_error:
                raise SQLAlchemyError("connection failed")
            if self.engine.locked:
                return FakeResult(False)
            self.engine.locked = True
            return FakeResult(True)
        if "pg_advisory_unlock" in sql:
            if self.engine.release_error:
                raise SQLAlchemyError("unlock failed")
            self.engine.locked = False
            return FakeResult(True)
        raise AssertionError(sql)

    def invalidate(self):
        self.invalidated = True
        self.engine.invalidations += 1

    def close(self):
        self.closed = True
        self.engine.closes += 1


class FakeBatchSession:
    def __init__(self):
        self.commit_count = 0

    def commit(self):
        self.commit_count += 1


class FakeEngine:
    dialect = FakeDialect()

    def __init__(self, *, connect_error=False, acquire_error=False, release_error=False):
        self.connect_error = connect_error
        self.acquire_error = acquire_error
        self.release_error = release_error
        self.locked = False
        self.connections = []
        self.closes = 0
        self.invalidations = 0

    def connect(self):
        if self.connect_error:
            raise SQLAlchemyError("connect failed")
        connection = FakeConnection(self)
        self.connections.append(connection)
        return connection


def test_first_lock_succeeds_and_concurrent_lock_is_rejected():
    engine = FakeEngine()

    with postgres_advisory_lock(17, bind=engine) as first:
        assert first is True
        with postgres_advisory_lock(17, bind=engine) as second:
            assert second is False

    assert engine.locked is False


def test_lock_can_be_acquired_after_first_request_finishes():
    engine = FakeEngine()

    with postgres_advisory_lock(17, bind=engine) as first:
        assert first is True
    with postgres_advisory_lock(17, bind=engine) as second:
        assert second is True


def test_lock_survives_batch_session_commit():
    engine = FakeEngine()
    batch_session = FakeBatchSession()

    with postgres_advisory_lock(17, bind=engine) as acquired:
        assert acquired is True
        batch_session.commit()
        assert batch_session.commit_count == 1
        assert engine.locked is True


def test_lock_is_released_when_batch_raises():
    engine = FakeEngine()

    with pytest.raises(RuntimeError, match="batch failed"):
        with postgres_advisory_lock(17, bind=engine) as acquired:
            assert acquired is True
            raise RuntimeError("batch failed")

    assert engine.locked is False
    assert engine.closes == 1


def test_database_connection_failure_is_not_reported_as_collision():
    engine = FakeEngine(connect_error=True)

    with pytest.raises(AdvisoryLockError, match="connect"):
        with postgres_advisory_lock(17, bind=engine):
            pass


def test_sqlite_compatibility_does_not_execute_postgresql_sql():
    engine = create_engine("sqlite:///:memory:")
    statements = []
    event.listen(
        engine,
        "before_cursor_execute",
        lambda conn, cursor, statement, parameters, context, executemany: statements.append(statement),
    )

    with postgres_advisory_lock(17, bind=engine, allow_sqlite=True) as acquired:
        assert acquired is True

    assert not any("advisory_lock" in statement for statement in statements)


def test_release_failure_invalidates_connection_before_pool_return():
    engine = FakeEngine(release_error=True)

    with pytest.raises(AdvisoryLockError, match="release"):
        with postgres_advisory_lock(17, bind=engine) as acquired:
            assert acquired is True

    assert engine.invalidations == 1
    assert engine.closes == 1


def test_sqlite_requires_explicit_test_opt_in():
    engine = create_engine("sqlite:///:memory:")

    with pytest.raises(AdvisoryLockError, match="unsupported"):
        with postgres_advisory_lock(17, bind=engine):
            pass
