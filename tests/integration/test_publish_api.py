# -*- coding: utf-8 -*-
"""自定义扩展发布 API 集成测试。"""
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from app.models import Extension, Publisher, User
from app.services.auth_service import create_access_token, get_password_hash


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
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.integration
class TestPublisherList:
    async def test_list_empty(self, client, db_session):
        """空列表。"""
        token = await _admin_token(db_session)
        resp = await client.get(
            "/api/v1/publish/publishers", headers=_auth_headers(token)
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == 0
        assert body["data"] == []

    async def test_list_with_custom_publishers(self, client, db_session):
        """列表仅返回自定义发布者（系统发布者不出现）。"""
        token = await _admin_token(db_session)
        custom = Publisher(
            name="com.example",
            display_name="Example",
            public_key="-----BEGIN PUBLIC KEY-----\nfake\n-----END PUBLIC KEY-----",
            is_custom=True,
        )
        system = Publisher(
            name="com.ongres",
            display_name="OnGres",
            is_custom=False,
        )
        db_session.add_all([custom, system])
        await db_session.commit()

        resp = await client.get(
            "/api/v1/publish/publishers", headers=_auth_headers(token)
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == 0
        assert body["count"] == 1
        item = body["data"][0]
        assert item["name"] == "com.example"
        assert item["is_custom"] is True
        assert item["public_key"].startswith("-----BEGIN PUBLIC KEY-----")


@pytest.mark.integration
class TestPublisherCreate:
    async def test_create_success(self, client, db_session):
        """创建自定义发布者成功（mock RSA 密钥生成）。"""
        token = await _admin_token(db_session)
        fake_publisher = Publisher(
            id="pub-001",
            name="com.newpublisher",
            display_name="New Publisher",
            public_key="-----BEGIN PUBLIC KEY-----\nfake-key\n-----END PUBLIC KEY-----",
            is_custom=True,
        )
        with patch(
            "app.api.publish.create_custom_publisher",
            new_callable=AsyncMock,
            return_value=fake_publisher,
        ):
            resp = await client.post(
                "/api/v1/publish/publishers",
                json={"name": "com.newpublisher", "display_name": "New Publisher"},
                headers=_auth_headers(token),
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == 0
        data = body["data"][0]
        assert data["id"] == "pub-001"
        assert data["name"] == "com.newpublisher"
        assert data["display_name"] == "New Publisher"
        assert data["public_key"].startswith("-----BEGIN PUBLIC KEY-----")

    async def test_create_duplicate_name(self, client, db_session):
        """重复名称返回错误。"""
        token = await _admin_token(db_session)
        existing = Publisher(
            name="com.duplicate",
            display_name="Duplicate",
            is_custom=True,
        )
        db_session.add(existing)
        await db_session.commit()

        resp = await client.post(
            "/api/v1/publish/publishers",
            json={"name": "com.duplicate", "display_name": "Dup"},
            headers=_auth_headers(token),
        )
        body = resp.json()
        assert body["code"] != 0
        assert "已存在" in body["msg"]


@pytest.mark.integration
class TestPublisherDelete:
    async def test_delete_custom_publisher(self, client, db_session):
        """删除自定义发布者成功。"""
        token = await _admin_token(db_session)
        pub = Publisher(
            name="com.todelete",
            display_name="To Delete",
            is_custom=True,
        )
        db_session.add(pub)
        await db_session.commit()
        await db_session.refresh(pub)

        resp = await client.delete(
            f"/api/v1/publish/publishers/{pub.id}",
            headers=_auth_headers(token),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == 0
        assert body["data"][0]["id"] == pub.id
        # 验证已从数据库删除
        result = await db_session.execute(
            select(Publisher).where(Publisher.id == pub.id)
        )
        assert result.scalar_one_or_none() is None

    async def test_delete_nonexistent(self, client, db_session):
        """删除不存在的发布者返回 404。"""
        token = await _admin_token(db_session)
        resp = await client.delete(
            "/api/v1/publish/publishers/nonexistent-id",
            headers=_auth_headers(token),
        )
        assert resp.status_code == 404

    async def test_delete_system_publisher_fails(self, client, db_session):
        """不能删除系统发布者（is_custom=False）。"""
        token = await _admin_token(db_session)
        pub = Publisher(
            name="com.ongres",
            display_name="OnGres",
            is_custom=False,
        )
        db_session.add(pub)
        await db_session.commit()
        await db_session.refresh(pub)

        resp = await client.delete(
            f"/api/v1/publish/publishers/{pub.id}",
            headers=_auth_headers(token),
        )
        body = resp.json()
        assert body["code"] != 0
        assert "系统发布者" in body["msg"]
        # 验证未被删除
        result = await db_session.execute(
            select(Publisher).where(Publisher.id == pub.id)
        )
        assert result.scalar_one_or_none() is not None


@pytest.mark.integration
class TestExtensionUpload:
    async def test_upload_success(self, client, db_session, patch_publish_settings):
        """上传 .tgz 发布成功（mock publish_extension 返回 success=True）。"""
        token = await _admin_token(db_session)
        files = {
            "tgz_file": ("test.tgz", b"fake-tgz-content", "application/gzip"),
        }
        data = {
            "publisher_id": "pub-001",
            "ext_name": "myext",
            "version": "1.0.0",
            "pg_version": "16.4",
        }
        with patch(
            "app.api.publish.publish_extension",
            new_callable=AsyncMock,
            return_value={
                "success": True,
                "package_path": "/repo/custom/pub-001/myext-1.0.0.tar",
                "error": "",
            },
        ):
            resp = await client.post(
                "/api/v1/publish/upload",
                files=files,
                data=data,
                headers=_auth_headers(token),
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == 0
        d = body["data"][0]
        assert d["ext_name"] == "myext"
        assert d["version"] == "1.0.0"
        assert d["publisher"] == "pub-001"
        assert d["package_path"].endswith(".tar")

    async def test_upload_non_tgz(self, client, db_session):
        """非 .tgz 文件返回错误。"""
        token = await _admin_token(db_session)
        files = {
            "tgz_file": ("test.zip", b"fake-content", "application/zip"),
        }
        data = {
            "publisher_id": "pub-001",
            "ext_name": "myext",
            "version": "1.0.0",
            "pg_version": "16.4",
        }
        resp = await client.post(
            "/api/v1/publish/upload",
            files=files,
            data=data,
            headers=_auth_headers(token),
        )
        body = resp.json()
        assert body["code"] != 0
        assert ".tgz" in body["msg"]

    async def test_upload_publish_failure(
        self, client, db_session, patch_publish_settings
    ):
        """publish_extension 失败（success=False）返回错误。"""
        token = await _admin_token(db_session)
        files = {
            "tgz_file": ("bad.tgz", b"invalid-content", "application/gzip"),
        }
        data = {
            "publisher_id": "pub-001",
            "ext_name": "myext",
            "version": "1.0.0",
            "pg_version": "16.4",
        }
        with patch(
            "app.api.publish.publish_extension",
            new_callable=AsyncMock,
            return_value={
                "success": False,
                "package_path": "",
                "error": "tgz 校验失败：缺少 .control 文件",
            },
        ):
            resp = await client.post(
                "/api/v1/publish/upload",
                files=files,
                data=data,
                headers=_auth_headers(token),
            )
        body = resp.json()
        assert body["code"] != 0
        assert "校验失败" in body["msg"]


@pytest.mark.integration
class TestPublishedExtensionsList:
    async def test_list_empty(self, client, db_session):
        """空列表。"""
        token = await _admin_token(db_session)
        resp = await client.get(
            "/api/v1/publish/extensions", headers=_auth_headers(token)
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == 0
        assert body["data"] == []
        assert body["count"] == 0

    async def test_list_with_extensions(self, client, db_session):
        """列表返回已发布的自定义扩展。"""
        token = await _admin_token(db_session)
        pub = Publisher(
            name="com.example",
            display_name="Example",
            is_custom=True,
        )
        db_session.add(pub)
        await db_session.commit()
        await db_session.refresh(pub)

        ext = Extension(
            name="myext",
            description="An extension",
            publisher_id=pub.id,
            is_custom=True,
            license="PostgreSQL",
        )
        db_session.add(ext)
        await db_session.commit()

        resp = await client.get(
            "/api/v1/publish/extensions", headers=_auth_headers(token)
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == 0
        assert body["count"] == 1
        item = body["data"][0]
        assert item["name"] == "myext"
        assert item["publisher"] == "com.example"
        assert item["publisher_id"] == pub.id
        assert item["license"] == "PostgreSQL"
        assert item["version_count"] == 0
        assert item["build_count"] == 0

    async def test_list_filter_by_publisher(self, client, db_session):
        """按 publisher_id 过滤只返回该发布者的扩展。"""
        token = await _admin_token(db_session)
        pub_a = Publisher(name="com.a", display_name="A", is_custom=True)
        pub_b = Publisher(name="com.b", display_name="B", is_custom=True)
        db_session.add_all([pub_a, pub_b])
        await db_session.commit()
        await db_session.refresh(pub_a)
        await db_session.refresh(pub_b)

        ext_a = Extension(name="ext_a", publisher_id=pub_a.id, is_custom=True)
        ext_b = Extension(name="ext_b", publisher_id=pub_b.id, is_custom=True)
        db_session.add_all([ext_a, ext_b])
        await db_session.commit()

        resp = await client.get(
            f"/api/v1/publish/extensions?publisher_id={pub_a.id}",
            headers=_auth_headers(token),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 1
        assert body["data"][0]["name"] == "ext_a"
        assert body["data"][0]["publisher_id"] == pub_a.id

    async def test_list_pagination(self, client, db_session):
        """分页参数 page/limit。"""
        token = await _admin_token(db_session)
        pub = Publisher(name="com.page", display_name="Page", is_custom=True)
        db_session.add(pub)
        await db_session.commit()
        await db_session.refresh(pub)

        for i in range(5):
            db_session.add(
                Extension(name=f"ext{i}", publisher_id=pub.id, is_custom=True)
            )
        await db_session.commit()

        # 第一页 limit=2
        resp = await client.get(
            "/api/v1/publish/extensions?page=1&limit=2",
            headers=_auth_headers(token),
        )
        body = resp.json()
        assert body["count"] == 5  # 总数
        assert len(body["data"]) == 2

        # 第二页
        resp = await client.get(
            "/api/v1/publish/extensions?page=2&limit=2",
            headers=_auth_headers(token),
        )
        body = resp.json()
        assert body["count"] == 5
        assert len(body["data"]) == 2

        # 第三页（仅剩 1 个）
        resp = await client.get(
            "/api/v1/publish/extensions?page=3&limit=2",
            headers=_auth_headers(token),
        )
        body = resp.json()
        assert body["count"] == 5
        assert len(body["data"]) == 1
