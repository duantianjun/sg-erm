# -*- coding: utf-8 -*-
"""扩展目录 API 集成测试。"""
import pytest
from sqlalchemy import select

from app.models import Extension, ExtensionBuild, ExtensionVersion, Publisher, User
from app.services.auth_service import create_access_token, get_password_hash


async def _login_as(db_session, username="admin", is_admin=True):
    """创建用户并返回 JWT。"""
    user = User(
        username=username,
        password_hash=get_password_hash("Admin@1234"),
        is_admin=is_admin,
        is_active=True,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    token = create_access_token(
        data={"sub": user.id, "token_version": user.token_version}
    )
    return user, token


async def _create_extension_with_build(db_session, name="postgis"):
    """创建扩展 + 版本 + 构建包。"""
    publisher = Publisher(name="com.ongres")
    db_session.add(publisher)
    await db_session.commit()
    await db_session.refresh(publisher)

    ext = Extension(
        name=name,
        description=f"{name} extension",
        publisher_id=publisher.id,
        license="Apache-2.0",
    )
    db_session.add(ext)
    await db_session.commit()
    await db_session.refresh(ext)

    version = ExtensionVersion(
        extension_id=ext.id,
        version="3.4.0",
        channel="stable",
    )
    db_session.add(version)
    await db_session.commit()
    await db_session.refresh(version)

    build = ExtensionBuild(
        version_id=version.id,
        postgres_version="16.4",
        arch="x86_64",
        os="linux",
        flavor="pg",
        build="6.51",
        package_path=f"com.ongres/x86_64/linux/{name}-3.4-pg16.4.tar",
        package_size=1024000,
        sha256="abc123",
        cached=True,
    )
    db_session.add(build)
    await db_session.commit()
    return ext, version, build


@pytest.mark.integration
class TestExtensionList:
    async def test_list_empty(self, client, db_session):
        """空扩展列表。"""
        _, token = await _login_as(db_session)
        resp = await client.get(
            "/api/v1/extensions", headers={"Authorization": f"Bearer {token}"}
        )
        assert resp.status_code == 200
        assert resp.json()["data"] == []

    async def test_list_with_extensions(self, client, db_session):
        """列表包含扩展。"""
        _, token = await _login_as(db_session)
        await _create_extension_with_build(db_session, "postgis")
        resp = await client.get(
            "/api/v1/extensions", headers={"Authorization": f"Bearer {token}"}
        )
        items = resp.json()["data"]
        assert len(items) == 1
        assert items[0]["name"] == "postgis"
        assert items[0]["version_count"] == 1
        assert items[0]["build_count"] == 1

    async def test_filter_by_publisher(self, client, db_session):
        """按发布者过滤。"""
        _, token = await _login_as(db_session)
        await _create_extension_with_build(db_session, "postgis")
        resp = await client.get(
            "/api/v1/extensions?publisher=com.ongres",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert len(resp.json()["data"]) == 1

    async def test_search_by_keyword(self, client, db_session):
        """关键词搜索。"""
        _, token = await _login_as(db_session)
        await _create_extension_with_build(db_session, "postgis")
        resp = await client.get(
            "/api/v1/extensions?keyword=postgis",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert len(resp.json()["data"]) == 1


@pytest.mark.integration
class TestExtensionDetail:
    async def test_get_detail(self, client, db_session):
        """扩展详情。"""
        _, token = await _login_as(db_session)
        ext, version, build = await _create_extension_with_build(db_session, "postgis")
        resp = await client.get(
            f"/api/v1/extensions/postgis",
            headers={"Authorization": f"Bearer {token}"},
        )
        data = resp.json()["data"][0]
        assert data["name"] == "postgis"
        assert data["publisher"] == "com.ongres"
        assert len(data["versions"]) == 1
        assert data["versions"][0]["version"] == "3.4.0"
        assert len(data["versions"][0]["builds"]) == 1
        assert data["versions"][0]["builds"][0]["postgres_version"] == "16.4"

    async def test_get_nonexistent(self, client, db_session):
        """不存在的扩展。"""
        _, token = await _login_as(db_session)
        resp = await client.get(
            "/api/v1/extensions/nonexistent",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.json()["code"] == 0
        assert resp.json()["data"] == []


@pytest.mark.integration
class TestExtensionAuth:
    async def test_unauthenticated_cannot_access(self, client, db_session):
        """未登录无法访问。"""
        resp = await client.get("/api/v1/extensions")
        assert resp.status_code == 401