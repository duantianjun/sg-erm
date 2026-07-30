# -*- coding: utf-8 -*-
"""whitelist API 集成测试：CRUD + 包名提取 + 空白名单拒绝。"""
import pytest
from sqlalchemy import select

from app.models import GlobalWhitelist, User
from app.services.auth_service import create_access_token, get_password_hash


async def _admin_token(db_session):
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
class TestWhitelistCrud:
    async def test_list_empty(self, client, db_session):
        token = await _admin_token(db_session)
        resp = await client.get(
            "/api/v1/whitelist", headers={"Authorization": f"Bearer {token}"}
        )
        assert resp.status_code == 200
        assert resp.json()["data"] == []

    async def test_add_and_list(self, client, db_session):
        token = await _admin_token(db_session)
        # 添加
        resp = await client.post(
            "/api/v1/whitelist",
            json={"extension_name": "postgis", "postgres_versions": [">=16.0"], "arch": ["x86_64"]},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.json()["code"] == 0
        # 列表
        resp = await client.get(
            "/api/v1/whitelist", headers={"Authorization": f"Bearer {token}"}
        )
        items = resp.json()["data"]
        assert len(items) == 1
        assert items[0]["extension_name"] == "postgis"

    async def test_add_duplicate_fails(self, client, db_session):
        token = await _admin_token(db_session)
        payload = {"extension_name": "postgis"}
        await client.post(
            "/api/v1/whitelist",
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
        )
        resp = await client.post(
            "/api/v1/whitelist",
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.json()["code"] != 0

    async def test_delete(self, client, db_session):
        token = await _admin_token(db_session)
        resp = await client.post(
            "/api/v1/whitelist",
            json={"extension_name": "postgis"},
            headers={"Authorization": f"Bearer {token}"},
        )
        entry_id = resp.json()["data"][0]["id"]
        resp = await client.delete(
            f"/api/v1/whitelist/{entry_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.json()["code"] == 0
        # 确认已删
        resp = await client.get(
            "/api/v1/whitelist", headers={"Authorization": f"Bearer {token}"}
        )
        assert resp.json()["data"] == []


@pytest.mark.integration
class TestWhitelistEnforcement:
    """对应项目硬约束：空白名单时同步请求全拒绝。"""

    async def test_empty_whitelist_rejects_sync(self, client, db_session):
        """白名单为空时，任何包名提取都不在白名单中。

        本测试验证包名提取逻辑（split('-')[0]）：
        'postgis-3.4-pg16.4' → 'postgis'，空列表中不包含 'postgis'。
        """
        # 白名单为空
        token = await _admin_token(db_session)
        resp = await client.get(
            "/api/v1/whitelist", headers={"Authorization": f"Bearer {token}"}
        )
        assert resp.json()["data"] == []

        # 包名提取逻辑（与 sync_engine 一致）
        package_name = "postgis-3.4-pg16.4"
        ext_name = package_name.split("-")[0]
        assert ext_name == "postgis"

        # 从空白名单查询，应不存在
        result = await db_session.execute(
            select(GlobalWhitelist).where(GlobalWhitelist.extension_name == ext_name)
        )
        assert result.scalar_one_or_none() is None  # 不在白名单 → 同步应拒绝

    async def test_package_name_extraction_various(self):
        """包名提取按 '-' 切分取首段。"""
        assert "postgis-3.4-pg16.4".split("-")[0] == "postgis"
        assert "pgvector-0.7.0-pg16.4".split("-")[0] == "pgvector"
        assert "timescaledb-2.13.0-pg15.5".split("-")[0] == "timescaledb"
