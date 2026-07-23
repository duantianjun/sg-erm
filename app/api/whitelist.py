# -*- coding: utf-8 -*-
"""全局白名单 API。

提供全局白名单的增删改查。
全局白名单作为基线，所有同步策略默认包含。
"""
import logging

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.response import success, error_response
from app.database import get_db
from app.models import GlobalWhitelist, User
from app.services.auth_service import require_admin

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/whitelist",
    tags=["whitelist"],
    dependencies=[Depends(require_admin)],
)


class WhitelistCreate(BaseModel):
    """创建白名单条目。"""
    extension_name: str
    postgres_versions: list | None = None  # 如 [">=16.0"]
    arch: list | None = None  # 如 ["x86_64", "aarch64"]


@router.get("")
async def list_whitelist(
    keyword: str = Query("", description="搜索关键词"),
    db: AsyncSession = Depends(get_db),
):
    """白名单列表。"""
    logger.info(f"[白名单API] 查询白名单 keyword={keyword or 'all'}")
    query = select(GlobalWhitelist).order_by(GlobalWhitelist.extension_name)

    if keyword:
        query = query.where(GlobalWhitelist.extension_name.contains(keyword))

    result = await db.execute(query)
    items = result.scalars().all()

    data = [
        {
            "id": w.id,
            "extension_name": w.extension_name,
            "postgres_versions": w.postgres_versions or [],
            "arch": w.arch or [],
            "created_at": w.created_at.isoformat() if w.created_at else None,
        }
        for w in items
    ]

    logger.info(f"[白名单API] 返回 {len(data)} 个白名单条目")
    return success(data, len(data))


@router.post("")
async def add_whitelist(
    body: WhitelistCreate,
    db: AsyncSession = Depends(get_db),
):
    """添加白名单条目。"""
    logger.info(
        f"[白名单API] 添加白名单 ext={body.extension_name} "
        f"pg_versions={body.postgres_versions} arch={body.arch}"
    )
    result = await db.execute(
        select(GlobalWhitelist).where(
            GlobalWhitelist.extension_name == body.extension_name
        )
    )
    existing = result.scalar_one_or_none()
    if existing:
        logger.warning(f"[白名单API] 添加失败：已存在 ext={body.extension_name}")
        return error_response(f"白名单中已存在 {body.extension_name}")

    entry = GlobalWhitelist(
        extension_name=body.extension_name,
        postgres_versions=body.postgres_versions,
        arch=body.arch,
    )
    db.add(entry)
    await db.commit()
    await db.refresh(entry)

    logger.info(f"[白名单API] 白名单添加成功 id={entry.id} ext={entry.extension_name}")
    return success({
        "id": entry.id,
        "extension_name": entry.extension_name,
    }, 1, "添加成功")


@router.delete("/{entry_id}")
async def delete_whitelist(
    entry_id: str,
    db: AsyncSession = Depends(get_db),
):
    """删除白名单条目。"""
    logger.info(f"[白名单API] 删除白名单 entry_id={entry_id}")
    entry = await db.get(GlobalWhitelist, entry_id)
    if not entry:
        logger.warning(f"[白名单API] 删除失败：白名单条目不存在 entry_id={entry_id}")
        return error_response("白名单条目不存在", status_code=404)

    ext_name = entry.extension_name
    await db.delete(entry)
    await db.commit()

    logger.info(f"[白名单API] 白名单删除成功 entry_id={entry_id} ext={ext_name}")
    return success({"id": entry_id}, 1, "删除成功")
