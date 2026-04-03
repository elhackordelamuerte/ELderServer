"""
Eldersafe API - Database Configuration
======================================
"""

import os
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.pool import NullPool

from models import Base

# Database configuration
DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "mysql+aiomysql://eldersafe:eldersafe@mysql:3306/eldersafe_db"
)

# Create async engine
engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    poolclass=NullPool,  # Avoid connection pool issues with aiomysql
    pool_pre_ping=True,  # Test connections before using
)

# Create async session factory
AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)

async def get_db() -> AsyncSession:
    """Dependency injection for database session."""
    async with AsyncSessionLocal() as session:
        yield session

async def init_db():
    """Initialize database tables on startup."""
    async with engine.begin() as conn:
        # Create all tables if they don't exist
        await conn.run_sync(Base.metadata.create_all)
