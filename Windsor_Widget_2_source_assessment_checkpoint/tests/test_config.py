from __future__ import annotations

from copy import deepcopy

import pytest

from windsor_widget.config import RuntimeSettings, UnsafeConfigurationError


def safe_mapping() -> dict[str, object]:
    return {
        "environment": "development",
        "application_name": "Windsor Widget 2.0 DEV",
        "database": {
            "server": "localhost\\SQLEXPRESS",
            "database": "WindsorWidgetV2_DEV",
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


def test_safe_windows_configuration_is_accepted() -> None:
    settings = RuntimeSettings.from_mapping(safe_mapping())

    assert settings.environment == "development"
    assert settings.database.database == "WindsorWidgetV2_DEV"


def test_safe_posix_configuration_is_accepted() -> None:
    mapping = safe_mapping()
    mapping["folders"] = {
        "root": "/srv/windsor-widget-2/dev",
        "watched": "/srv/windsor-widget-2/dev/watched",
        "exports": "/srv/windsor-widget-2/dev/exports",
        "archive": "/srv/windsor-widget-2/dev/archive",
        "failed": "/srv/windsor-widget-2/dev/failed",
    }

    RuntimeSettings.from_mapping(mapping)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("environment",), "production"),
        (("application_name",), "Windsor Widget DEV"),
        (("database", "database"), "windsor_real"),
        (("folders", "watched"), "C:\\WindsorWidget1\\watched"),
        (("folders", "exports"), "C:\\other\\WindsorWidget2\\exports"),
    ],
)
def test_unsafe_targets_are_rejected(path: tuple[str, ...], value: object) -> None:
    mapping = deepcopy(safe_mapping())
    target = mapping
    for key in path[:-1]:
        child = target[key]
        assert isinstance(child, dict)
        target = child
    target[path[-1]] = value

    with pytest.raises(UnsafeConfigurationError):
        RuntimeSettings.from_mapping(mapping)


def test_sql_auth_requires_environment_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    mapping = safe_mapping()
    database = mapping["database"]
    assert isinstance(database, dict)
    database["authentication"] = "sql"
    database["username_env"] = "WW2_TEST_USER"
    database["password_env"] = "WW2_TEST_PASSWORD"
    monkeypatch.delenv("WW2_TEST_USER", raising=False)
    monkeypatch.delenv("WW2_TEST_PASSWORD", raising=False)

    with pytest.raises(UnsafeConfigurationError, match="SQL authentication credentials"):
        RuntimeSettings.from_mapping(mapping)


def test_connection_url_contains_only_v2_database(monkeypatch: pytest.MonkeyPatch) -> None:
    mapping = safe_mapping()
    database = mapping["database"]
    assert isinstance(database, dict)
    database["authentication"] = "sql"
    database["username_env"] = "WW2_TEST_USER"
    database["password_env"] = "WW2_TEST_PASSWORD"
    monkeypatch.setenv("WW2_TEST_USER", "widget_v2")
    monkeypatch.setenv("WW2_TEST_PASSWORD", "not-a-real-secret")

    settings = RuntimeSettings.from_mapping(mapping)
    url = settings.database.sqlalchemy_url()

    assert url.database == "WindsorWidgetV2_DEV"
    assert url.username == "widget_v2"
    assert url.password == "not-a-real-secret"
