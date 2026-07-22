"""扩展目录 API。

提供扩展列表（分页/搜索/过滤）和扩展详情。
"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.response import success, success_response
from app.database import get_db
from app.models import Extension, ExtensionBuild, ExtensionVersion, Publisher, User
from app.services.auth_service import require_auth

router = APIRouter(prefix="/api/v1/extensions", tags=["extensions"])


@router.get("")
async def list_extensions(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    keyword: str = Query("", description="搜索关键词"),
    publisher: str = Query("", description="发布者过滤"),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_auth),
):
    """扩展列表（分页）。"""
    # 构建查询（预加载 publisher）
    query = select(Extension).options(selectinload(Extension.publisher)).order_by(Extension.name)

    # 关键词搜索
    if keyword:
        query = query.where(
            Extension.name.contains(keyword)
            | Extension.description.contains(keyword)
        )

    # 发布者过滤
    if publisher:
        query = query.join(Publisher).where(Publisher.name == publisher)

    # 总数
    count_query = select(func.count()).select_from(query.subquery())
    total = await db.scalar(count_query)
    total = total or 0

    # 分页
    query = query.offset((page - 1) * limit).limit(limit)
    result = await db.execute(query)
    extensions = result.scalars().all()

    # 一次性获取所有扩展的版本数和构建数
    ext_ids = [ext.id for ext in extensions]
    ver_counts = {}
    build_counts = {}
    if ext_ids:
        ver_result = await db.execute(
            select(ExtensionVersion.extension_id, func.count())
            .where(ExtensionVersion.extension_id.in_(ext_ids))
            .group_by(ExtensionVersion.extension_id)
        )
        for eid, cnt in ver_result.all():
            ver_counts[eid] = cnt
        build_result = await db.execute(
            select(ExtensionVersion.extension_id, func.count())
            .join(ExtensionBuild)
            .where(ExtensionVersion.extension_id.in_(ext_ids))
            .group_by(ExtensionVersion.extension_id)
        )
        for eid, cnt in build_result.all():
            build_counts[eid] = cnt

    data = []
    for ext in extensions:
        data.append({
            "id": ext.id,
            "name": ext.name,
            "description": ext.description or "",
            "publisher": ext.publisher.name if ext.publisher else "",
            "license": ext.license or "",
            "is_custom": ext.is_custom,
            "version_count": ver_counts.get(ext.id, 0),
            "build_count": build_counts.get(ext.id, 0),
            "updated_at": ext.updated_at.isoformat() if ext.updated_at else None,
        })

    return success(data, total)


@router.get("/{name}")
async def get_extension(
    name: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_auth),
):
    """扩展详情（含版本和构建信息）。"""
    result = await db.execute(
        select(Extension)
        .options(selectinload(Extension.publisher))
        .where(Extension.name == name)
    )
    ext = result.scalar_one_or_none()

    if not ext:
        return success_response([], 0, "扩展不存在")

    # 获取版本列表
    ver_result = await db.execute(
        select(ExtensionVersion)
        .where(ExtensionVersion.extension_id == ext.id)
        .order_by(ExtensionVersion.version)
    )
    versions = ver_result.scalars().all()

    version_data = []
    for ver in versions:
        # 获取该版本的构建列表
        build_result = await db.execute(
            select(ExtensionBuild)
            .where(ExtensionBuild.version_id == ver.id)
            .order_by(ExtensionBuild.postgres_version)
        )
        builds = build_result.scalars().all()

        version_data.append({
            "version": ver.version,
            "channel": ver.channel,
            "builds": [
                {
                    "postgres_version": b.postgres_version,
                    "arch": b.arch,
                    "os": b.os,
                    "flavor": b.flavor,
                    "build": b.build,
                    "package_path": b.package_path,
                    "package_size": b.package_size,
                    "cached": b.cached,
                    "verified": b.verified,
                }
                for b in builds
            ],
        })

    data = {
        "id": ext.id,
        "name": ext.name,
        "description": ext.description or "",
        "abstract": ext.abstract or "",
        "publisher": ext.publisher.name if ext.publisher else "",
        "license": ext.license or "",
        "url": ext.url or "",
        "source_url": ext.source_url or "",
        "tags": ext.tags or [],
        "channels": ext.channels or {},
        "is_custom": ext.is_custom,
        "versions": version_data,
        "created_at": ext.created_at.isoformat() if ext.created_at else None,
        "updated_at": ext.updated_at.isoformat() if ext.updated_at else None,
    }

    return success(data, 1)
