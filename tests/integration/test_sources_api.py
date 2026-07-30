# -*- coding: utf-8 -*-
"""仓库源管理 API 集成测试。"""
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from app.models import RepositorySource, User
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
class TestSourceList:
    async def test_list_empty(self, client, db_session):
        """空列表。"""
        token = await _admin_token(db_session)
        resp = await client.get(
            "/api/v1/sources", headers={"Authorization": f"Bearer {token}"}
        )
        assert resp.status_code == 200
        assert resp.json()["data"] == []

    async def test_list_with_sources(self, client, db_session):
        """列表包含已创建的源。"""
        token = await _admin_token(db_session)
        await client.post(
            "/api/v1/sources",
            json={"name": "official", "url": "https://ext.stackgres.io/repo"},
            headers={"Authorization": f"Bearer {token}"},
        )
        resp = await client.get(
            "/api/v1/sources", headers={"Authorization": f"Bearer {token}"}
        )
        items = resp.json()["data"]
        assert len(items) == 1
        assert items[0]["name"] == "official"


@pytest.mark.integration
class TestSourceCreate:
    async def test_create_with_defaults(self, client, db_session):
        """创建源（默认值）。"""
        token = await _admin_token(db_session)
        resp = await client.post(
            "/api/v1/sources",
            json={"name": "third-party", "url": "https://example.com/repo"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.json()["code"] == 0
        data = resp.json()["data"][0]
        assert data["name"] == "third-party"
        assert data["enabled"] is True  # 默认启用
        # 从数据库验证 priority 默认值
        source = await db_session.get(RepositorySource, data["id"])
        assert source.priority == 100  # 默认优先级


@pytest.mark.integration
class TestSourceUpdate:
    async def test_update_fields(self, client, db_session):
        """更新源字段。"""
        token = await _admin_token(db_session)
        resp = await client.post(
            "/api/v1/sources",
            json={"name": "test-source", "url": "https://test.com/repo"},
            headers={"Authorization": f"Bearer {token}"},
        )
        source_id = resp.json()["data"][0]["id"]
        resp = await client.put(
            f"/api/v1/sources/{source_id}",
            json={"enabled": False, "priority": 50},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.json()["code"] == 0
        # 验证数据库更新
        source = await db_session.get(RepositorySource, source_id)
        assert source.enabled is False
        assert source.priority == 50


@pytest.mark.integration
class TestSourceDelete:
    async def test_delete_existing(self, client, db_session):
        """删除存在的源。"""
        token = await _admin_token(db_session)
        resp = await client.post(
            "/api/v1/sources",
            json={"name": "to-delete", "url": "https://delete.com/repo"},
            headers={"Authorization": f"Bearer {token}"},
        )
        source_id = resp.json()["data"][0]["id"]
        resp = await client.delete(
            f"/api/v1/sources/{source_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.json()["code"] == 0
        result = await db_session.execute(
            select(RepositorySource).where(RepositorySource.id == source_id)
        )
        assert result.scalar_one_or_none() is None

    async def test_delete_nonexistent(self, client, db_session):
        """删除不存在的源返回 404。"""
        token = await _admin_token(db_session)
        resp = await client.delete(
            "/api/v1/sources/nonexistent-id",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 404


@pytest.mark.integration
class TestSourceHealthCheck:
    async def test_health_check_endpoint(self, client, db_session):
        """健康检查端点可调用。"""
        token = await _admin_token(db_session)
        with patch(
            "app.services.health_checker.run_health_check",
            new_callable=AsyncMock,
            return_value={"checked": 0, "healthy": 0, "degraded": 0, "down": 0},
        ):
            resp = await client.post(
                "/api/v1/sources/health-check",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.json()["code"] == 0
            assert "checked" in resp.json()["data"][0]


@pytest.mark.integration
class TestSourceAggregateIndex:
    async def test_aggregate_index_endpoint(self, client, db_session):
        """索引聚合端点可调用。"""
        token = await _admin_token(db_session)
        with patch(
            "app.services.index_aggregator.build_aggregated_index",
            new_callable=AsyncMock,
            return_value="/data/repo/index.json",
        ):
            resp = await client.post(
                "/api/v1/sources/aggregate-index",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.json()["code"] == 0
            assert "path" in resp.json()["data"][0]