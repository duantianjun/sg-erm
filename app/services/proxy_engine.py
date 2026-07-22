"""混合模式代理引擎。

核心流程（设计文档 5.5）：
1. StackGres 请求 .tar 文件
2. 检查本地缓存 → HIT: 返回文件
3. 未命中 → 检查代理模式:
   - strict: 返回 404
   - hybrid/proxy_only: 从上游拉取 → 缓存 → 返回 (MISS)
4. 上游也没有 → 404

三种模式：
- hybrid (默认): 预同步白名单 + 代理兜底
- strict: 仅返回本地已缓存的包
- proxy_only: 不预同步，按需代理（永远 MISS → 拉取）
"""
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

import aiohttp
from sqlalchemy import select

from app.config import settings
from app.database import async_session_factory
from app.models import ExtensionBuild, RepositorySource
from app.services.naming import (
    INDEX_PATH,
    get_index_url,
    get_local_path,
    get_package_url,
)

logger = logging.getLogger(__name__)

# 返回状态常量
HIT = "HIT"       # 本地缓存命中
MISS = "MISS"     # 从上游拉取并缓存
NOT_FOUND = "404"  # 上游也没有


class ProxyEngine:
    """混合模式代理引擎。"""

    def __init__(
        self,
        session_factory=async_session_factory,
        config=settings,
    ):
        self.session_factory = session_factory
        self.config = config

    async def handle_package_request(
        self,
        publisher: str,
        arch: str,
        os_name: str,
        package_name: str,
    ) -> tuple[Optional[Path], str]:
        """处理 .tar 文件请求。

        Args:
            publisher: 发布者 (如 com.ongres)
            arch: 架构 (如 x86_64)
            os_name: 操作系统 (如 linux)
            package_name: 包名 (如 postgis-3.4-pg16.4)

        Returns:
            (file_path, status): file_path 为 None 时表示 404
                                 status 为 HIT/MISS/404
        """
        # 构造本地路径
        relative_path = get_local_path(publisher, arch, os_name, package_name)
        local_path = self.config.repo_dir / relative_path

        # 1. 检查本地缓存（HIT）
        if local_path.exists() and local_path.stat().st_size > 0:
            logger.debug(f"HIT: {relative_path}")
            # 更新访问时间（用于 LRU 淘汰）
            await self._update_access_time(publisher, arch, os_name, package_name)
            return local_path, HIT

        # 2. strict 模式：不代理，直接 404
        if self.config.proxy_mode == "strict":
            logger.debug(f"MISS (strict mode, no proxy): {relative_path}")
            return None, NOT_FOUND

        # 3. hybrid / proxy_only 模式：从上游拉取
        source_url = await self._get_upstream_url()
        if not source_url:
            logger.warning("无可用上游仓库源")
            return None, NOT_FOUND

        # 构造上游 URL
        url = get_package_url(source_url, publisher, arch, os_name, package_name)
        logger.info(f"MISS: 代理拉取 {url}")

        try:
            # 确保目录存在
            local_path.parent.mkdir(parents=True, exist_ok=True)

            # 断点续传：检查是否有未完成的临时文件
            tmp_path = Path(str(local_path) + ".tmp")
            resume_offset = 0
            if tmp_path.exists() and tmp_path.stat().st_size > 0:
                resume_offset = tmp_path.stat().st_size

            headers = {}
            if resume_offset > 0:
                headers["Range"] = f"bytes={resume_offset}-"

            # 异步下载
            timeout = aiohttp.ClientTimeout(total=self.config.sync_download_timeout)
            async with aiohttp.ClientSession(timeout=timeout) as http_session:
                async with http_session.get(url, headers=headers) as resp:
                    if resp.status == 404:
                        logger.warning(f"上游 404: {url}")
                        return None, NOT_FOUND

                    if resp.status == 416:
                        # Range Not Satisfiable — 重试
                        tmp_path.unlink(missing_ok=True)
                        async with http_session.get(url) as resp2:
                            resp2.raise_for_status()
                            with open(tmp_path, "wb") as f:
                                async for chunk in resp2.content.iter_chunked(8192):
                                    f.write(chunk)
                    elif resp.status == 206:
                        # Partial Content — 续传
                        logger.debug(
                            f"代理断点续传: {relative_path} "
                            f"从 {resume_offset} 字节继续"
                        )
                        with open(tmp_path, "ab") as f:
                            async for chunk in resp.content.iter_chunked(8192):
                                f.write(chunk)
                    else:
                        resp.raise_for_status()
                        with open(tmp_path, "wb") as f:
                            async for chunk in resp.content.iter_chunked(8192):
                                f.write(chunk)

            # 下载完成，重命名为最终文件名
            tmp_path.rename(local_path)

            logger.info(f"MISS → 已缓存: {relative_path} ({local_path.stat().st_size} bytes)")

            # 更新数据库（标记为已缓存）
            await self._mark_cached(publisher, arch, os_name, package_name)

            return local_path, MISS

        except aiohttp.ClientError as e:
            logger.warning(f"代理拉取失败 (网络错误): {url}: {e}")
            # 清理可能的不完整文件（保留 .tmp 用于断点续传）
            if local_path.exists():
                local_path.unlink()
            return None, NOT_FOUND
        except Exception as e:
            logger.exception(f"代理拉取异常: {url}: {e}")
            if local_path.exists():
                local_path.unlink()
            return None, NOT_FOUND

    async def handle_index_request(self) -> Optional[Path]:
        """处理 index.json 请求。

        如果有多个启用的仓库源，执行多源聚合；
        否则返回单源本地缓存或从上游获取。
        """
        index_path = self.config.repo_dir / INDEX_PATH

        # 本地有 → 直接返回
        if index_path.exists():
            logger.debug("index.json HIT")
            return index_path

        # 检查是否有多源配置
        multi_source = await self._has_multiple_sources()

        if multi_source:
            # 多源聚合模式
            from app.services.index_aggregator import build_aggregated_index
            path = await build_aggregated_index()
            return path

        # 单源回退
        source_url = await self._get_upstream_url()
        if not source_url:
            return None

        url = get_index_url(source_url)
        logger.info(f"index.json MISS: 从上游获取 {url}")

        try:
            index_path.parent.mkdir(parents=True, exist_ok=True)
            timeout = aiohttp.ClientTimeout(total=self.config.sync_download_timeout)
            async with aiohttp.ClientSession(timeout=timeout) as http_session:
                async with http_session.get(url) as resp:
                    resp.raise_for_status()
                    with open(index_path, "wb") as f:
                        async for chunk in resp.content.iter_chunked(8192):
                            f.write(chunk)

            logger.info(f"index.json 已缓存: {index_path}")
            return index_path

        except Exception as e:
            logger.warning(f"获取 index.json 失败: {e}")
            return None

    async def _has_multiple_sources(self) -> bool:
        """检查是否有多个启用的仓库源。"""
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

    # ─── 内部方法 ───────────────────────────────────────────────

    async def _get_upstream_url(self) -> Optional[str]:
        """获取优先级最高的启用仓库源 URL。

        如果数据库中有启用的源，取优先级最高的；
        否则使用配置中的默认上游 URL。
        """
        try:
            async with self.session_factory() as session:
                result = await session.execute(
                    select(RepositorySource)
                    .where(RepositorySource.enabled == True)  # noqa: E712
                    .order_by(RepositorySource.priority)
                    .limit(1)
                )
                source = result.scalar_one_or_none()
                if source:
                    return source.url
        except Exception as e:
            logger.warning(f"查询仓库源失败: {e}")

        # 回退到配置默认值
        return self.config.upstream_repo_url

    async def _update_access_time(
        self,
        publisher: str,
        arch: str,
        os_name: str,
        package_name: str,
    ) -> None:
        """更新包的最后访问时间（用于 LRU 淘汰）。

        在 ExtensionBuild 表中查找匹配的记录并更新 last_accessed。
        """
        try:
            # 构造 package_path 用于查找
            relative_path = get_local_path(publisher, arch, os_name, package_name)
            async with self.session_factory() as session:
                result = await session.execute(
                    select(ExtensionBuild).where(
                        ExtensionBuild.package_path == relative_path
                    )
                )
                build = result.scalar_one_or_none()
                if build:
                    build.last_accessed = datetime.utcnow()
                    await session.commit()
        except Exception as e:
            logger.debug(f"更新访问时间失败（非致命）: {e}")

    async def _mark_cached(
        self,
        publisher: str,
        arch: str,
        os_name: str,
        package_name: str,
    ) -> None:
        """在数据库中标记包为已缓存。"""
        try:
            relative_path = get_local_path(publisher, arch, os_name, package_name)
            async with self.session_factory() as session:
                result = await session.execute(
                    select(ExtensionBuild).where(
                        ExtensionBuild.package_path == relative_path
                    )
                )
                build = result.scalar_one_or_none()
                if build:
                    build.cached = True
                    build.last_accessed = datetime.utcnow()
                    if build.package_size is None:
                        # 更新文件大小
                        local_file = self.config.repo_dir / relative_path
                        if local_file.exists():
                            build.package_size = local_file.stat().st_size
                    await session.commit()
        except Exception as e:
            logger.debug(f"标记缓存失败（非致命）: {e}")


# 全局单例
proxy_engine = ProxyEngine()
