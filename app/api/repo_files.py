# -*- coding: utf-8 -*-
"""仓库文件浏览器 API。

提供本地仓库中缓存扩展包的浏览、删除、重新下载、SHA256 验证和一致性检查。
"""
import hashlib
import logging
import os

import aiohttp
from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.response import error_response, success, success_response
from app.config import settings
from app.database import get_db
from app.models import (
    AuditLog,
    Extension,
    ExtensionBuild,
    ExtensionVersion,
    Publisher,
    RepositorySource,
    User,
)
from app.services.auth_service import require_auth
from app.services.naming import get_package_url, parse_package_name

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/repo-files",
    tags=["repo-files"],
    dependencies=[Depends(require_auth)],
)


def _validate_path(relative_path: str, base_dir: str) -> str:
    """验证相对路径是否在 base_dir 内，防止路径遍历攻击。

    Args:
        relative_path: 相对路径
        base_dir: 基础目录

    Returns:
        规范化后的绝对路径

    Raises:
        ValueError: 如果路径超出 base_dir
    """
    full_path = os.path.normpath(os.path.join(base_dir, relative_path))
    base_path = os.path.normpath(base_dir)
    if not full_path.startswith(base_path + os.sep) and full_path != base_path:
        raise ValueError(f"非法路径: {relative_path}")
    return full_path


# ─── 包列表 ────────────────────────────────────────────────────

@router.get("/packages")
async def list_packages(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    publisher: str = Query("", description="发布者名称过滤"),
    arch: str = Query("", description="架构过滤"),
    os_name: str = Query("", alias="os", description="操作系统过滤"),
    keyword: str = Query("", description="关键词搜索"),
    db: AsyncSession = Depends(get_db),
):
    """包列表（分页，仅返回 cached=True 的记录）。

    JOIN ExtensionVersion → Extension → Publisher 获取完整元数据，
    对每条记录检查磁盘文件是否存在。
    """
    logger.debug(
        f"[仓库文件API] 查询包列表 page={page} limit={limit} "
        f"publisher={publisher or 'all'} arch={arch or 'all'} "
        f"os={os_name or 'all'} keyword={keyword or 'none'}"
    )
    query = (
        select(
            ExtensionBuild,
            ExtensionVersion.version,
            Extension.name,
            Publisher.name,
            Extension.description,
            Extension.license,
        )
        .join(ExtensionVersion, ExtensionBuild.version_id == ExtensionVersion.id)
        .join(Extension, ExtensionVersion.extension_id == Extension.id)
        .join(Publisher, Extension.publisher_id == Publisher.id)
        .where(ExtensionBuild.cached == True)  # noqa: E712
        .order_by(ExtensionBuild.package_path)
    )

    # 过滤条件
    if publisher:
        query = query.where(Publisher.name == publisher)
    if arch:
        query = query.where(ExtensionBuild.arch == arch)
    if os_name:
        query = query.where(ExtensionBuild.os == os_name)
    if keyword:
        query = query.where(
            ExtensionBuild.package_path.contains(keyword)
            | Extension.name.contains(keyword)
        )

    # 总数
    count_query = select(func.count()).select_from(query.subquery())
    total = await db.scalar(count_query)
    total = total or 0

    # 分页
    query = query.offset((page - 1) * limit).limit(limit)
    result = await db.execute(query)
    rows = result.all()

    repo_dir = str(settings.repo_dir)
    data = []
    for build, version, ext_name, pub_name, ext_desc, ext_license in rows:
        file_path = os.path.join(repo_dir, build.package_path)
        data.append({
            "build_id": build.id,
            "publisher": pub_name,
            "arch": build.arch,
            "os": build.os,
            "package_name": os.path.basename(build.package_path),
            "extension_name": ext_name,
            "description": ext_desc or "",
            "license": ext_license or "",
            "version": version,
            "postgres_version": build.postgres_version,
            "flavor": build.flavor,
            "build": build.build,
            "package_path": build.package_path,
            "package_size": build.package_size,
            "sha256": build.sha256,
            "cached": build.cached,
            "file_exists": os.path.exists(file_path),
        })

    logger.info(f"[仓库文件API] 返回 {len(data)} 个包，总计 {total}")
    return success(data, total)


# ─── 目录树 ────────────────────────────────────────────────────

@router.get("/tree")
async def get_tree(
    db: AsyncSession = Depends(get_db),
):
    """目录树（publisher → arch → os 三级聚合，计算每层 count）。"""
    logger.info("[仓库文件API] 查询目录树")
    query = (
        select(Publisher.name, ExtensionBuild.arch, ExtensionBuild.os, func.count())
        .join(ExtensionVersion, ExtensionBuild.version_id == ExtensionVersion.id)
        .join(Extension, ExtensionVersion.extension_id == Extension.id)
        .join(Publisher, Extension.publisher_id == Publisher.id)
        .where(ExtensionBuild.cached == True)  # noqa: E712
        .group_by(Publisher.name, ExtensionBuild.arch, ExtensionBuild.os)
    )
    result = await db.execute(query)
    rows = result.all()

    # 构建三级嵌套树: publisher -> {arch -> {os -> count}}
    tree: dict = {}
    for pub_name, arch_val, os_val, cnt in rows:
        tree.setdefault(pub_name, {}).setdefault(arch_val, {})[os_val] = cnt

    data = []
    for pub_name, arches in tree.items():
        pub_total = 0
        arch_children = []
        for arch_val, oses in arches.items():
            arch_total = 0
            os_children = []
            for os_val, cnt in oses.items():
                os_children.append({"os": os_val, "count": cnt})
                arch_total += cnt
            arch_children.append({
                "arch": arch_val,
                "count": arch_total,
                "children": os_children,
            })
            pub_total += arch_total
        data.append({
            "publisher": pub_name,
            "count": pub_total,
            "children": arch_children,
        })

    return success(data)


# ─── 删除包 ────────────────────────────────────────────────────

@router.delete("/packages/{build_id}")
async def delete_package(
    build_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_auth),
):
    """删除磁盘文件并清除缓存标记。

    - 文件不存在时返回 404
    - 删除成功后更新 cached=False 并写 AuditLog
    """
    logger.info(f"[仓库文件API] 删除包 build_id={build_id} user={current_user.username}")
    build = await db.get(ExtensionBuild, build_id)
    if not build:
        logger.warning(f"[仓库文件API] 删除失败：构建记录不存在 build_id={build_id}")
        return error_response("构建记录不存在", status_code=404)

    try:
        file_path = _validate_path(build.package_path, str(settings.repo_dir))
    except ValueError as e:
        logger.warning(f"[仓库文件API] 删除失败：路径非法 build_id={build_id} path={build.package_path}")
        return error_response(str(e), status_code=400)

    if not os.path.exists(file_path):
        logger.warning(f"[仓库文件API] 删除失败：文件不存在 build_id={build_id} path={file_path}")
        return error_response("文件不存在", status_code=404)

    try:
        os.remove(file_path)
    except OSError as e:
        logger.warning("删除文件失败 %s: %s", file_path, e)
        return error_response(f"删除文件失败: {e}")

    build.cached = False
    audit = AuditLog(
        actor=current_user.username,
        action="repo_file_delete",
        resource=build.package_path,
        result="success",
    )
    db.add(audit)
    await db.commit()

    logger.info(f"[仓库文件API] 删除成功 build_id={build_id} path={build.package_path}")
    return success({"build_id": build_id}, 1, "已删除文件，缓存已清除")


# ─── 重新下载 ──────────────────────────────────────────────────

@router.post("/packages/{build_id}/redownload")
async def redownload_package(
    build_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_auth),
):
    """从上游重新下载包文件。

    - 从 ExtensionBuild 元数据重建上游 URL
    - 获取关联的 RepositorySource.url，拼接 /{publisher}/{arch}/{os}/{package_name}.tar
    - 用 aiohttp 下载到 repo_dir/package_path
    - 更新 cached=True, package_size, sha256
    - 写 AuditLog (action=repo_file_redownload)
    """
    logger.info(f"[仓库文件API] 重新下载包 build_id={build_id} user={current_user.username}")
    result = await db.execute(
        select(ExtensionBuild, Publisher.name, RepositorySource.url)
        .join(ExtensionVersion, ExtensionBuild.version_id == ExtensionVersion.id)
        .join(Extension, ExtensionVersion.extension_id == Extension.id)
        .join(Publisher, Extension.publisher_id == Publisher.id)
        .outerjoin(RepositorySource, Extension.source_id == RepositorySource.id)
        .where(ExtensionBuild.id == build_id)
    )
    row = result.first()
    if not row:
        logger.warning(f"[仓库文件API] 重新下载失败：构建记录不存在 build_id={build_id}")
        return error_response("构建记录不存在", status_code=404)

    build, pub_name, source_url = row
    source_url = source_url or settings.upstream_repo_url

    package_name = os.path.basename(build.package_path)
    base_name = package_name[:-4] if package_name.endswith(".tar") else package_name
    url = get_package_url(source_url, pub_name, build.arch, build.os, base_name)

    try:
        local_file = _validate_path(build.package_path, str(settings.repo_dir))
    except ValueError as e:
        logger.warning(f"[仓库文件API] 重新下载失败：路径非法 build_id={build_id} path={build.package_path}")
        return error_response(str(e), status_code=400)

    logger.debug(f"[仓库文件API] 下载URL: {url}")

    try:
        os.makedirs(os.path.dirname(local_file), exist_ok=True)
        timeout = aiohttp.ClientTimeout(total=settings.sync_download_timeout)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url) as resp:
                if resp.status == 404:
                    logger.warning(f"[仓库文件API] 上游包不存在 url={url}")
                    return error_response("上游包不存在", status_code=502)
                resp.raise_for_status()
                with open(local_file, "wb") as f:
                    async for chunk in resp.content.iter_chunked(8192):
                        f.write(chunk)
    except aiohttp.ClientError as e:
        logger.warning("重新下载失败 %s: %s", url, e)
        return error_response(f"下载失败: {e}", status_code=502)
    except OSError as e:
        logger.warning("写入文件失败 %s: %s", local_file, e)
        return error_response(f"写入文件失败: {e}")

    # 更新元数据
    with open(local_file, "rb") as f:
        computed_sha256 = hashlib.sha256(f.read()).hexdigest()
    build.cached = True
    build.package_size = os.path.getsize(local_file)
    build.sha256 = computed_sha256

    audit = AuditLog(
        actor=current_user.username,
        action="repo_file_redownload",
        resource=build.package_path,
        result="success",
    )
    db.add(audit)
    await db.commit()

    logger.info(
        f"[仓库文件API] 重新下载成功 build_id={build_id} "
        f"path={build.package_path} size={build.package_size}"
    )
    return success({
        "build_id": build_id,
        "package_path": build.package_path,
        "package_size": build.package_size,
        "sha256": build.sha256,
        "cached": True,
    }, 1, "重新下载成功")


# ─── SHA256 验证 ───────────────────────────────────────────────

@router.post("/packages/{build_id}/verify")
async def verify_package(
    build_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_auth),
):
    """计算文件 SHA256 并与数据库记录比对。

    - 文件不存在时返回 404
    - 返回 {matched, computed, stored}
    - 写 AuditLog (action=repo_file_verify)
    """
    logger.info(f"[仓库文件API] 验证SHA256 build_id={build_id} user={current_user.username}")
    build = await db.get(ExtensionBuild, build_id)
    if not build:
        logger.warning(f"[仓库文件API] 验证失败：构建记录不存在 build_id={build_id}")
        return error_response("构建记录不存在", status_code=404)

    try:
        file_path = _validate_path(build.package_path, str(settings.repo_dir))
    except ValueError as e:
        logger.warning(f"[仓库文件API] 验证失败：路径非法 build_id={build_id}")
        return error_response(str(e), status_code=400)

    if not os.path.exists(file_path):
        logger.warning(f"[仓库文件API] 验证失败：文件不存在 build_id={build_id} path={file_path}")
        return error_response("文件不存在", status_code=404)

    with open(file_path, "rb") as f:
        computed = hashlib.sha256(f.read()).hexdigest()
    stored = build.sha256 or ""

    if not stored:
        # 数据库无 SHA256 记录，自动计算并存储
        build.sha256 = computed
        await db.flush()
        matched = True
        msg = "SHA256 已自动计算并保存"
    else:
        matched = computed == stored
        msg = "SHA256 匹配" if matched else "SHA256 不匹配"

    audit = AuditLog(
        actor=current_user.username,
        action="repo_file_verify",
        resource=build.package_path,
        result="success",
    )
    db.add(audit)
    await db.commit()

    if matched:
        logger.info(f"[仓库文件API] 验证成功 build_id={build_id} path={build.package_path}")
    else:
        logger.warning(
            f"[仓库文件API] SHA256 不匹配 build_id={build_id} path={build.package_path} "
            f"computed={computed[:12]}... stored={stored[:12]}..."
        )

    return success({
        "matched": matched,
        "computed": computed,
        "stored": stored or computed,
    }, 1, msg)


# ─── 一致性检查 ────────────────────────────────────────────────

@router.post("/consistency-check")
async def consistency_check(
    db: AsyncSession = Depends(get_db),
):
    """扫描文件系统与数据库比对。

    - os.walk(repo_dir) 收集所有 .tar 相对路径
    - 与数据库 ExtensionBuild.package_path (cached=True) 集合做差集
    - 返回 {missing_files, orphan_files}
    """
    logger.info("[仓库文件API] 开始一致性检查")
    repo_dir = str(settings.repo_dir)

    # 收集磁盘上所有 .tar 相对路径
    disk_files: set[str] = set()
    for root, _dirs, files in os.walk(repo_dir):
        for fname in files:
            if fname.endswith(".tar"):
                full_path = os.path.join(root, fname)
                rel_path = os.path.relpath(full_path, repo_dir).replace("\\", "/")
                disk_files.add(rel_path)

    # 收集数据库中 cached=True 的 package_path
    db_result = await db.execute(
        select(ExtensionBuild.package_path).where(
            ExtensionBuild.cached == True  # noqa: E712
        )
    )
    db_files = {row[0] for row in db_result.all()}

    # 差集
    missing = db_files - disk_files  # 数据库有但磁盘没有
    orphans = disk_files - db_files  # 磁盘有但数据库没有

    # 缺失文件详情（需要 build_id, package_path, extension_name）
    missing_data = []
    if missing:
        miss_result = await db.execute(
            select(ExtensionBuild.id, ExtensionBuild.package_path, Extension.name)
            .join(ExtensionVersion, ExtensionBuild.version_id == ExtensionVersion.id)
            .join(Extension, ExtensionVersion.extension_id == Extension.id)
            .where(ExtensionBuild.package_path.in_(missing))
        )
        for build_id, package_path, ext_name in miss_result.all():
            missing_data.append({
                "build_id": build_id,
                "package_path": package_path,
                "extension_name": ext_name,
            })

    # 孤儿文件详情（需要 file_path, file_size）
    orphan_data = []
    for rel_path in sorted(orphans):
        full_path = os.path.join(repo_dir, rel_path)
        file_size = os.path.getsize(full_path) if os.path.exists(full_path) else 0
        orphan_data.append({
            "file_path": rel_path,
            "file_size": file_size,
        })

    return success({
        "missing_files": missing_data,
        "orphan_files": orphan_data,
    }, 1)

    logger.info(
        f"[仓库文件API] 一致性检查完成: missing={len(missing_data)} orphans={len(orphan_data)}"
    )


# ─── 修复一致性（将孤儿文件同步到数据库） ────────────────────────

@router.post("/repair-consistency")
async def repair_consistency(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_auth),
):
    """修复一致性：将磁盘上的孤儿文件同步到数据库。

    扫描所有磁盘上的 .tar 文件，如果数据库中没有对应记录，
    则从路径和包名解析信息并创建完整的三层记录：
    Publisher -> Extension -> ExtensionVersion -> ExtensionBuild

    这用于修复历史遗留问题（代理下载时没有创建数据库记录）。
    """
    logger.info(f"[仓库文件API] 开始修复一致性 user={current_user.username}")
    repo_dir = str(settings.repo_dir)

    db_result = await db.execute(
        select(ExtensionBuild.package_path).where(
            ExtensionBuild.cached == True  # noqa: E712
        )
    )
    db_files = {row[0] for row in db_result.all()}

    created_count = 0
    skipped_count = 0
    failed_count = 0

    for root, _dirs, files in os.walk(repo_dir):
        for fname in files:
            if fname.endswith(".tar"):
                full_path = os.path.join(root, fname)
                rel_path = os.path.relpath(full_path, repo_dir).replace("\\", "/")

                if rel_path in db_files:
                    skipped_count += 1
                    continue

                try:
                    parts = rel_path.split("/")
                    if len(parts) < 4:
                        failed_count += 1
                        continue

                    publisher = parts[0]
                    arch = parts[1]
                    os_name = parts[2]
                    package_name = fname[:-4] if fname.endswith(".tar") else fname

                    ext_info = parse_package_name(package_name)
                    if not ext_info:
                        logger.warning(f"无法解析包名: {package_name}")
                        failed_count += 1
                        continue

                    pub_result = await db.execute(
                        select(Publisher).where(Publisher.name == publisher)
                    )
                    pub = pub_result.scalar_one_or_none()
                    if not pub:
                        pub = Publisher(name=publisher, display_name=publisher)
                        db.add(pub)
                        await db.flush()

                    ext_result = await db.execute(
                        select(Extension).where(Extension.name == ext_info["name"])
                    )
                    ext = ext_result.scalar_one_or_none()
                    if not ext:
                        ext = Extension(name=ext_info["name"], publisher_id=pub.id)
                        db.add(ext)
                        await db.flush()

                    ver_result = await db.execute(
                        select(ExtensionVersion).where(
                            ExtensionVersion.extension_id == ext.id,
                            ExtensionVersion.version == ext_info["version"],
                        )
                    )
                    ver = ver_result.scalar_one_or_none()
                    if not ver:
                        ver = ExtensionVersion(
                            extension_id=ext.id,
                            version=ext_info["version"],
                            channel="stable",
                        )
                        db.add(ver)
                        await db.flush()

                    package_size = os.path.getsize(full_path) if os.path.exists(full_path) else None

                    new_build = ExtensionBuild(
                        version_id=ver.id,
                        postgres_version=ext_info["postgres_version"],
                        arch=arch,
                        os=os_name,
                        flavor=ext_info["flavor"],
                        build=ext_info["build"],
                        package_path=rel_path,
                        package_size=package_size,
                        cached=True,
                    )
                    db.add(new_build)
                    await db.commit()

                    created_count += 1
                    db_files.add(rel_path)

                    if created_count % 100 == 0:
                        logger.info(f"[仓库文件API] 已创建 {created_count} 条记录")

                except Exception as e:
                    logger.warning(f"创建记录失败 {rel_path}: {e}")
                    failed_count += 1
                    await db.rollback()

    audit = AuditLog(
        actor=current_user.username,
        action="repo_file_repair",
        resource=f"created={created_count}, skipped={skipped_count}, failed={failed_count}",
        result="success",
    )
    db.add(audit)
    await db.commit()

    logger.info(
        f"[仓库文件API] 一致性修复完成: created={created_count}, skipped={skipped_count}, failed={failed_count}"
    )
    return success({
        "created": created_count,
        "skipped": skipped_count,
        "failed": failed_count,
    }, 1, f"修复完成，共创建 {created_count} 条记录")
