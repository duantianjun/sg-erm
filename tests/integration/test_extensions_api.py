# -*- coding: utf-8 -*-
"""扩展目录 API 集成测试。

覆盖场景：
- 扩展列表（分页 / 关键词 / 发布者过滤 / 统计字段）
- 扩展详情（含版本和构建结构）
- 批量删除扩展（含磁盘文件删除 + 数据库级联）
- 批量删除构建包（含磁盘文件删除）
"""
import pytest
from sqlalchemy import select

from app.models import (
    AuditLog,
    Extension,
    ExtensionBuild,
    ExtensionVersion,
    Publisher,
    User,
)
from app.services.auth_service import create_access_token, get_password_hash


# ─── 辅助函数 ────────────────────────────────────────

async def _admin_token(db_session):
    """创建管理员用户并返回 JWT。"""
    user = User(
        username="admin",
        password_hash=get_password_hash("Admin@1234"),
        is_admin=True,
        is_active=True,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return create_access_token(
        data={"sub": user.id, "token_version": user.token_version}
    )


def _auth_headers(token):
    """构造认证请求头。"""
    return {"Authorization": f"Bearer {token}"}


async def _create_publisher(db_session, name="com.ongres"):
    """创建发布者。"""
    pub = Publisher(name=name, display_name=name)
    db_session.add(pub)
    await db_session.commit()
    await db_session.refresh(pub)
    return pub


async def _create_extension(
    db_session, publisher_id, name="postgis", description=None
):
    """创建扩展（不含版本/构建）。"""
    ext = Extension(
        name=name,
        description=description if description is not None else f"{name} extension",
        publisher_id=publisher_id,
        license="Apache-2.0",
    )
    db_session.add(ext)
    await db_session.commit()
    await db_session.refresh(ext)
    return ext


async def _create_version(
    db_session, extension_id, version="3.4.0", channel="stable"
):
    """创建扩展版本。"""
    ver = ExtensionVersion(
        extension_id=extension_id,
        version=version,
        channel=channel,
    )
    db_session.add(ver)
    await db_session.commit()
    await db_session.refresh(ver)
    return ver


async def _create_build(
    db_session,
    version_id,
    package_path,
    package_size=1024000,
    cached=True,
    postgres_version="16.4",
):
    """创建构建包记录。"""
    build = ExtensionBuild(
        version_id=version_id,
        postgres_version=postgres_version,
        arch="x86_64",
        os="linux",
        flavor="pg",
        build="6.51",
        package_path=package_path,
        package_size=package_size,
        sha256="abc123def456",
        cached=cached,
    )
    db_session.add(build)
    await db_session.commit()
    await db_session.refresh(build)
    return build


async def _create_full_chain(
    db_session,
    name="postgis",
    publisher_name="com.ongres",
    package_path=None,
    package_size=1024000,
    cached=True,
):
    """创建 Publisher → Extension → Version → Build 完整链路。"""
    pub = await _create_publisher(db_session, publisher_name)
    ext = await _create_extension(db_session, pub.id, name)
    ver = await _create_version(db_session, ext.id)
    path = package_path or f"{publisher_name}/x86_64/linux/{name}-3.4-pg16.4.tar"
    build = await _create_build(
        db_session,
        ver.id,
        path,
        package_size=package_size,
        cached=cached,
    )
    return ext, ver, build, pub


def _create_package_file(repo_dir, package_path, content=b"fake-package-content"):
    """在 repo_dir 下创建磁盘包文件（含父目录），返回 Path。"""
    file_path = repo_dir / package_path
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_bytes(content)
    return file_path


# ─── 扩展列表 ────────────────────────────────────────

@pytest.mark.integration
class TestListExtensions:
    """扩展列表测试。"""

    async def test_unauthenticated_returns_401(self, client, db_session):
        """无认证访问 → 401。"""
        resp = await client.get("/api/v1/extensions")
        assert resp.status_code == 401

    async def test_list_empty(self, client, db_session):
        """空数据库 → 200, 空列表。"""
        token = await _admin_token(db_session)
        resp = await client.get(
            "/api/v1/extensions", headers=_auth_headers(token)
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == 0
        assert body["data"] == []
        assert body["count"] == 0

    async def test_list_with_statistics(self, client, db_session):
        """有扩展 → 验证 version_count/build_count/cached_build_count/total_size。"""
        token = await _admin_token(db_session)
        await _create_full_chain(
            db_session, "postgis", package_size=1024000, cached=True
        )
        resp = await client.get(
            "/api/v1/extensions", headers=_auth_headers(token)
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 1
        item = body["data"][0]
        assert item["name"] == "postgis"
        assert item["publisher"] == "com.ongres"
        assert item["version_count"] == 1
        assert item["build_count"] == 1
        assert item["cached_build_count"] == 1
        assert item["total_size"] == 1024000

    async def test_list_keyword_search(self, client, db_session):
        """关键词搜索 → 过滤结果。"""
        token = await _admin_token(db_session)
        pub = await _create_publisher(db_session)
        await _create_extension(db_session, pub.id, "postgis", "PostGIS extension")
        await _create_extension(db_session, pub.id, "pgaudit", "Audit logging")
        resp = await client.get(
            "/api/v1/extensions?keyword=postgis", headers=_auth_headers(token)
        )
        body = resp.json()
        assert body["count"] == 1
        assert body["data"][0]["name"] == "postgis"

    async def test_list_keyword_search_by_description(self, client, db_session):
        """关键词匹配描述字段。"""
        token = await _admin_token(db_session)
        pub = await _create_publisher(db_session)
        await _create_extension(db_session, pub.id, "postgis", "geometry types")
        await _create_extension(db_session, pub.id, "pgaudit", "audit logging")
        resp = await client.get(
            "/api/v1/extensions?keyword=geometry", headers=_auth_headers(token)
        )
        body = resp.json()
        assert body["count"] == 1
        assert body["data"][0]["name"] == "postgis"

    async def test_list_filter_by_publisher(self, client, db_session):
        """按发布者过滤 → 过滤结果。"""
        token = await _admin_token(db_session)
        pub_a = await _create_publisher(db_session, "com.a")
        pub_b = await _create_publisher(db_session, "com.b")
        await _create_extension(db_session, pub_a.id, "ext_a")
        await _create_extension(db_session, pub_b.id, "ext_b")
        resp = await client.get(
            "/api/v1/extensions?publisher=com.a", headers=_auth_headers(token)
        )
        body = resp.json()
        assert body["count"] == 1
        assert body["data"][0]["name"] == "ext_a"
        assert body["data"][0]["publisher"] == "com.a"

    async def test_list_pagination(self, client, db_session):
        """分页 → page/limit。"""
        token = await _admin_token(db_session)
        pub = await _create_publisher(db_session)
        for i in range(5):
            await _create_extension(db_session, pub.id, f"ext{i}")

        # 第一页 limit=2 → 2 条
        resp = await client.get(
            "/api/v1/extensions?page=1&limit=2", headers=_auth_headers(token)
        )
        body = resp.json()
        assert body["count"] == 5
        assert len(body["data"]) == 2

        # 第二页 → 2 条
        resp = await client.get(
            "/api/v1/extensions?page=2&limit=2", headers=_auth_headers(token)
        )
        body = resp.json()
        assert body["count"] == 5
        assert len(body["data"]) == 2

        # 第三页 → 仅剩 1 条
        resp = await client.get(
            "/api/v1/extensions?page=3&limit=2", headers=_auth_headers(token)
        )
        body = resp.json()
        assert body["count"] == 5
        assert len(body["data"]) == 1


# ─── 扩展详情 ────────────────────────────────────────

@pytest.mark.integration
class TestGetExtension:
    """扩展详情测试。"""

    async def test_get_nonexistent(self, client, db_session):
        """不存在的扩展名 → 200, code=0, count=0, data=[]。"""
        token = await _admin_token(db_session)
        resp = await client.get(
            "/api/v1/extensions/nonexistent", headers=_auth_headers(token)
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == 0
        assert body["count"] == 0
        assert body["data"] == []

    async def test_get_detail_with_versions_and_builds(self, client, db_session):
        """存在的扩展 → 验证 versions 列表和 builds 列表结构。"""
        token = await _admin_token(db_session)
        ext, ver, build, pub = await _create_full_chain(db_session, "postgis")
        resp = await client.get(
            "/api/v1/extensions/postgis", headers=_auth_headers(token)
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == 0
        assert body["count"] == 1
        data = body["data"][0]
        # 基本字段
        assert data["name"] == "postgis"
        assert data["publisher"] == "com.ongres"
        assert data["license"] == "Apache-2.0"
        assert data["is_custom"] is False
        # 版本列表
        assert len(data["versions"]) == 1
        ver_data = data["versions"][0]
        assert ver_data["version"] == "3.4.0"
        assert ver_data["channel"] == "stable"
        # 构建列表
        assert len(ver_data["builds"]) == 1
        build_data = ver_data["builds"][0]
        assert build_data["build_id"] == build.id
        assert build_data["postgres_version"] == "16.4"
        assert build_data["arch"] == "x86_64"
        assert build_data["os"] == "linux"
        assert build_data["flavor"] == "pg"
        assert build_data["build"] == "6.51"
        assert build_data["package_path"] == build.package_path
        assert build_data["package_size"] == 1024000
        assert build_data["sha256"] == "abc123def456"
        assert build_data["cached"] is True
        assert build_data["verified"] is False


# ─── 批量删除扩展 ────────────────────────────────────

@pytest.mark.integration
class TestBatchDeleteExtensions:
    """批量删除扩展测试。"""

    async def test_unauthenticated_returns_401(self, client, db_session):
        """无认证 → 401。"""
        resp = await client.request(
            "DELETE", "/api/v1/extensions/batch", json={"ids": []}
        )
        assert resp.status_code == 401

    async def test_delete_empty_list(self, client, db_session):
        """空 ids 列表 → 200, deleted=0。"""
        token = await _admin_token(db_session)
        resp = await client.request(
            "DELETE",
            "/api/v1/extensions/batch",
            json={"ids": []},
            headers=_auth_headers(token),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == 0
        assert body["data"][0]["deleted"] == 0
        assert body["data"][0]["failed"] == 0

    async def test_delete_extensions_success(
        self, client, db_session, repo_dir, patch_publish_settings
    ):
        """正常删除扩展 → deleted=N, 磁盘文件被删除, 数据库记录被级联删除。"""
        token = await _admin_token(db_session)
        ext, ver, build, pub = await _create_full_chain(db_session, "postgis")
        # 创建磁盘包文件
        pkg_file = _create_package_file(repo_dir, build.package_path)
        assert pkg_file.exists()

        resp = await client.request(
            "DELETE",
            "/api/v1/extensions/batch",
            json={"ids": [ext.id]},
            headers=_auth_headers(token),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == 0
        assert body["data"][0]["deleted"] == 1
        assert body["data"][0]["failed"] == 0
        # 磁盘文件已删除
        assert not pkg_file.exists()
        # 扩展记录已删除
        result = await db_session.execute(
            select(Extension).where(Extension.id == ext.id)
        )
        assert result.scalar_one_or_none() is None
        # 审计日志已记录
        audit_result = await db_session.execute(
            select(AuditLog).where(AuditLog.action == "extension_batch_delete")
        )
        assert audit_result.scalar_one_or_none() is not None

    async def test_delete_nonexistent_id(self, client, db_session):
        """不存在的扩展 ID → failed+=1。"""
        token = await _admin_token(db_session)
        resp = await client.request(
            "DELETE",
            "/api/v1/extensions/batch",
            json={"ids": ["nonexistent-ext-id"]},
            headers=_auth_headers(token),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"][0]["deleted"] == 0
        assert body["data"][0]["failed"] == 1


# ─── 批量删除构建包 ──────────────────────────────────

@pytest.mark.integration
class TestBatchDeleteBuilds:
    """批量删除构建包测试。"""

    async def test_unauthenticated_returns_401(self, client, db_session):
        """无认证 → 401。"""
        resp = await client.request(
            "DELETE",
            "/api/v1/extensions/postgis/builds/batch",
            json={"build_ids": []},
        )
        assert resp.status_code == 401

    async def test_delete_builds_success(
        self, client, db_session, repo_dir, patch_publish_settings
    ):
        """正常删除构建包 → deleted=N, 磁盘文件被删除。"""
        token = await _admin_token(db_session)
        ext, ver, build, pub = await _create_full_chain(db_session, "postgis")
        # 创建磁盘包文件
        pkg_file = _create_package_file(repo_dir, build.package_path)
        assert pkg_file.exists()

        resp = await client.request(
            "DELETE",
            "/api/v1/extensions/postgis/builds/batch",
            json={"build_ids": [build.id]},
            headers=_auth_headers(token),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == 0
        assert body["data"][0]["deleted"] == 1
        assert body["data"][0]["failed"] == 0
        # 磁盘文件已删除
        assert not pkg_file.exists()
        # 构建记录已删除
        result = await db_session.execute(
            select(ExtensionBuild).where(ExtensionBuild.id == build.id)
        )
        assert result.scalar_one_or_none() is None
        # 审计日志已记录
        audit_result = await db_session.execute(
            select(AuditLog).where(
                AuditLog.action == "extension_build_batch_delete"
            )
        )
        assert audit_result.scalar_one_or_none() is not None

    async def test_delete_nonexistent_build_id(self, client, db_session):
        """不存在的 build_id → failed+=1。"""
        token = await _admin_token(db_session)
        resp = await client.request(
            "DELETE",
            "/api/v1/extensions/postgis/builds/batch",
            json={"build_ids": ["nonexistent-build-id"]},
            headers=_auth_headers(token),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"][0]["deleted"] == 0
        assert body["data"][0]["failed"] == 1
