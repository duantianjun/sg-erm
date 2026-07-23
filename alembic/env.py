# -*- coding: utf-8 -*-
"""Alembic 异步迁移环境。

从 app.config 读取数据库 URL，导入所有 ORM 模型作为 target_metadata。
支持异步引擎（aiosqlite）。
"""
import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from app.config import settings
# 导入所有模型，确保它们在 Base.metadata 中注册
from app.database import Base
from app.models import *  # noqa: F401, F403

# Alembic 配置
config = context.config

# 从 app.settings 覆盖数据库 URL
config.set_main_option("sqlalchemy.url", settings.db_url)

# 日志配置
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# 目标 metadata（用于 autogenerate）
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """离线模式：生成 SQL 脚本而不连接数据库。"""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        # SQLite 特定：启用 WAL 模式外键支持
        render_as_batch=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    """执行迁移（在连接上下文中）。"""
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        # SQLite batch 模式：支持表结构修改（ALTER TABLE 限制）
        render_as_batch=True,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """异步模式：使用异步引擎执行迁移。"""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """在线模式：连接数据库执行迁移。"""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
