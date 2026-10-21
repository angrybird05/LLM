import os

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from coding_agent.config import settings


class Base(DeclarativeBase):
    pass


def _resolve_db_url() -> str:
    # If DATABASE_URL was explicitly set via env/config to something non-default,
    # respect it. Otherwise use the per-project path.
    explicit = os.environ.get("DATABASE_URL", "")
    if explicit and not explicit.startswith("sqlite"):
        return explicit          # PostgreSQL or other explicit override
    return settings.project_db_url   # per-project SQLite inside <workspace>/.agent/


engine = create_async_engine(
    _resolve_db_url(),
    echo=settings.APP_ENV == "development",
    pool_pre_ping=True,
)

async_session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_db() -> AsyncSession:
    async with async_session_maker() as session:
        yield session


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
