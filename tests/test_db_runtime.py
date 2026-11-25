import pytest
from sqlalchemy.engine import make_url

from app.core.config import Settings
from app.db import runtime
from app.db.runtime import (
    DATABASE_SCHEMA,
    DATABASE_SEARCH_PATH,
    normalize_async_database_url,
)


@pytest.mark.parametrize(
    "database_url",
    [
        "postgresql://user:pass@db.example:5432/classmatework",
        "postgresql+psycopg://user:pass@db.example:5432/classmatework",
        "postgresql+psycopg2://user:pass@db.example:5432/classmatework",
    ],
)
def test_postgresql_urls_are_normalized_for_async_engine(database_url: str) -> None:
    normalized = make_url(normalize_async_database_url(database_url))

    assert normalized.drivername == "postgresql+asyncpg"
    assert normalized.username == "user"
    assert normalized.password == "pass"
    assert normalized.host == "db.example"
    assert normalized.port == 5432
    assert normalized.database == "classmatework"


def test_existing_asyncpg_url_is_preserved() -> None:
    database_url = (
        "postgresql+asyncpg://user:pass@db.example:5432/classmatework"
    )

    assert normalize_async_database_url(database_url) == database_url


def test_non_postgresql_url_is_preserved() -> None:
    database_url = "sqlite+aiosqlite:///:memory:"

    assert normalize_async_database_url(database_url) == database_url


def test_application_uses_a_dedicated_postgres_schema() -> None:
    assert DATABASE_SCHEMA == "classmatework"
    assert DATABASE_SEARCH_PATH == "classmatework,public"


def test_product_database_engine_does_not_inherit_work_search_path(monkeypatch) -> None:
    captured = {}

    def fake_create_async_engine(url, **kwargs):
        captured["url"] = url
        captured["kwargs"] = kwargs
        return object()

    monkeypatch.setattr(runtime, "create_async_engine", fake_create_async_engine)
    runtime.create_product_database_engine(
        "postgresql://product:secret@db.example:5432/classmate"
    )

    assert make_url(captured["url"]).drivername == "postgresql+asyncpg"
    assert captured["kwargs"]["pool_pre_ping"] is True
    assert "connect_args" not in captured["kwargs"]


def test_product_database_url_is_loaded_as_a_secret() -> None:
    settings = Settings(
        _env_file=None,
        c2_db_url="postgresql://product:secret@db.example:5432/classmate",
    )

    assert settings.c2_db_url is not None
    assert settings.c2_db_url.get_secret_value().endswith("/classmate")
    assert "secret" not in str(settings.c2_db_url)


def test_product_database_engine_is_not_mandatory_for_local_or_legacy_runtime() -> None:
    settings = Settings(_env_file=None)

    assert settings.c2_db_url is None
