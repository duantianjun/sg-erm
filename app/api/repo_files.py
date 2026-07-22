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
from app.services.naming import get_package_url

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/repo-files",
    tags=["repo-files"],
    dependencies=[Depends(require_auth)],
)


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
    query = (
        select(
            ExtensionBuild,
            ExtensionVersion.version,
            Extension.name,
            Publisher.name,
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
    for build, version, ext_name, pub_name in rows:
        file_path = os.path.join(repo_dir, build.package_path)
        data.append({
            "build_id": build.id,
            "publisher": pub_name,
            "arch": build.arch,
            "os": build.os,
            "package_name": os.path.basename(build.package_path),
            "extension_name": ext_name,
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

    return success(data, total)


# ─── 目录树 ────────────────────────────────────────────────────

@router.get("/tree")
async def get_tree(
    db: AsyncSession = Depends(get_db),
):
    """目录树（publisher → arch → os 三级聚合，计算每层 count）。"""
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
    build = await db.get(ExtensionBuild, build_id)
    if not build:
        return error_response("构建记录不存在", status_code=404)

    file_path = os.path.join(str(settings.repo_dir), build.package_path)
    if not os.path.exists(file_path):
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
        return error_response("构建记录不存在", status_code=404)

    build, pub_name, source_url = row
    source_url = source_url or settings.upstream_repo_url

    # 重建上游 URL: {repo_url}/{publisher}/{arch}/{os}/{package_name}.tar
    package_name = os.path.basename(build.package_path)
    base_name = package_name[:-4] if package_name.endswith(".tar") else package_name
    url = get_package_url(source_url, pub_name, build.arch, build.os, base_name)

    local_file = os.path.join(str(settings.repo_dir), build.package_path)

    try:
        os.makedirs(os.path.dirname(local_file), exist_ok=True)
        timeout = aiohttp.ClientTimeout(total=settings.sync_download_timeout)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url) as resp:
                if resp.status == 404:
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
    build = await db.get(ExtensionBuild, build_id)
    if not build:
        return error_response("构建记录不存在", status_code=404)

    file_path = os.path.join(str(settings.repo_dir), build.package_path)
    if not os.path.exists(file_path):
        return error_response("文件不存在", status_code=404)

    with open(file_path, "rb") as f:
        computed = hashlib.sha256(f.read()).hexdigest()
    stored = build.sha256 or ""
    matched = computed == stored

    audit = AuditLog(
        actor=current_user.username,
        action="repo_file_verify",
        resource=build.package_path,
        result="success",
    )
    db.add(audit)
    await db.commit()

    return success({
        "matched": matched,
        "computed": computed,
        "stored": stored,
    }, 1)


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
