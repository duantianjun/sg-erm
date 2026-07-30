# -*- coding: utf-8 -*-
"""auth API 集成测试：login / refresh / change-password / users CRUD。"""
import pytest
from sqlalchemy import select

from app.models import User
from app.services.auth_service import create_access_token, create_refresh_token, get_password_hash


async def _create_user(db_session, username="admin", password="Admin@1234", is_admin=True):
    user = User(
        username=username,
        password_hash=get_password_hash(password),
        is_admin=is_admin,
        is_active=True,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest.mark.integration
class TestLogin:
    async def test_login_success(self, client, db_session):
        user = await _create_user(db_session)
        resp = await client.post(
            "/api/v1/auth/login",
            data={"username": "admin", "password": "Admin@1234"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == 0
        assert body["data"][0]["access_token"]
        assert body["data"][0]["refresh_token"]
        assert body["data"][0]["user"]["username"] == "admin"
        # last_login 应被更新
        await db_session.refresh(user)
        assert user.last_login is not None

    async def test_login_wrong_password(self, client, db_session):
        await _create_user(db_session)
        resp = await client.post(
            "/api/v1/auth/login",
            data={"username": "admin", "password": "wrong"},
        )
        assert resp.status_code == 401

    async def test_login_nonexistent_user(self, client):
        resp = await client.post(
            "/api/v1/auth/login",
            data={"username": "ghost", "password": "whatever"},
        )
        assert resp.status_code == 401


@pytest.mark.integration
class TestRefresh:
    async def test_refresh_with_refresh_token_succeeds(self, client, db_session):
        user = await _create_user(db_session)
        refresh = create_refresh_token(
            data={"sub": user.id, "token_version": user.token_version}
        )
        resp = await client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": refresh},
        )
        assert resp.status_code == 200
        assert resp.json()["data"][0]["access_token"]

    async def test_refresh_with_access_token_fails(self, client, db_session):
        user = await _create_user(db_session)
        access = create_access_token(
            data={"sub": user.id, "token_version": user.token_version}
        )
        resp = await client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": access},
        )
        assert resp.status_code == 401
        assert "无效" in resp.json().get("detail", "") or resp.json()["detail"]


@pytest.mark.integration
class TestChangePassword:
    async def test_change_password_invalidates_old_jwt(self, client, db_session):
        user = await _create_user(db_session)
        access = create_access_token(
            data={"sub": user.id, "token_version": user.token_version}
        )
        resp = await client.post(
            "/api/v1/auth/change-password",
            json={"old_password": "Admin@1234", "new_password": "NewPass@5678"},
            headers={"Authorization": f"Bearer {access}"},
        )
        assert resp.status_code == 200
        # token_version 应递增
        await db_session.refresh(user)
        assert user.token_version == 2
        # 旧 JWT（token_version=1）应失效
        resp_me = await client.get(
            "/api/v1/auth/me", headers={"Authorization": f"Bearer {access}"}
        )
        assert resp_me.status_code == 401

    async def test_change_password_wrong_old(self, client, db_session):
        user = await _create_user(db_session)
        access = create_access_token(
            data={"sub": user.id, "token_version": user.token_version}
        )
        resp = await client.post(
            "/api/v1/auth/change-password",
            json={"old_password": "wrong", "new_password": "NewPass@5678"},
            headers={"Authorization": f"Bearer {access}"},
        )
        assert resp.json()["code"] != 0  # error_response


@pytest.mark.integration
class TestUsersCrud:
    async def test_create_user_requires_admin(self, client, db_session):
        # 普通用户不能创建用户
        normal = await _create_user(db_session, username="normal", is_admin=False)
        access = create_access_token(
            data={"sub": normal.id, "token_version": normal.token_version}
        )
        resp = await client.post(
            "/api/v1/auth/users",
            json={"username": "newbie", "password": "Pass@1234"},
            headers={"Authorization": f"Bearer {access}"},
        )
        assert resp.status_code == 403

    async def test_admin_can_list_users(self, client, db_session):
        admin = await _create_user(db_session)
        access = create_access_token(
            data={"sub": admin.id, "token_version": admin.token_version}
        )
        resp = await client.get(
            "/api/v1/auth/users", headers={"Authorization": f"Bearer {access}"}
        )
        assert resp.status_code == 200
        names = [u["username"] for u in resp.json()["data"]]
        assert "admin" in names
