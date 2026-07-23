# -*- coding: utf-8 -*-
"""StackGres 扩展包命名工具。

从 sync-stackgres-repo.py 迁移的核心函数，与 StackGres Java 端的
ExtensionUtil.getExtensionPackageName() 和 getExtensionPackageUri() 保持一致。

包名格式: {name}-{version}-{flavor}{pgVersion}[-build-{build}]
URL 格式: {repositoryUri}/{publisher}/{arch}/{os}/{packageName}.tar
index.json 路径: {repositoryUri}/v2/index.json
"""

# index.json 相对路径
INDEX_PATH = "v2/index.json"

# 默认值
DEFAULT_ARCH = "x86_64"
DEFAULT_OS = "linux"
DEFAULT_PUBLISHER = "com.ongres"


def get_package_name(
    extension_name: str,
    version: str,
    flavor: str,
    pg_version: str,
    build: str | None = None,
) -> str:
    """构造扩展包名，与 ExtensionUtil.getExtensionPackageName() 一致。

    示例:
        get_package_name("postgis", "3.4", "pg", "16.4") -> "postgis-3.4-pg16.4"
        get_package_name("postgis", "3.4", "pg", "16.4", "6.51") -> "postgis-3.4-pg16.4-build-6.51"
    """
    name = f"{extension_name}-{version}-{flavor}{pg_version}"
    if build:
        name += f"-build-{build}"
    return name


def get_package_url(
    repo_url: str,
    publisher: str,
    arch: str,
    os_name: str,
    package_name: str,
) -> str:
    """构造扩展包下载 URL，与 ExtensionUtil.getExtensionPackageUri() 一致。

    示例:
        get_package_url("https://.../repository", "com.ongres", "x86_64", "linux", "postgis-3.4-pg16.4")
        -> "https://.../repository/com.ongres/x86_64/linux/postgis-3.4-pg16.4.tar"
    """
    return f"{repo_url.rstrip('/')}/{publisher}/{arch}/{os_name}/{package_name}.tar"


def get_index_url(repo_url: str) -> str:
    """构造 index.json URL，与 ExtensionUtil.getIndexUri() 一致。"""
    return f"{repo_url.rstrip('/')}/{INDEX_PATH}"


def get_flavor_prefix(flavor: str | None) -> str:
    """获取风味前缀。pg -> pg, bf -> bf。"""
    if flavor is None or flavor == "pg":
        return "pg"
    elif flavor == "bf":
        return "bf"
    return flavor


def get_arch(arch: str | None) -> str:
    """获取架构，None 时返回默认值。"""
    return arch if arch is not None else DEFAULT_ARCH


def get_os(os_name: str | None) -> str:
    """获取操作系统，None 时返回默认值。"""
    return os_name if os_name is not None else DEFAULT_OS


def get_publisher_name(publisher: str | None) -> str:
    """获取发布者名称，None 时返回默认值。"""
    return publisher if publisher is not None else DEFAULT_PUBLISHER


def get_local_path(publisher: str, arch: str, os_name: str, package_name: str) -> str:
    """构造本地存储相对路径。

    示例:
        get_local_path("com.ongres", "x86_64", "linux", "postgis-3.4-pg16.4")
        -> "com.ongres/x86_64/linux/postgis-3.4-pg16.4.tar"
    """
    return f"{publisher}/{arch}/{os_name}/{package_name}.tar"


def validate_path_segment(segment: str) -> bool:
    """验证路径段是否安全（不包含路径遍历字符）。

    Args:
        segment: 路径段（如 publisher, arch, os_name, package_name）

    Returns:
        True 表示安全，False 表示包含危险字符
    """
    if not segment:
        return False
    if ".." in segment or "/" in segment or "\\" in segment:
        return False
    return True


def parse_package_name(package_name: str) -> dict:
    """从包名解析扩展信息。

    包名格式: {name}-{version}-{flavor}{pgVersion}[-build-{build}]

    Args:
        package_name: 包名（如 postgis-3.4-pg16.4 或 postgis-3.4-pg16.4-build-6.51）

    Returns:
        解析结果字典，包含 name, version, flavor, postgres_version, build
    """
    parts = package_name.split("-")
    if len(parts) < 3:
        return None

    result = {
        "name": parts[0],
        "version": parts[1],
    }

    # 解析 flavor + pgVersion（如 pg16.4）
    third_part = parts[2]
    if third_part.startswith("pg"):
        result["flavor"] = "pg"
        result["postgres_version"] = third_part[2:]
    elif third_part.startswith("bf"):
        result["flavor"] = "bf"
        result["postgres_version"] = third_part[2:]
    else:
        result["flavor"] = "pg"
        result["postgres_version"] = third_part

    # 解析 build（如 -build-6.51）
    if len(parts) >= 5 and parts[3] == "build":
        result["build"] = parts[4]
    else:
        result["build"] = None

    return result
