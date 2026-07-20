"""Runtime configuration with a hard Windsor Widget v2 development safety boundary."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path, PurePath, PurePosixPath, PureWindowsPath
from typing import Any, Literal

from sqlalchemy import URL


class UnsafeConfigurationError(ValueError):
    """Raised when configuration could cross the Windsor Widget v2 safety boundary."""


def _is_v2_path(value: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]", "", value.casefold())
    return "windsorwidget2" in normalized or "windsorwidgetv2" in normalized


def _portable_path(value: str) -> PurePath:
    """Interpret configured Windows paths correctly even when tests run elsewhere."""
    if "\\" in value or re.match(r"^[A-Za-z]:", value):
        return PureWindowsPath(value)
    return PurePosixPath(value)


@dataclass(frozen=True, slots=True)
class DatabaseSettings:
    server: str
    database: str
    driver: str = "ODBC Driver 18 for SQL Server"
    authentication: Literal["windows", "sql"] = "windows"
    username_env: str = "WINDSOR_WIDGET_V2_DB_USERNAME"
    password_env: str = "WINDSOR_WIDGET_V2_DB_PASSWORD"
    encrypt: bool = True
    trust_server_certificate: bool = False

    def validate(self) -> None:
        if not self.server.strip():
            raise UnsafeConfigurationError("A SQL Server name is required.")
        if self.database.casefold() != "windsorwidgetv2_dev".casefold():
            raise UnsafeConfigurationError(
                "Development is restricted to database WindsorWidgetV2_DEV."
            )
        if self.authentication not in {"windows", "sql"}:
            raise UnsafeConfigurationError("Database authentication must be windows or sql.")
        if self.authentication == "sql":
            if not os.getenv(self.username_env) or not os.getenv(self.password_env):
                raise UnsafeConfigurationError(
                    "SQL authentication credentials must be provided through the configured "
                    "environment variables."
                )

    def sqlalchemy_url(self) -> URL:
        self.validate()
        query = {
            "driver": self.driver,
            "Encrypt": "yes" if self.encrypt else "no",
            "TrustServerCertificate": "yes" if self.trust_server_certificate else "no",
        }
        username: str | None = None
        password: str | None = None
        if self.authentication == "windows":
            query["Trusted_Connection"] = "yes"
        else:
            username = os.environ[self.username_env]
            password = os.environ[self.password_env]

        return URL.create(
            "mssql+pyodbc",
            username=username,
            password=password,
            host=self.server,
            database=self.database,
            query=query,
        )


@dataclass(frozen=True, slots=True)
class FolderSettings:
    root: str
    watched: str
    archive: str
    failed: str
    exports: str

    def validate(self) -> None:
        values = {
            "root": self.root,
            "watched": self.watched,
            "archive": self.archive,
            "failed": self.failed,
            "exports": self.exports,
        }
        for label, value in values.items():
            if not value.strip():
                raise UnsafeConfigurationError(f"Folder {label} is required.")
            if not _is_v2_path(value):
                raise UnsafeConfigurationError(
                    f"Folder {label} must include an explicit WindsorWidget2 path marker."
                )

        root = _portable_path(self.root)
        for label in ("watched", "archive", "failed", "exports"):
            path = _portable_path(values[label])
            try:
                path.relative_to(root)
            except ValueError as exc:
                raise UnsafeConfigurationError(
                    f"Folder {label} must be located beneath the configured v2 root."
                ) from exc

        operational_paths = [
            _portable_path(values[name])
            for name in ("watched", "archive", "failed", "exports")
        ]
        if len({str(path).casefold() for path in operational_paths}) != len(operational_paths):
            raise UnsafeConfigurationError("Each operational folder must be separate.")


@dataclass(frozen=True, slots=True)
class RuntimeSettings:
    environment: str
    application_name: str
    database: DatabaseSettings
    folders: FolderSettings

    def validate(self) -> None:
        if self.environment.casefold() != "development":
            raise UnsafeConfigurationError(
                "This Stage 1 build is locked to the development environment."
            )
        if "2.0" not in self.application_name or "DEV" not in self.application_name.upper():
            raise UnsafeConfigurationError(
                "The application name must visibly identify Windsor Widget 2.0 DEV."
            )
        self.database.validate()
        self.folders.validate()

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> RuntimeSettings:
        settings = cls(
            environment=str(data["environment"]),
            application_name=str(data["application_name"]),
            database=DatabaseSettings(**data["database"]),
            folders=FolderSettings(**data["folders"]),
        )
        settings.validate()
        return settings


def load_settings(path: str | Path) -> RuntimeSettings:
    config_path = Path(path)
    with config_path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise UnsafeConfigurationError("The configuration root must be a JSON object.")
    return RuntimeSettings.from_mapping(data)
