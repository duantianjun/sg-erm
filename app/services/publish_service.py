# -*- coding: utf-8 -*-
"""自定义扩展发布服务。

处理完整的发布流程：
1. 接收 .tgz 扩展包和元数据
2. 校验扩展包（检查 .control 文件存在性）
3. 用发布者私钥生成签名（.sha256）
4. 打包为 .tar（包含 .sha256 + .tgz）
5. 写入本地存储
6. 更新本地 index.json
7. 创建/更新数据库记录
"""
import json
import logging
import os
import shutil
import tarfile
import tempfile
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import Extension, ExtensionBuild, ExtensionVersion, Publisher
from app.services.crypto_service import (
    decrypt_private_key,
    get_system_password,
    sign_sha256_file,
)
from app.services.naming import get_local_path, get_package_name

logger = logging.getLogger(__name__)


def _ensure_dir(path: Path) -> None:
    """确保目录存在。"""
    path.mkdir(parents=True, exist_ok=True)


def validate_tgz(tgz_path: str) -> tuple[bool, str]:
    """校验 .tgz 扩展包。

    检查:
    - 文件是否为有效的 tar.gz
    - 是否包含至少一个 .control 文件（PostgreSQL 扩展控制文件）

    Returns:
        (是否有效, 错误信息)
    """
    try:
        with tarfile.open(tgz_path, "r:gz") as tf:
            names = tf.getnames()
            control_files = [n for n in names if n.endswith(".control")]
            if not control_files:
                logger.warning(f"[发布服务] 校验失败：未找到 .control 文件 tgz={tgz_path}")
                return False, "扩展包中未找到 .control 文件"
            logger.debug(f"[发布服务] tgz 校验通过 tgz={tgz_path} control_files={len(control_files)}")
            return True, ""
    except tarfile.TarError as e:
        logger.warning(f"[发布服务] 校验失败：无效的 tar.gz 文件 tgz={tgz_path}: {e}")
        return False, f"无效的 tar.gz 文件: {e}"


def build_tar_package(tgz_path: str, sha256_path: str, dest_path: str) -> None:
    """构建 .tar 包（包含 .sha256 签名文件和 .tgz 扩展包）。"""
    _ensure_dir(Path(dest_path).parent)
    with tarfile.open(dest_path, "w") as tf:
        tf.add(tgz_path, arcname=os.path.basename(tgz_path))
        tf.add(sha256_path, arcname=os.path.basename(sha256_path))


def update_local_index(
    repo_dir: Path,
    ext_name: str,
    publisher_name: str,
    version: str,
    channel: str,
    flavor: str,
    pg_version: str,
    arch: str,
    os_name: str,
    build_num: str | None,
    package_path: str,
    public_key: str | None,
):
    """更新本地 index.json，添加/更新自定义扩展条目。

    如果 publisher 不存在于 index.json 中，会添加 publisher 记录。
    """
    index_path = repo_dir / "v2" / "index.json"

    # 读取现有 index.json，如果不存在则创建空结构
    if index_path.exists():
        with open(index_path, "r", encoding="utf-8") as f:
            index = json.load(f)
    else:
        index = {"extensions": [], "publishers": []}
        _ensure_dir(index_path.parent)

    extensions = index.get("extensions", [])
    publishers = index.get("publishers", [])

    # 确保 publisher 存在于 publishers 列表
    pub_entry = next((p for p in publishers if p.get("id") == publisher_name), None)
    if pub_entry is None:
        pub_entry = {"id": publisher_name, "name": publisher_name}
        if public_key:
            pub_entry["publicKey"] = public_key
        publishers.append(pub_entry)
    elif public_key and pub_entry.get("publicKey") != public_key:
        pub_entry["publicKey"] = public_key

    # 查找或创建扩展条目
    ext_entry = next((e for e in extensions if e.get("name") == ext_name), None)
    if ext_entry is None:
        ext_entry = {
            "name": ext_name,
            "publisher": publisher_name,
            "description": "",
            "versions": [],
        }
        extensions.append(ext_entry)

    # 查找或创建版本条目
    ver_entry = next(
        (v for v in ext_entry["versions"] if v.get("version") == version), None
    )
    if ver_entry is None:
        ver_entry = {"version": version, "availableFor": []}
        ext_entry["versions"].append(ver_entry)

    # 构建 availableFor 条目
    af_entry = {
        "flavor": flavor,
        "postgresVersion": pg_version,
        "arch": arch,
        "build": build_num,
        "os": os_name,
    }

    # 去重：检查是否已存在相同构建
    existing = next(
        (
            a
            for a in ver_entry["availableFor"]
            if a.get("flavor") == flavor
            and a.get("postgresVersion") == pg_version
            and a.get("arch") == arch
            and a.get("os") == os_name
        ),
        None,
    )
    if existing is None:
        ver_entry["availableFor"].append(af_entry)

    # 更新 channels
    channels = ext_entry.get("channels", {})
    if channel not in channels.values():
        channels[channel] = version
        ext_entry["channels"] = channels

    # 写回 index.json
    index["extensions"] = extensions
    index["publishers"] = publishers

    with open(index_path, "w", encoding="utf-8") as f:
        json.dump(index, f, indent=2, ensure_ascii=False)

    logger.info(
        f"[发布服务] index.json 更新完成 ext={ext_name} version={version} "
        f"publisher={publisher_name} arch={arch} os={os_name} pg={pg_version}"
    )


async def publish_extension(
    session: AsyncSession,
    publisher_id: str,
    tgz_path: str,
    ext_name: str,
    version: str,
    flavor: str,
    pg_version: str,
    arch: str,
    os_name: str,
    build_num: str | None,
    channel: str = "stable",
    description: str = "",
    license_str: str = "",
    tags: list | None = None,
) -> dict:
    """发布自定义扩展。

    完整流程：
    1. 校验 .tgz
    2. 获取发布者私钥
    3. 签名 .tgz → 生成 .sha256
    4. 打包 .tar (.sha256 + .tgz)
    5. 写入存储
    6. 更新 index.json
    7. 创建/更新数据库记录

    Args:
        session: 数据库会话
        publisher_id: 发布者 UUID
        tgz_path: 上传的 .tgz 文件路径
        ext_name: 扩展名称
        version: 版本号
        flavor: 风味 (pg/bf)
        pg_version: PostgreSQL 版本
        arch: 架构
        os_name: 操作系统
        build_num: 构建号（可选）
        channel: 通道 (stable/beta/dev)
        description: 描述
        license_str: 许可证
        tags: 标签列表

    Returns:
        {"success": bool, "package_path": str, "error": str}
    """
    repo_dir = settings.repo_dir
    password = get_system_password()

    logger.info(
        f"[发布服务] 开始发布 ext={ext_name} version={version} "
        f"publisher_id={publisher_id} flavor={flavor} pg={pg_version} arch={arch} os={os_name}"
    )

    # 1. 校验 .tgz
    valid, error = validate_tgz(tgz_path)
    if not valid:
        logger.warning(f"[发布服务] 发布中止：tgz 校验失败 ext={ext_name} version={version}: {error}")
        return {"success": False, "package_path": "", "error": error}

    # 2. 获取发布者信息
    result = await session.execute(
        select(Publisher).where(Publisher.id == publisher_id)
    )
    publisher = result.scalar_one_or_none()
    if not publisher:
        logger.warning(f"[发布服务] 发布中止：发布者不存在 publisher_id={publisher_id}")
        return {"success": False, "package_path": "", "error": "发布者不存在"}

    if not publisher.private_key:
        logger.warning(f"[发布服务] 发布中止：发布者无私钥 publisher={publisher.name}")
        return {"success": False, "package_path": "", "error": "发布者没有私钥"}

    # 解密私钥
    try:
        private_key_pem = decrypt_private_key(publisher.private_key, password)
        logger.debug(f"[发布服务] 私钥解密成功 publisher={publisher.name}")
    except Exception as e:
        logger.error(f"[发布服务] 私钥解密失败 publisher={publisher.name}: {e}")
        return {"success": False, "package_path": "", "error": f"私钥解密失败: {e}"}

    # 3. 构建包名和路径
    package_name = get_package_name(ext_name, version, flavor, pg_version, build_num)
    rel_path = get_local_path(publisher.name, arch, os_name, package_name)
    dest_path = repo_dir / rel_path
    logger.debug(f"[发布服务] 包路径规划 package_name={package_name} rel_path={rel_path}")

    # 4. 签名 → 生成 .sha256
    try:
        sha256_b64 = sign_sha256_file(private_key_pem, tgz_path)
        logger.debug(f"[发布服务] 签名完成 tgz={tgz_path}")
    except Exception as e:
        logger.error(f"[发布服务] 签名失败 ext={ext_name} version={version}: {e}")
        return {"success": False, "package_path": "", "error": f"签名失败: {e}"}

    # 5. 在临时目录中创建 .sha256 文件
    with tempfile.NamedTemporaryFile(
        mode="wb", suffix=".sha256", delete=False
    ) as sha256_file:
        sha256_file.write(sha256_b64)
        sha256_path = sha256_file.name

    try:
        # 6. 打包 .tar
        build_tar_package(tgz_path, sha256_path, str(dest_path))
        logger.debug(f"[发布服务] tar 包已构建 path={dest_path}")

        # 7. 计算包大小
        package_size = dest_path.stat().st_size
        logger.debug(f"[发布服务] 包大小 size={package_size} bytes")

        # 8. 更新 index.json
        update_local_index(
            repo_dir=repo_dir,
            ext_name=ext_name,
            publisher_name=publisher.name,
            version=version,
            channel=channel,
            flavor=flavor,
            pg_version=pg_version,
            arch=arch,
            os_name=os_name,
            build_num=build_num,
            package_path=rel_path,
            public_key=publisher.public_key,
        )

        # 9. 创建/更新数据库记录
        # 查找或创建 Extension
        ext_result = await session.execute(
            select(Extension).where(Extension.name == ext_name)
        )
        ext = ext_result.scalar_one_or_none()
        if ext is None:
            ext = Extension(
                name=ext_name,
                publisher_id=publisher_id,
                description=description,
                license=license_str,
                tags=tags or [],
                is_custom=True,
            )
            session.add(ext)
            await session.flush()  # 获取 ext.id
            logger.debug(f"[发布服务] 新建 Extension 记录 ext={ext_name} id={ext.id}")
        else:
            logger.debug(f"[发布服务] 复用 Extension 记录 ext={ext_name} id={ext.id}")

        # 查找或创建 ExtensionVersion
        ver_result = await session.execute(
            select(ExtensionVersion).where(
                ExtensionVersion.extension_id == ext.id,
                ExtensionVersion.version == version,
            )
        )
        ver = ver_result.scalar_one_or_none()
        if ver is None:
            ver = ExtensionVersion(
                extension_id=ext.id,
                version=version,
                channel=channel,
            )
            session.add(ver)
            await session.flush()
            logger.debug(f"[发布服务] 新建 ExtensionVersion ext={ext_name} version={version} id={ver.id}")
        else:
            logger.debug(f"[发布服务] 复用 ExtensionVersion ext={ext_name} version={version} id={ver.id}")

        # 查找或创建 ExtensionBuild
        build_result = await session.execute(
            select(ExtensionBuild).where(
                ExtensionBuild.version_id == ver.id,
                ExtensionBuild.postgres_version == pg_version,
                ExtensionBuild.arch == arch,
                ExtensionBuild.os == os_name,
                ExtensionBuild.flavor == flavor,
            )
        )
        build = build_result.scalar_one_or_none()
        if build is None:
            build = ExtensionBuild(
                version_id=ver.id,
                postgres_version=pg_version,
                arch=arch,
                os=os_name,
                flavor=flavor,
                build=build_num,
                package_path=rel_path,
                package_size=package_size,
                cached=True,
                verified=True,
            )
            session.add(build)
            logger.debug(f"[发布服务] 新建 ExtensionBuild ext={ext_name} version={version} arch={arch} os={os_name}")
        else:
            # 更新现有构建
            build.package_path = rel_path
            build.package_size = package_size
            build.cached = True
            build.verified = True
            logger.debug(f"[发布服务] 更新 ExtensionBuild ext={ext_name} version={version} arch={arch} os={os_name}")

        await session.commit()

        logger.info(
            f"[发布服务] 发布成功 ext={ext_name} version={version} "
            f"publisher={publisher.name} package={package_name} size={package_size}"
        )

        return {
            "success": True,
            "package_path": str(dest_path),
            "error": "",
        }

    except Exception as e:
        logger.exception(f"[发布服务] 发布过程异常 ext={ext_name} version={version}: {e}")
        return {"success": False, "package_path": "", "error": str(e)}

    finally:
        # 清理临时 .sha256 文件
        if os.path.exists(sha256_path):
            os.unlink(sha256_path)
            logger.debug(f"[发布服务] 清理临时 .sha256 文件 path={sha256_path}")


async def create_custom_publisher(
    session: AsyncSession,
    name: str,
    display_name: str | None = None,
) -> Publisher:
    """创建自定义发布者，并生成 RSA 密钥对。

    Returns:
        创建的 Publisher 对象
    """
    from app.services.crypto_service import encrypt_private_key, generate_key_pair

    logger.info(f"[发布服务] 创建自定义发布者 name={name}")

    private_pem, public_pem = generate_key_pair()
    encrypted_private = encrypt_private_key(private_pem, get_system_password())

    publisher = Publisher(
        name=name,
        display_name=display_name or name,
        public_key=public_pem,
        private_key=encrypted_private,
        is_custom=True,
    )
    session.add(publisher)
    await session.commit()
    await session.refresh(publisher)

    logger.info(f"[发布服务] 自定义发布者创建成功 name={name} id={publisher.id}")
    return publisher
