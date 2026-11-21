from redis.asyncio import Redis
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


def normalize_async_database_url(database_url: str) -> str:
    """Use asyncpg for PostgreSQL URLs consumed by SQLAlchemy async engines.

    Railway's generated DATABASE_URL may specify the psycopg dialect, while
    this service deliberately ships asyncpg. Normalizing at the application
    boundary keeps runtime startup and Alembic on the same async driver.
    """
    url = make_url(database_url)
    if url.get_backend_name() == "postgresql" and url.get_driver_name() != "asyncpg":
        url = url.set(drivername="postgresql+asyncpg")
        return url.render_as_string(hide_password=False)
    return database_url


def create_database_engine(database_url: str) -> AsyncEngine:
    return create_async_engine(
        normalize_async_database_url(database_url),
        pool_pre_ping=True,
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )


def create_redis_client(redis_url: str) -> Redis:
    return Redis.from_url(
        redis_url,
        encoding="utf-8",
        decode_responses=True,
        health_check_interval=30,
    )
