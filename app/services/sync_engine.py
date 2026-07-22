"""异步同步引擎。

核心流程：
1. 获取上游 index.json（aiohttp 异步请求）
2. 解析扩展元数据
3. 应用 SyncPolicy 过滤器（arch/os/publisher/extensions 白名单）
4. 与本地对比，计算 diff（新增/变更/删除）
5. 并发下载新增和变更的包（asyncio.Semaphore 控制并发）
6. 更新本地 index.json
7. 全程通过进度回调推送事件（供 WebSocket 转发）
"""
import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Coroutine

import aiohttp

from app.config import settings
from app.database import async_session_factory
from app.models import (
    Extension,
    ExtensionBuild,
    ExtensionVersion,
    GlobalWhitelist,
    Publisher,
    RepositorySource,
    SyncTask,
)
from app.services.naming import (
    INDEX_PATH,
    get_arch,
    get_flavor_prefix,
    get_index_url,
    get_local_path,
    get_os,
    get_package_name,
    get_package_url,
    get_publisher_name,
)

logger = logging.getLogger(__name__)

# 进度回调类型
ProgressCallback = Callable[[dict], Coroutine[Any, Any, None]]


class SyncEngine:
    """异步扩展同步引擎。"""

    def __init__(
        self,
        session_factory=async_session_factory,
        config=settings,
    ):
        self.session_factory = session_factory
        self.config = config
        self._progress_callbacks: list[ProgressCallback] = []
        self._running_tasks: dict[str, asyncio.Task] = {}

    def add_progress_callback(self, callback: ProgressCallback) -> None:
        """注册进度回调（用于 WebSocket 推送）。"""
        self._progress_callbacks.append(callback)

    async def _notify(self, event: dict) -> None:
        """通知所有进度回调。"""
        for cb in self._progress_callbacks:
            try:
                await cb(event)
            except Exception as e:
                logger.warning(f"进度回调失败: {e}")

    # ─── 公开接口 ───────────────────────────────────────────────

    async def run(
        self,
        source_id: str,
        policy_id: str | None = None,
        dry_run: bool = False,
    ) -> SyncTask:
        """执行完整同步流程。

        Args:
            source_id: 仓库源 ID
            policy_id: 同步策略 ID（可选）
            dry_run: 仅模拟，不实际下载

        Returns:
            SyncTask 记录
        """
        # 创建 SyncTask 记录
        async with self.session_factory() as session:
            source = await session.get(RepositorySource, source_id)
            if not source:
                raise ValueError(f"仓库源不存在: {source_id}")

            task = SyncTask(
                source_id=source_id,
                policy_id=policy_id,
                status="running",
                started_at=datetime.utcnow(),
            )
            session.add(task)
            await session.commit()
            await session.refresh(task)
            task_id = task.id

        # 在后台运行实际同步
        coro = self._execute(task_id, source_id, policy_id, dry_run)
        async_task = asyncio.create_task(coro)
        self._running_tasks[task_id] = async_task

        return task

    async def cancel(self, task_id: str) -> bool:
        """取消运行中的同步任务。"""
        async_task = self._running_tasks.get(task_id)
        if async_task and not async_task.done():
            async_task.cancel()
            return True
        return False

    # ─── 内部实现 ───────────────────────────────────────────────

    async def _execute(
        self,
        task_id: str,
        source_id: str,
        policy_id: str | None,
        dry_run: bool,
    ) -> None:
        """实际同步执行逻辑。"""
        try:
            async with self.session_factory() as session:
                source = await session.get(RepositorySource, source_id)
                task = await session.get(SyncTask, task_id)

                await self._notify({
                    "type": "start",
                    "task_id": task_id,
                    "source": source.name,
                    "message": f"开始同步: {source.name}",
                })

                # 1. 获取 index.json
                index_data = await self._fetch_index(source.url)
                logger.info(f"[{task_id}] 获取 index.json 成功, "
                            f"扩展数: {len(index_data.get('extensions', []))}")

                # 2. 解析并收集包
                filters = await self._get_filters(session, policy_id)
                packages = self._collect_packages(index_data, filters)
                total = len(packages)

                await self._update_task(session, task, total=total)
                await self._notify({
                    "type": "progress",
                    "task_id": task_id,
                    "total": total,
                    "downloaded": 0,
                    "failed": 0,
                    "skipped": 0,
                    "message": f"匹配到 {total} 个包",
                })

                if dry_run:
                    # dry-run 模式：仅报告，不下载
                    await self._update_task(
                        session, task,
                        status="completed",
                        downloaded=0,
                        skipped=total,
                        finished_at=datetime.utcnow(),
                        diff_summary={"dry_run": True, "packages": total, "removed": 0},
                    )
                    await self._notify({
                        "type": "complete",
                        "task_id": task_id,
                        "message": f"[模拟] 匹配 {total} 个包",
                    })
                    return

                # 3. 预写数据库元数据（cached=False，让仓库文件浏览器立即可见）
                await self._update_db_metadata(session, index_data, source_id)

                # 4. 并发下载
                downloaded, failed, skipped = await self._download_packages(
                    packages, source.url, task_id,
                )

                # 5. 更新已下载文件的缓存状态（cached=True）
                await self._mark_cached(packages)

                # 6. 清理已从上游移除的本地包
                removed = await self._cleanup_removed_packages(
                    packages, source_id
                )

                # 7. 再次刷新元数据（更新 cached 状态）
                await self._update_db_metadata(session, index_data, source_id)

                # 6. 更新本地 index.json
                await self._update_local_index(index_data)

                # 7. 完成
                await self._update_task(
                    session, task,
                    status="completed",
                    downloaded=downloaded,
                    failed=failed,
                    skipped=skipped,
                    finished_at=datetime.utcnow(),
                    diff_summary={
                        "total": total,
                        "downloaded": downloaded,
                        "failed": failed,
                        "skipped": skipped,
                        "removed": removed,
                    },
                )

                # 更新源同步状态
                source.last_sync = datetime.utcnow()
                source.last_sync_status = "success"

                await self._notify({
                    "type": "complete",
                    "task_id": task_id,
                    "total": total,
                    "downloaded": downloaded,
                    "failed": failed,
                    "skipped": skipped,
                    "message": f"同步完成: 下载 {downloaded}, 跳过 {skipped}, 失败 {failed}",
                })

        except asyncio.CancelledError:
            async with self.session_factory() as session:
                task = await session.get(SyncTask, task_id)
                if task:
                    await self._update_task(
                        session, task,
                        status="cancelled",
                        finished_at=datetime.utcnow(),
                    )
                await self._notify({"type": "cancelled", "task_id": task_id})
            raise

        except Exception as e:
            logger.exception(f"同步任务 {task_id} 失败")
            async with self.session_factory() as session:
                task = await session.get(SyncTask, task_id)
                source = await session.get(RepositorySource, source_id)
                if task:
                    await self._update_task(
                        session, task,
                        status="failed",
                        error_message=str(e),
                        finished_at=datetime.utcnow(),
                    )
                if source:
                    source.last_sync = datetime.utcnow()
                    source.last_sync_status = "failed"
                await session.commit()
            await self._notify({
                "type": "error",
                "task_id": task_id,
                "message": str(e),
            })
        finally:
            self._running_tasks.pop(task_id, None)

    # ─── 获取 index.json ────────────────────────────────────────

    async def _fetch_index(self, source_url: str) -> dict:
        """异步获取上游 index.json。"""
        url = get_index_url(source_url)
        timeout = aiohttp.ClientTimeout(total=self.config.sync_download_timeout)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url) as resp:
                resp.raise_for_status()
                return await resp.json()

    # ─── 过滤策略 ───────────────────────────────────────────────

    async def _get_filters(self, session, policy_id: str | None) -> dict:
        """获取过滤配置。

        1. 如果有 policy_id，从 SyncPolicy.filters 读取策略级过滤器；
        2. 合并全局白名单作为扩展 include 基线。
        """
        filters = {}
        if policy_id:
            from app.models import SyncPolicy
            policy = await session.get(SyncPolicy, policy_id)
            if policy and policy.filters:
                filters = dict(policy.filters)

        # 合并全局白名单为 extensions.include 基线
        wl_result = await session.execute(
            select(GlobalWhitelist.extension_name)
        )
        wl_names = [row[0] for row in wl_result.all()]
        if wl_names:
            ext_filters = filters.setdefault("extensions", {})
            wl_set = set(wl_names)
            existing_include = set(ext_filters.get("include", []))
            if existing_include:
                # 策略级 include 与白名单取交集
                merged = existing_include & wl_set
            else:
                # 无策略级 include，白名单即 include
                merged = wl_set
            if merged:
                ext_filters["include"] = sorted(merged)

        return filters

    def _collect_packages(self, index_data: dict, filters: dict) -> list[dict]:
        """收集所有匹配过滤器的包。

        Args:
            index_data: index.json 解析后的字典
            filters: 过滤配置，支持 arch/os/publisher/extensions

        Returns:
            包信息列表
        """
        packages = []
        seen = set()

        # 解析过滤器
        arch_filter = filters.get("arch")
        os_filter = filters.get("os")
        publisher_filter = filters.get("publisher")
        ext_include = filters.get("extensions", {}).get("include")
        ext_exclude = set(filters.get("extensions", {}).get("exclude", []))

        # 如果过滤器是列表，转为集合方便查找
        arch_set = set(arch_filter) if arch_filter else None
        os_set = set(os_filter) if os_filter else None
        publisher_set = set(publisher_filter) if publisher_filter else None
        ext_include_set = set(ext_include) if ext_include else None

        for ext_data in index_data.get("extensions", []):
            ext_name = ext_data.get("name", "")
            publisher = get_publisher_name(ext_data.get("publisher"))

            # 过滤发布者
            if publisher_set and publisher not in publisher_set:
                continue

            # 过滤扩展名
            if ext_name in ext_exclude:
                continue
            if ext_include_set and ext_name not in ext_include_set:
                continue

            for ver_data in ext_data.get("versions", []):
                version = ver_data.get("version", "")

                for target in ver_data.get("availableFor", []):
                    flavor = get_flavor_prefix(target.get("flavor"))
                    pg_version = target.get("postgresVersion")
                    build = target.get("build")
                    arch = get_arch(target.get("arch"))
                    os_name = get_os(target.get("os"))

                    # 过滤架构和 OS
                    if arch_set and arch not in arch_set:
                        continue
                    if os_set and os_name not in os_set:
                        continue

                    pkg_name = get_package_name(
                        ext_name, version, flavor, pg_version, build
                    )

                    # 去重
                    key = (publisher, arch, os_name, pkg_name)
                    if key in seen:
                        continue
                    seen.add(key)

                    packages.append({
                        "publisher": publisher,
                        "arch": arch,
                        "os": os_name,
                        "package_name": pkg_name,
                        "extension_name": ext_name,
                        "version": version,
                        "flavor": flavor,
                        "pg_version": pg_version,
                        "build": build,
                        "local_path": get_local_path(
                            publisher, arch, os_name, pkg_name
                        ),
                    })

        return packages

    # ─── 并发下载 ────────────────────────────────────────────────

    async def _download_packages(
        self,
        packages: list[dict],
        source_url: str,
        task_id: str,
    ) -> tuple[int, int, int]:
        """并发下载所有包。

        Returns:
            (downloaded, failed, skipped)
        """
        total = len(packages)
        if total == 0:
            return 0, 0, 0

        downloaded = 0
        failed = 0
        skipped = 0
        semaphore = asyncio.Semaphore(self.config.sync_concurrency)

        timeout = aiohttp.ClientTimeout(total=self.config.sync_download_timeout)
        connector = aiohttp.TCPConnector(limit=self.config.sync_concurrency)

        async with aiohttp.ClientSession(timeout=timeout, connector=connector) as http_session:
            async def download_one(pkg: dict) -> str:
                async with semaphore:
                    local_file = self.config.repo_dir / pkg["local_path"]
                    pkg_name = pkg["package_name"]
                    tmp_file = Path(str(local_file) + ".tmp")

                    # 检查是否已下载完成
                    if local_file.exists() and local_file.stat().st_size > 0:
                        return "skipped"

                    url = get_package_url(
                        source_url, pkg["publisher"], pkg["arch"],
                        pkg["os"], pkg["package_name"],
                    )

                    try:
                        local_file.parent.mkdir(parents=True, exist_ok=True)

                        # 断点续传：检查是否有未完成的临时文件
                        resume_offset = 0
                        if tmp_file.exists() and tmp_file.stat().st_size > 0:
                            resume_offset = tmp_file.stat().st_size

                        headers = {}
                        if resume_offset > 0:
                            headers["Range"] = f"bytes={resume_offset}-"

                        async with http_session.get(url, headers=headers) as resp:
                            if resp.status == 404:
                                logger.warning(f"404: {url}")
                                return "not_found"

                            if resp.status == 416:
                                # Range Not Satisfiable — 临时文件可能已完成
                                logger.info(f"416 范围无效，重试完整下载: {pkg_name}")
                                tmp_file.unlink(missing_ok=True)
                                resume_offset = 0
                                async with http_session.get(url) as resp2:
                                    resp2.raise_for_status()
                                    with open(tmp_file, "wb") as f:
                                        async for chunk in resp2.content.iter_chunked(8192):
                                            f.write(chunk)
                            elif resp.status == 206:
                                # Partial Content — 续传
                                logger.info(
                                    f"断点续传: {pkg_name} "
                                    f"从 {resume_offset} 字节继续"
                                )
                                with open(tmp_file, "ab") as f:
                                    async for chunk in resp.content.iter_chunked(8192):
                                        f.write(chunk)
                            else:
                                resp.raise_for_status()
                                with open(tmp_file, "wb") as f:
                                    async for chunk in resp.content.iter_chunked(8192):
                                        f.write(chunk)

                        # 下载完成，重命名为最终文件名
                        tmp_file.rename(local_file)
                        return "ok"
                    except Exception as e:
                        logger.warning(f"下载失败 {pkg_name}: {e}")
                        return f"error"

                    finally:
                        nonlocal downloaded, failed, skipped
                        # 进度更新在 _update_progress 中处理

            # 使用 asyncio.gather 并发下载
            results = await asyncio.gather(
                *[download_one(pkg) for pkg in packages],
                return_exceptions=True,
            )

            for result in results:
                if isinstance(result, Exception):
                    failed += 1
                elif result == "ok":
                    downloaded += 1
                elif result == "skipped":
                    skipped += 1
                else:
                    failed += 1

            # 更新进度
            await self._notify({
                "type": "progress",
                "task_id": task_id,
                "total": total,
                "downloaded": downloaded,
                "failed": failed,
                "skipped": skipped,
            })

        return downloaded, failed, skipped

    # ─── 数据库更新 ─────────────────────────────────────────────

    async def _update_db_metadata(
        self,
        session,
        index_data: dict,
        source_id: str,
    ) -> None:
        """将 index.json 中的扩展元数据写入数据库。

        更新三层模型：Extension -> ExtensionVersion -> ExtensionBuild。
        对已下载到本地的包设置 cached=True 和 package_size。
        """
        import os

        from sqlalchemy import select

        repo_dir = str(self.config.repo_dir)

        # 更新 publishers
        for pub_data in index_data.get("publishers", []):
            pub_id = pub_data.get("id", "")
            if not pub_id:
                continue
            result = await session.execute(
                select(Publisher).where(Publisher.name == pub_id)
            )
            pub = result.scalar_one_or_none()
            if not pub:
                pub = Publisher(
                    name=pub_id,
                    display_name=pub_data.get("name"),
                    public_key=pub_data.get("publicKey"),
                    is_custom=False,
                )
                session.add(pub)
                await session.flush()

        # 更新 extensions / versions / builds
        for ext_data in index_data.get("extensions", []):
            ext_name = ext_data.get("name", "")
            if not ext_name:
                continue
            publisher_name = get_publisher_name(ext_data.get("publisher"))

            result = await session.execute(
                select(Publisher).where(Publisher.name == publisher_name)
            )
            pub = result.scalar_one_or_none()
            if not pub:
                continue

            # Extension
            result = await session.execute(
                select(Extension).where(Extension.name == ext_name)
            )
            ext = result.scalar_one_or_none()
            if not ext:
                ext = Extension(
                    name=ext_name,
                    publisher_id=pub.id,
                    source_id=source_id,
                    description=ext_data.get("description"),
                    abstract=ext_data.get("abstract"),
                    tags=ext_data.get("tags"),
                    url=ext_data.get("url"),
                    source_url=ext_data.get("source"),
                    license=ext_data.get("license"),
                    channels=ext_data.get("channels"),
                    is_custom=False,
                )
                session.add(ext)
                await session.flush()

            # versions
            for ver_data in ext_data.get("versions", []):
                ver_str = ver_data.get("version", "")
                if not ver_str:
                    continue

                result = await session.execute(
                    select(ExtensionVersion)
                    .where(
                        ExtensionVersion.extension_id == ext.id,
                        ExtensionVersion.version == ver_str,
                    )
                )
                ver = result.scalar_one_or_none()
                if not ver:
                    ver = ExtensionVersion(
                        extension_id=ext.id,
                        version=ver_str,
                        channel="stable",
                    )
                    session.add(ver)
                    await session.flush()

                # builds
                seen_build_keys = set()
                for target in ver_data.get("availableFor", []):
                    flavor = get_flavor_prefix(target.get("flavor"))
                    pg_version = target.get("postgresVersion", "")
                    arch = get_arch(target.get("arch"))
                    os_name = get_os(target.get("os"))
                    build_num = target.get("build")

                    pkg_name = get_package_name(
                        ext_name, ver_str, flavor, pg_version, build_num
                    )
                    local_path = get_local_path(publisher_name, arch, os_name, pkg_name)
                    full_path = os.path.join(repo_dir, local_path)
                    file_exists = os.path.exists(full_path)
                    file_size = os.path.getsize(full_path) if file_exists else None

                    # 去重（同一组合可能出现多次）
                    build_key = (ver.id, pg_version, arch, os_name, flavor, build_num or "")
                    if build_key in seen_build_keys:
                        continue
                    seen_build_keys.add(build_key)

                    result = await session.execute(
                        select(ExtensionBuild)
                        .where(
                            ExtensionBuild.version_id == ver.id,
                            ExtensionBuild.postgres_version == pg_version,
                            ExtensionBuild.arch == arch,
                            ExtensionBuild.os == os_name,
                            ExtensionBuild.flavor == flavor,
                            ExtensionBuild.build == (build_num or ""),
                        )
                    )
                    build = result.scalar_one_or_none()
                    if not build:
                        build = ExtensionBuild(
                            version_id=ver.id,
                            postgres_version=pg_version,
                            arch=arch,
                            os=os_name,
                            flavor=flavor,
                            build=build_num or "",
                            package_path=local_path,
                            package_size=file_size,
                            cached=file_exists,
                        )
                        session.add(build)
                    else:
                        # 更新现有记录
                        build.package_path = local_path
                        build.package_size = file_size
                        build.cached = file_exists

        await session.commit()

    async def _mark_cached(self, packages: list[dict]) -> None:
        """将已下载到本地的包标记为 cached=True。

        批量更新，避免逐条查询。
        """
        import os

        from sqlalchemy import select

        repo_dir = str(self.config.repo_dir)

        # 收集本地已存在的文件路径
        local_paths = set()
        for pkg in packages:
            local_file = os.path.join(repo_dir, pkg["local_path"])
            if os.path.exists(local_file):
                local_paths.add(pkg["local_path"])

        if not local_paths:
            return

        async with self.session_factory() as session:
            result = await session.execute(
                select(ExtensionBuild).where(
                    ExtensionBuild.package_path.in_(local_paths)
                )
            )
            builds = result.scalars().all()
            for build in builds:
                full_path = os.path.join(repo_dir, build.package_path)
                if os.path.exists(full_path):
                    build.cached = True
                    build.package_size = os.path.getsize(full_path)
            await session.commit()
            logger.info(f"更新 {len(builds)} 个包的缓存状态为 cached=True")

    # ─── 旧包清理 ─────────────────────────────────────────────

    async def _cleanup_removed_packages(
        self,
        upstream_packages: list[dict],
        source_id: str,
    ) -> int:
        """清理已从上游移除的本地包。

        比较上游包列表与本地磁盘文件，删除上游不再提供的本地 .tar 文件。
        仅清理与该源关联的 publisher 目录下的文件，不影响自定义扩展。

        Returns:
            删除的文件数量
        """
        # 构造上游包的本地路径集合
        upstream_paths = set()
        for pkg in upstream_packages:
            upstream_paths.add(pkg["local_path"])

        # 获取该源关联的 publishers
        from sqlalchemy import select
        async with self.session_factory() as session:
            result = await session.execute(
                select(Publisher).where(Publisher.is_custom == False)  # noqa: E712
            )
            publishers = result.scalars().all()
            pub_names = {p.name for p in publishers}

        removed = 0
        repo_dir = self.config.repo_dir

        for pub_name in pub_names:
            pub_dir = repo_dir / pub_name
            if not pub_dir.exists():
                continue

            # 遍历该 publisher 下所有 .tar 文件
            for tar_file in pub_dir.rglob("*.tar"):
                # 计算相对路径
                rel = tar_file.relative_to(repo_dir).as_posix()
                if rel not in upstream_paths:
                    # 上游已不包含此包，删除
                    try:
                        tar_file.unlink()
                        removed += 1
                        logger.info(f"清理旧包: {rel}")

                        # 同步清理数据库中对应的 ExtensionBuild 缓存状态
                        await self._uncache_build(rel)
                    except OSError as e:
                        logger.warning(f"删除文件失败 {rel}: {e}")

        if removed > 0:
            await self._notify({
                "type": "cleanup",
                "task_id": "",  # 复用当前上下文
                "removed": removed,
                "message": f"清理了 {removed} 个上游已移除的包",
            })

        return removed

    async def _uncache_build(self, relative_path: str) -> None:
        """将 ExtensionBuild 的 cached 标记为 False。"""
        from sqlalchemy import select
        try:
            async with self.session_factory() as session:
                result = await session.execute(
                    select(ExtensionBuild).where(
                        ExtensionBuild.package_path == relative_path
                    )
                )
                build = result.scalar_one_or_none()
                if build:
                    build.cached = False
                    await session.commit()
        except Exception as e:
            logger.debug(f"更新缓存状态失败（非致命）: {e}")

    # ─── 本地 index.json 更新 ──────────────────────────────────

    async def _update_local_index(self, index_data: dict) -> None:
        """更新本地 index.json。

        如果有多源配置，触发聚合；否则直接写入当前源的 index。
        """
        multi_source = await self._has_multiple_sources()
        if multi_source:
            from app.services.index_aggregator import build_aggregated_index
            await build_aggregated_index()
        else:
            index_path = self.config.repo_dir / INDEX_PATH
            index_path.parent.mkdir(parents=True, exist_ok=True)
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                lambda: self._write_json(index_path, index_data),
            )

    async def _has_multiple_sources(self) -> bool:
        """检查是否有多源配置。"""
        from sqlalchemy import func
        try:
            async with self.session_factory() as session:
                count = await session.scalar(
                    select(func.count()).select_from(RepositorySource)
                    .where(RepositorySource.enabled == True)  # noqa: E712
                )
                return (count or 0) > 1
        except Exception:
            return False

    @staticmethod
    def _write_json(path: Path, data: dict) -> None:
        """写入 JSON 文件。"""
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    # ─── 工具方法 ───────────────────────────────────────────────

    async def _update_task(self, session, task, **kwargs) -> None:
        """更新 SyncTask 字段。"""
        for key, value in kwargs.items():
            setattr(task, key, value)
        await session.commit()
        await session.refresh(task)


# 全局单例
sync_engine = SyncEngine()
