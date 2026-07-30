# -*- coding: utf-8 -*-
"""API Token 管理 API 集成测试。"""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.models import ApiToken, User
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


@pytest.mark.integration
class TestTokenList:
    async def test_list_empty(self, client, db_session):
        """空列表。"""
        token = await _admin_token(db_session)
        resp = await client.get(
            "/api/v1/tokens", headers={"Authorization": f"Bearer {token}"}
        )
        assert resp.status_code == 200
        assert resp.json()["data"] == []

    async def test_list_with_tokens(self, client, db_session):
        """列表包含已创建的 Token。"""
        token = await _admin_token(db_session)
        # 创建一个 Token
        await client.post(
            "/api/v1/tokens",
            json={"name": "test-token", "type": "read"},
            headers={"Authorization": f"Bearer {token}"},
        )
        resp = await client.get(
            "/api/v1/tokens", headers={"Authorization": f"Bearer {token}"}
        )
        assert resp.status_code == 200
        items = resp.json()["data"]
        assert len(items) == 1
        assert items[0]["name"] == "test-token"


@pytest.mark.integration
class TestTokenCreate:
    async def test_create_returns_plain_token(self, client, db_session):
        """创建时返回明文 Token（只返回一次）。"""
        token = await _admin_token(db_session)
        resp = await client.post(
            "/api/v1/tokens",
            json={"name": "my-token", "type": "read"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.json()["code"] == 0
        data = resp.json()["data"][0]
        assert data["name"] == "my-token"
        assert data["token"].startswith("sgerm_")  # 明文只返回一次
        assert data["type"] == "read"

    async def test_create_with_expiry(self, client, db_session):
        """创建带过期时间的 Token。"""
        token = await _admin_token(db_session)
        resp = await client.post(
            "/api/v1/tokens",
            json={"name": "expiring-token", "expires_days": 7},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.json()["code"] == 0
        expires_at = resp.json()["data"][0]["expires_at"]
        assert expires_at is not None
        # 验证数据库中 prefix 索引存在
        result = await db_session.execute(
            select(ApiToken).where(ApiToken.name == "expiring-token")
        )
        db_token = result.scalar_one()
        assert len(db_token.token_prefix) == 8


@pytest.mark.integration
class TestTokenDelete:
    async def test_delete_existing(self, client, db_session):
        """删除存在的 Token。"""
        token = await _admin_token(db_session)
        resp = await client.post(
            "/api/v1/tokens",
            json={"name": "to-delete"},
            headers={"Authorization": f"Bearer {token}"},
        )
        token_id = resp.json()["data"][0]["id"]
        resp = await client.delete(
            f"/api/v1/tokens/{token_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.json()["code"] == 0
        # 验证已删除
        result = await db_session.execute(
            select(ApiToken).where(ApiToken.id == token_id)
        )
        assert result.scalar_one_or_none() is None

    async def test_delete_nonexistent(self, client, db_session):
        """删除不存在的 Token 返回 404。"""
        token = await _admin_token(db_session)
        resp = await client.delete(
            "/api/v1/tokens/nonexistent-id",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 404


@pytest.mark.integration
class TestTokenAuth:
    async def test_non_admin_cannot_access(self, client, db_session):
        """非管理员无权访问。"""
        user = User(
            username="normal",
            password_hash=get_password_hash("Pass@1234"),
            is_admin=False,
            is_active=True,
        )
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)
        token = create_access_token(
            data={"sub": user.id, "token_version": user.token_version}
        )
        resp = await client.get(
            "/api/v1/tokens", headers={"Authorization": f"Bearer {token}"}
        )
        assert resp.status_code == 403