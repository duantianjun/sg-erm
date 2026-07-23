# -*- coding: utf-8 -*-
"""扩展目录 API。

提供扩展列表（分页/搜索/过滤）、扩展详情、批量删除扩展和构建。
"""
import logging
import os

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.response import error_response, success
from app.config import settings
from app.database import get_db
from app.models import AuditLog, Extension, ExtensionBuild, ExtensionVersion, Publisher, User
from app.services.auth_service import require_auth

logger = logging.getLogger(__name__)

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
    """扩展列表（分页），含缓存统计。"""
    logger.debug(
        f"[扩展API] 查询扩展列表 page={page} limit={limit} "
        f"keyword={keyword or 'all'} publisher={publisher or 'all'}"
    )
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
    cached_counts = {}
    size_sums = {}
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

        # 缓存构建数
        cached_result = await db.execute(
            select(ExtensionVersion.extension_id, func.count())
            .join(ExtensionBuild)
            .where(
                ExtensionVersion.extension_id.in_(ext_ids),
                ExtensionBuild.cached == True,  # noqa: E712
            )
            .group_by(ExtensionVersion.extension_id)
        )
        for eid, cnt in cached_result.all():
            cached_counts[eid] = cnt

        # 磁盘大小合计
        size_result = await db.execute(
            select(ExtensionVersion.extension_id, func.coalesce(func.sum(ExtensionBuild.package_size), 0))
            .join(ExtensionBuild)
            .where(
                ExtensionVersion.extension_id.in_(ext_ids),
                ExtensionBuild.cached == True,  # noqa: E712
            )
            .group_by(ExtensionVersion.extension_id)
        )
        for eid, total_size in size_result.all():
            size_sums[eid] = total_size

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
            "cached_build_count": cached_counts.get(ext.id, 0),
            "total_size": size_sums.get(ext.id, 0),
            "updated_at": ext.updated_at.isoformat() if ext.updated_at else None,
        })

    logger.info(f"[扩展API] 返回 {len(data)} 个扩展，总计 {total}")
    return success(data, total)


@router.get("/{name}")
async def get_extension(
    name: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_auth),
):
    """扩展详情（含版本和构建信息，构建含 build_id）。"""
    logger.debug(f"[扩展API] 查询扩展详情 name={name}")
    result = await db.execute(
        select(Extension)
        .options(selectinload(Extension.publisher))
        .where(Extension.name == name)
    )
    ext = result.scalar_one_or_none()

    if not ext:
        logger.warning(f"[扩展API] 扩展不存在 name={name}")
        return success([], 0, "扩展不存在")

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
                    "build_id": b.id,
                    "postgres_version": b.postgres_version,
                    "arch": b.arch,
                    "os": b.os,
                    "flavor": b.flavor,
                    "build": b.build,
                    "package_path": b.package_path,
                    "package_size": b.package_size,
                    "sha256": b.sha256,
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

    logger.info(f"[扩展API] 返回扩展详情 name={name} versions={len(version_data)}")
    return success(data, 1)


# ─── 批量删除 ────────────────────────────────────────

class BatchDeleteRequest(BaseModel):
    """批量删除扩展请求。"""
    ids: list[str]


@router.delete("/batch")
async def batch_delete_extensions(
    body: BatchDeleteRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_auth),
):
    """批量删除扩展（含磁盘文件 + 数据库级联删除）。"""
    logger.info(f"[扩展API] 批量删除扩展 ids={body.ids} user={current_user.username}")
    repo_dir = str(settings.repo_dir)
    deleted = 0
    failed = 0

    for ext_id in body.ids:
        try:
            # 查询关联的所有构建包路径
            result = await db.execute(
                select(ExtensionBuild.package_path)
                .join(ExtensionVersion, ExtensionBuild.version_id == ExtensionVersion.id)
                .where(ExtensionVersion.extension_id == ext_id)
            )
            paths = [row[0] for row in result.all()]

            # 删除磁盘文件
            for rel_path in paths:
                file_path = os.path.join(repo_dir, rel_path)
                if os.path.exists(file_path):
                    try:
                        os.remove(file_path)
                    except OSError as e:
                        logger.warning(f"删除文件失败 {rel_path}: {e}")

            # 删除数据库记录（级联删除 Version → Build）
            ext = await db.get(Extension, ext_id)
            if ext:
                await db.delete(ext)
                await db.commit()
                deleted += 1
            else:
                failed += 1
        except Exception as e:
            logger.warning(f"删除扩展失败 {ext_id}: {e}")
            failed += 1
            await db.rollback()

    # 审计日志
    audit = AuditLog(
        actor=current_user.username,
        action="extension_batch_delete",
        resource=f"deleted={deleted}, failed={failed}",
        result="success" if failed == 0 else "partial",
    )
    db.add(audit)
    await db.commit()

    logger.info(f"[扩展API] 批量删除完成: deleted={deleted}, failed={failed}")
    return success({"deleted": deleted, "failed": failed}, 1, f"已删除 {deleted} 个扩展")


class BatchDeleteBuildsRequest(BaseModel):
    """批量删除构建包请求。"""
    build_ids: list[str]


@router.delete("/{name}/builds/batch")
async def batch_delete_builds(
    name: str,
    body: BatchDeleteBuildsRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_auth),
):
    """批量删除指定扩展的构建包。"""
    logger.info(f"[扩展API] 批量删除构建 ext={name} build_ids={body.build_ids} user={current_user.username}")
    repo_dir = str(settings.repo_dir)
    deleted = 0
    failed = 0

    for build_id in body.build_ids:
        try:
            build = await db.get(ExtensionBuild, build_id)
            if not build:
                failed += 1
                continue

            # 删除磁盘文件
            file_path = os.path.join(repo_dir, build.package_path)
            if os.path.exists(file_path):
                try:
                    os.remove(file_path)
                except OSError as e:
                    logger.warning(f"删除文件失败 {build.package_path}: {e}")

            # 删除数据库记录
            await db.delete(build)
            await db.commit()
            deleted += 1
        except Exception as e:
            logger.warning(f"删除构建失败 {build_id}: {e}")
            failed += 1
            await db.rollback()

    # 审计日志
    audit = AuditLog(
        actor=current_user.username,
        action="extension_build_batch_delete",
        resource=f"ext={name}, deleted={deleted}, failed={failed}",
        result="success" if failed == 0 else "partial",
    )
    db.add(audit)
    await db.commit()

    logger.info(f"[扩展API] 批量删除构建完成: ext={name} deleted={deleted}, failed={failed}")
    return success({"deleted": deleted, "failed": failed}, 1, f"已删除 {deleted} 个构建包")
