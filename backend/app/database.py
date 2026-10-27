from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import get_settings

engine = create_async_engine(
    get_settings().database_url, pool_size=5, max_overflow=5,
    pool_pre_ping=True, pool_recycle=1800, pool_timeout=30,
)
SessionFactory = async_sessionmaker(engine, expire_on_commit=False)


async def get_session():
    async with SessionFactory() as session:
        yield session
