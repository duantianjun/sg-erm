# -*- coding: utf-8 -*-
"""集成测试 fixture：内存 SQLite + httpx ASGITransport。

关键点：
1. db_engine/db_session 用 sqlite:///:memory: + StaticPool，每测试一个全新库
2. client fixture 除覆盖 get_db 外，还 patch app.database.async_session_maker
   与 async_session_factory，使审计中间件（直接 from app.database import
   async_session_maker）的 DB 写入也落到测试库
3. 用 httpx.ASGITransport 而非 TestClient，不触发 lifespan
   （绕开 init_db / start_scheduler / start_health_checker / _init_default_admin）
"""
import app.database as db_module
from app.database import Base, get_db
from app.models import *  # noqa: F401,F403  确保所有模型注册到 Base.metadata
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import httpx
import pytest_asyncio


@pytest_asyncio.fixture
async def db_engine():
    """函数级内存 SQLite 引擎，建表后 yield，测完 drop+dispose。"""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        future=True,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(db_engine):
    """单测试用 AsyncSession，与 client 共享同一内存库。"""
    factory = async_sessionmaker(
        db_engine, class_=AsyncSession, expire_on_commit=False
    )
    async with factory() as session:
        yield session


@pytest_asyncio.fixture
async def client(db_engine, db_session):
    """API 测试客户端。

    - 覆盖 get_db → 路由用 db_session
    - patch app.database.async_session_factory / async_session_maker → 审计中间件
      写日志用同一内存库（中间件不走依赖注入，直接拿模块级 session_maker）
    """
    test_factory = async_sessionmaker(
        db_engine, class_=AsyncSession, expire_on_commit=False
    )
    original_factory = db_module.async_session_factory
    original_maker = db_module.async_session_maker
    db_module.async_session_factory = test_factory
    db_module.async_session_maker = test_factory

    from app.main import app

    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test"
    ) as c:
        yield c

    app.dependency_overrides.clear()
    db_module.async_session_factory = original_factory
    db_module.async_session_maker = original_maker
