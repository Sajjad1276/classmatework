import pytest
from sqlalchemy.engine import make_url

from app.db.runtime import normalize_async_database_url


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
