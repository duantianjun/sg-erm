# -*- coding: utf-8 -*-
"""SQLAlchemy 2.0 异步数据库引擎。

特性：
- SQLite WAL 模式，支持并发读
- 异步引擎 + 异步会话工厂
- 基类 DeclarativeBase（所有 ORM 模型继承）

使用方式：
    from app.database import get_db
    # FastAPI 依赖注入
    @app.get("/items")
    async def list_items(db: AsyncSession = Depends(get_db)):
        ...
"""
from typing import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.config import settings


class Base(DeclarativeBase):
    """所有 ORM 模型的基类。"""
    pass


# 异步引擎
# check_same_thread=False: 允许 FastAPI 多线程使用
engine = create_async_engine(
    settings.db_url,
    echo=False,
    future=True,
)

# 异步会话工厂
# expire_on_commit=False: 避免提交后访问对象时触发额外的数据库查询
async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)

# 为外部模块（如中间件）导出的别名
async_session_maker = async_session_factory


async def init_db() -> None:
    """初始化数据库。

    - 创建数据目录与仓库目录
    - 启用 WAL 模式（支持并发读）
    - 启用外键约束
    - 优化并发写性能

    注意：生产环境表结构由 Alembic 迁移管理，这里不自动建表。
    """
    # 确保数据目录存在
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.repo_dir.mkdir(parents=True, exist_ok=True)

    # 启用 WAL 与外键，优化并发性能
    async with engine.begin() as conn:
        # WAL 模式：读写不互斥，适合单写多读场景
        await conn.exec_driver_sql("PRAGMA journal_mode=WAL")
        # 外键约束
        await conn.exec_driver_sql("PRAGMA foreign_keys=ON")
        # NORMAL 同步模式：WAL 下安全且更快
        await conn.exec_driver_sql("PRAGMA synchronous=NORMAL")
        # 写忙等待 5 秒，避免 SQLITE_BUSY
        await conn.exec_driver_sql("PRAGMA busy_timeout=5000")


async def close_db() -> None:
    """关闭数据库引擎，释放连接池。"""
    await engine.dispose()


async def get_db() -> AsyncIterator[AsyncSession]:
    """FastAPI 依赖：获取数据库会话。

    用法：
        @app.get("/items")
        async def list_items(db: AsyncSession = Depends(get_db)):
            result = await db.execute(select(Item))
            return result.scalars().all()
    """
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
