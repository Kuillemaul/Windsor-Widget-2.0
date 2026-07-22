from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict
from typing import Any

import pytest

from windsor_widget.config import RuntimeSettings, UnsafeConfigurationError
from windsor_widget.db.base import Base
from windsor_widget.db.bootstrap import (
    APPROVED_DEVELOPMENT_DATABASE,
    CREATE_DATABASE_SQL,
    ensure_development_database,
    verify_development_database,
)


def safe_settings() -> RuntimeSettings:
    return RuntimeSettings.from_mapping(
        {
            "environment": "development",
            "application_name": "Windsor Widget 2.0 DEV",
            "database": {
                "server": "localhost\\SQLEXPRESS",
                "database": APPROVED_DEVELOPMENT_DATABASE,
                "authentication": "windows",
                "driver": "ODBC Driver 18 for SQL Server",
                "encrypt": True,
                "trust_server_certificate": True,
            },
            "folders": {
                "root": "C:\\WindsorWidget2\\DEV",
                "watched": "C:\\WindsorWidget2\\DEV\\watched",
                "exports": "C:\\WindsorWidget2\\DEV\\exports",
                "archive": "C:\\WindsorWidget2\\DEV\\archive",
                "failed": "C:\\WindsorWidget2\\DEV\\failed",
            },
        }
    )


class FakeResult:
    def __init__(self, *, scalar: object = None, rows: Iterable[tuple[object, ...]] = ()):
        self.scalar = scalar
        self.rows = tuple(rows)

    def scalar_one_or_none(self) -> object:
        return self.scalar

    def scalar_one(self) -> object:
        if self.scalar is None:
            raise AssertionError("Expected a scalar result")
        return self.scalar

    def __iter__(self):
        return iter(self.rows)


class FakeConnection:
    def __init__(self, results: list[FakeResult]):
        self.results = results
        self.executed: list[tuple[str, object]] = []
        self.driver_sql: list[str] = []

    def __enter__(self) -> FakeConnection:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def execute(self, statement: object, parameters: object = None) -> FakeResult:
        self.executed.append((str(statement), parameters))
        return self.results.pop(0)

    def exec_driver_sql(self, statement: str) -> None:
        self.driver_sql.append(statement)


class FakeEngine:
    def __init__(self, connection: FakeConnection):
        self.connection = connection
        self.disposed = False

    def connect(self) -> FakeConnection:
        return self.connection

    def dispose(self) -> None:
        self.disposed = True


class FakeEngineFactory:
    def __init__(self, engine: FakeEngine):
        self.engine = engine
        self.calls: list[tuple[object, dict[str, Any]]] = []

    def __call__(self, url: object, **kwargs: Any) -> FakeEngine:
        self.calls.append((url, kwargs))
        return self.engine


def test_bootstrap_creates_only_the_fixed_approved_database() -> None:
    connection = FakeConnection([FakeResult(scalar=None)])
    engine = FakeEngine(connection)
    factory = FakeEngineFactory(engine)

    result = ensure_development_database(
        safe_settings(), engine_factory=factory  # type: ignore[arg-type]
    )

    assert result.created is True
    assert connection.driver_sql == [CREATE_DATABASE_SQL]
    assert factory.calls[0][0].database == "master"  # type: ignore[union-attr]
    assert factory.calls[0][1]["isolation_level"] == "AUTOCOMMIT"
    assert connection.executed[0][1] == {"database_name": APPROVED_DEVELOPMENT_DATABASE}
    assert engine.disposed is True


def test_bootstrap_does_not_recreate_an_existing_database() -> None:
    connection = FakeConnection([FakeResult(scalar=7)])
    engine = FakeEngine(connection)

    result = ensure_development_database(  # type: ignore[arg-type]
        safe_settings(), engine_factory=FakeEngineFactory(engine)
    )

    assert result.created is False
    assert connection.driver_sql == []


def test_bootstrap_rejects_every_other_database_name() -> None:
    mapping = asdict(safe_settings())
    mapping["database"]["database"] = "WindsorWidget"

    with pytest.raises(UnsafeConfigurationError):
        RuntimeSettings.from_mapping(mapping)


def test_verification_checks_name_revision_and_every_expected_table() -> None:
    tables = sorted(set(Base.metadata.tables) | {"alembic_version"})
    connection = FakeConnection(
        [
            FakeResult(scalar=APPROVED_DEVELOPMENT_DATABASE),
            FakeResult(scalar="0006_web_accounts"),
            FakeResult(rows=[(name,) for name in tables]),
        ]
    )
    engine = FakeEngine(connection)

    report = verify_development_database(  # type: ignore[arg-type]
        safe_settings(), engine_factory=FakeEngineFactory(engine)
    )

    assert report.database == APPROVED_DEVELOPMENT_DATABASE
    assert report.alembic_revision == "0006_web_accounts"
    assert set(report.tables) == set(tables)
    assert engine.disposed is True
