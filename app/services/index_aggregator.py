# -*- coding: utf-8 -*-
"""多源 index.json 聚合服务。

当系统配置了多个仓库源时，将所有源的 index.json 合并为一个统一的索引文件。
StackGres 集群只需指向 SG-ERM，即可访问所有源中的扩展。

聚合策略（设计文档 5.3 冲突处理）：
- 同名扩展，不同源：按源优先级，高优先级覆盖元数据（description、tags 等）
- 同名同版本，不同构建：全部保留，StackGres 按 build 匹配
- Publisher 冲突：按 publisher id 合并，高优先级源的 publicKey 胜出

聚合后的 index.json 结构与单个源完全一致，StackGres 无需任何适配。
"""
import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiohttp
from sqlalchemy import select

from app.config import settings
from app.database import async_session_factory
from app.logging_config import get_task_logger
from app.models import RepositorySource
from app.services.naming import INDEX_PATH, get_index_url

logger = logging.getLogger(__name__)
task_logger = get_task_logger()


async def aggregate_indices() -> dict:
    """从所有启用的仓库源获取 index.json 并聚合。

    Returns:
        聚合后的 index.json 字典
    """
    # 获取所有启用的源（按优先级升序，优先级数字越小越高）
    async with async_session_factory() as session:
        result = await session.execute(
            select(RepositorySource)
            .where(RepositorySource.enabled == True)  # noqa: E712
            .order_by(RepositorySource.priority)
        )
        sources = result.scalars().all()

    if not sources:
        logger.warning("没有启用的仓库源")
        return {"publishers": [], "extensions": []}

    task_logger.info(f"[索引聚合] 开始从 {len(sources)} 个仓库源获取 index.json")

    # 并发获取所有源的 index.json
    tasks = [_fetch_source_index(src) for src in sources]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    # 按优先级从低到高处理（低优先级先处理，高优先级后覆盖）
    merged = {
        "publishers": [],
        "extensions": [],
        "aggregatedAt": datetime.now(timezone.utc).isoformat(),
        "sources": [],
    }

    for source, index_data in zip(sources, results):
        if isinstance(index_data, Exception):
            task_logger.error(
                f"[索引聚合] 获取源 {source.name} 的 index.json 失败: {index_data}"
            )
            merged["sources"].append({
                "id": source.id,
                "name": source.name,
                "url": source.url,
                "status": "error",
                "error": str(index_data),
            })
            continue

        merged["sources"].append({
            "id": source.id,
            "name": source.name,
            "url": source.url,
            "status": "ok",
        })

        _merge_into(merged, index_data, source)

    task_logger.info(
        f"[索引聚合] 完成: {len(sources)} 个源, "
        f"{len(merged['extensions'])} 个扩展, "
        f"{len(merged['publishers'])} 个发布者"
    )
    return merged


async def _fetch_source_index(source: RepositorySource) -> dict:
    """获取单个仓库源的 index.json。"""
    url = get_index_url(source.url)
    timeout = aiohttp.ClientTimeout(total=settings.sync_download_timeout)

    # 构建请求头（支持 basic auth）
    headers = {}
    if source.auth_type == "basic" and source.auth_config:
        import base64
        cred = source.auth_config
        token = base64.b64encode(
            f"{cred.get('username', '')}:{cred.get('password', '')}".encode()
        ).decode()
        headers["Authorization"] = f"Basic {token}"

    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url, headers=headers) as resp:
            resp.raise_for_status()
            return await resp.json()


def _merge_into(
    merged: dict,
    source_index: dict,
    source: RepositorySource,
) -> None:
    """将一个源的 index.json 合并到聚合结果中。

    高优先级源的扩展会覆盖低优先级源的元数据，
    但版本和构建会做深度合并（保留所有）。
    """
    source_priority = source.priority

    # ─── 合并 publishers ───────────────────────────────────
    merged_pubs = {p["id"]: p for p in merged["publishers"]}
    for pub in source_index.get("publishers", []):
        pub_id = pub.get("id", "")
        if not pub_id:
            continue
        # 高优先级覆盖低优先级的 publicKey
        if pub_id in merged_pubs:
            # 检查优先级：当前源优先级更高则覆盖
            existing_priority = merged_pubs[pub_id].get("_priority", 999)
            if source_priority <= existing_priority:
                merged_pubs[pub_id]["publicKey"] = pub.get("publicKey", "")
                merged_pubs[pub_id]["_priority"] = source_priority
                merged_pubs[pub_id]["_source_name"] = source.name
        else:
            pub_entry = {
                "id": pub_id,
                "name": pub.get("name", pub_id),
                "publicKey": pub.get("publicKey", ""),
                "_priority": source_priority,
                "_source_name": source.name,
            }
            merged_pubs[pub_id] = pub_entry

    # 清理内部字段，保留 StackGres 兼容格式
    merged["publishers"] = [
        {k: v for k, v in p.items() if not k.startswith("_")}
        for p in merged_pubs.values()
    ]

    # ─── 合并 extensions ───────────────────────────────────
    merged_exts = {e["name"]: e for e in merged["extensions"]}

    for ext in source_index.get("extensions", []):
        ext_name = ext.get("name", "")
        if not ext_name:
            continue

        if ext_name in merged_exts:
            existing = merged_exts[ext_name]
            existing_priority = existing.get("_priority", 999)

            if source_priority <= existing_priority:
                # 高优先级源覆盖元数据
                for meta_key in ("description", "abstract", "tags", "url", "source", "license", "channels"):
                    if ext.get(meta_key) is not None:
                        existing[meta_key] = ext[meta_key]
                existing["_priority"] = source_priority
                existing["_source_name"] = source.name

            # 深度合并版本（所有源的版本都保留）
            for ver in ext.get("versions", []):
                _merge_version(existing, ver, source_priority)
        else:
            ext_entry = {
                "name": ext_name,
                "publisher": ext.get("publisher", {}),
                "description": ext.get("description", ""),
                "abstract": ext.get("abstract", ""),
                "tags": ext.get("tags", []),
                "url": ext.get("url", ""),
                "source": ext.get("source", ""),
                "license": ext.get("license", ""),
                "channels": ext.get("channels", {}),
                "versions": [],
                "_priority": source_priority,
                "_source_name": source.name,
            }
            for ver in ext.get("versions", []):
                _merge_version(ext_entry, ver, source_priority)
            merged_exts[ext_name] = ext_entry

    # 清理内部字段
    merged["extensions"] = [
        {k: v for k, v in e.items() if not k.startswith("_")}
        for e in merged_exts.values()
    ]


def _merge_version(
    ext_entry: dict,
    version_data: dict,
    source_priority: int,
) -> None:
    """深度合并版本到扩展条目中。

    同版本号 + 同 target (arch/os/pgVersion/flavor/build) 由高优先级源胜出。
    同版本号 + 不同 target 全部保留。
    """
    ver_str = version_data.get("version", "")

    # 查找已有版本
    existing_ver = None
    for v in ext_entry.get("versions", []):
        if v.get("version") == ver_str:
            existing_ver = v
            break

    if existing_ver is None:
        # 新版本，直接添加
        ext_entry["versions"].append({
            "version": ver_str,
            "availableFor": list(version_data.get("availableFor", [])),
            "_priority": source_priority,
        })
        return

    # 已有该版本，合并 availableFor
    existing_priority = existing_ver.get("_priority", 999)
    for target in version_data.get("availableFor", []):
        target_key = _target_key(target)

        # 检查是否已有相同 target
        found = False
        for et in existing_ver.get("availableFor", []):
            if _target_key(et) == target_key:
                found = True
                # 高优先级覆盖
                if source_priority <= existing_priority:
                    et.update(target)
                break

        if not found:
            existing_ver.setdefault("availableFor", []).append(target)

    if source_priority <= existing_priority:
        existing_ver["_priority"] = source_priority


def _target_key(target: dict) -> str:
    """构造 target 的唯一标识。"""
    return (
        f"{target.get('arch', '')}_{target.get('os', '')}_"
        f"{target.get('flavor', '')}_{target.get('postgresVersion', '')}_"
        f"{target.get('build', '')}"
    )


async def build_aggregated_index() -> Path | None:
    """执行聚合并写入本地 index.json。

    Returns:
        写入的 index.json 路径，失败返回 None
    """
    try:
        aggregated = await aggregate_indices()

        index_path = settings.repo_dir / INDEX_PATH
        index_path.parent.mkdir(parents=True, exist_ok=True)

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None,
            lambda: _write_json(index_path, aggregated),
        )

        task_logger.info(f"[索引聚合] 已写入: {index_path}")
        return index_path
    except Exception as e:
        task_logger.error(f"[索引聚合] 失败: {e}", exc_info=True)
        return None


def _write_json(path: Path, data: dict) -> None:
    """写入 JSON 文件。"""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)