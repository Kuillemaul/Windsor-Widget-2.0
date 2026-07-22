"""Create and verify the isolated Windsor Widget v2 development database.

This module deliberately has no generic database-creation API.  Every operation is
hard-locked to the one database approved for development so a configuration mistake
cannot redirect the bootstrap towards Windsor Widget v1 or another SQL Server database.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from alembic import command
from alembic.config import Config
from sqlalchemy import URL, Engine, create_engine, text

from windsor_widget.config import DatabaseSettings, RuntimeSettings, UnsafeConfigurationError
from windsor_widget.db import models as _models  # noqa: F401
from windsor_widget.db.base import Base

APPROVED_DEVELOPMENT_DATABASE = "WindsorWidgetV2_DEV"
ALEMBIC_HEAD = "0006_web_accounts"
CREATE_DATABASE_SQL = "CREATE DATABASE [WindsorWidgetV2_DEV]"


class EngineFactory(Protocol):
    def __call__(self, url: URL, **kwargs: Any) -> Engine: ...


@dataclass(frozen=True, slots=True)
class DatabaseCreationResult:
    database: str
    created: bool

    @property
    def status(self) -> str:
        return "created" if self.created else "already existed"


@dataclass(frozen=True, slots=True)
class DatabaseVerification:
    database: str
    alembic_revision: str
    tables: tuple[str, ...]


def _assert_approved_database(database: str) -> None:
    if database != APPROVED_DEVELOPMENT_DATABASE:
        raise UnsafeConfigurationError(
            f"Database bootstrap is restricted to {APPROVED_DEVELOPMENT_DATABASE}."
        )


def _connection_url(settings: DatabaseSettings, database: str) -> URL:
    """Build a URL after the v2 target configuration has passed its safety checks."""

    settings.validate()
    _assert_approved_database(settings.database)

    query = {
        "driver": settings.driver,
        "Encrypt": "yes" if settings.encrypt else "no",
        "TrustServerCertificate": "yes" if settings.trust_server_certificate else "no",
    }
    username: str | None = None
    password: str | None = None
    if settings.authentication == "windows":
        query["Trusted_Connection"] = "yes"
    else:
        username = os.environ[settings.username_env]
        password = os.environ[settings.password_env]

    return URL.create(
        "mssql+pyodbc",
        username=username,
        password=password,
        host=settings.server,
        database=database,
        query=query,
    )


def ensure_development_database(
    settings: RuntimeSettings,
    *,
    engine_factory: EngineFactory = create_engine,
) -> DatabaseCreationResult:
    """Create the approved v2 database if absent; never modify an existing database."""

    settings.validate()
    _assert_approved_database(settings.database.database)
    master_url = _connection_url(settings.database, "master")
    engine = engine_factory(
        master_url,
        isolation_level="AUTOCOMMIT",
        pool_pre_ping=True,
        future=True,
    )
    try:
        with engine.connect() as connection:
            exists = connection.execute(
                text("SELECT database_id FROM sys.databases WHERE name = :database_name"),
                {"database_name": APPROVED_DEVELOPMENT_DATABASE},
            ).scalar_one_or_none()
            if exists is not None:
                return DatabaseCreationResult(APPROVED_DEVELOPMENT_DATABASE, created=False)

            # The identifier is an immutable module constant, never user-supplied text.
            connection.exec_driver_sql(CREATE_DATABASE_SQL)
            return DatabaseCreationResult(APPROVED_DEVELOPMENT_DATABASE, created=True)
    finally:
        engine.dispose()


@contextmanager
def _migration_config_path(config_path: Path) -> Iterator[None]:
    previous = os.environ.get("WINDSOR_WIDGET_V2_CONFIG")
    os.environ["WINDSOR_WIDGET_V2_CONFIG"] = str(config_path.resolve())
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("WINDSOR_WIDGET_V2_CONFIG", None)
        else:
            os.environ["WINDSOR_WIDGET_V2_CONFIG"] = previous


def upgrade_development_database(
    config_path: Path,
    *,
    alembic_config_path: Path = Path("alembic.ini"),
) -> None:
    """Apply every committed migration to the approved development database."""

    # Load and validate before Alembic is allowed to construct an engine.
    from windsor_widget.config import load_settings

    settings = load_settings(config_path)
    _assert_approved_database(settings.database.database)
    alembic_config = Config(str(alembic_config_path))
    with _migration_config_path(config_path):
        command.upgrade(alembic_config, "head")


def verify_development_database(
    settings: RuntimeSettings,
    *,
    engine_factory: EngineFactory = create_engine,
) -> DatabaseVerification:
    """Verify the connected name, Alembic revision and complete application table set."""

    settings.validate()
    _assert_approved_database(settings.database.database)
    engine = engine_factory(
        _connection_url(settings.database, APPROVED_DEVELOPMENT_DATABASE),
        pool_pre_ping=True,
        future=True,
    )
    try:
        with engine.connect() as connection:
            connected_database = connection.execute(text("SELECT DB_NAME()")).scalar_one()
            _assert_approved_database(str(connected_database))
            revision = connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one()
            table_names = tuple(
                sorted(
                    str(row[0])
                    for row in connection.execute(
                        text(
                            "SELECT name FROM sys.tables "
                            "WHERE is_ms_shipped = 0 ORDER BY name"
                        )
                    )
                )
            )
    finally:
        engine.dispose()

    expected = set(Base.metadata.tables) | {"alembic_version"}
    missing = sorted(expected.difference(table_names))
    if revision != ALEMBIC_HEAD:
        raise RuntimeError(
            f"Unexpected Alembic revision {revision!r}; expected {ALEMBIC_HEAD!r}."
        )
    if missing:
        raise RuntimeError(f"Development database is missing tables: {', '.join(missing)}")

    return DatabaseVerification(
        database=str(connected_database),
        alembic_revision=str(revision),
        tables=table_names,
    )
