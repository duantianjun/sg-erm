# -*- coding: utf-8 -*-
"""仪表盘 API 集成测试。

覆盖端点：
- GET  /api/v1/dashboard/stats        统计数据
- POST /api/v1/dashboard/cache/evict   手动触发缓存淘汰
"""
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from app.models import (
    Extension,
    ExtensionBuild,
    ExtensionVersion,
    GlobalWhitelist,
    Publisher,
    RepositorySource,
    SyncTask,
    User,
)
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


async def _normal_token(db_session):
    """创建普通（非管理员）用户并返回 JWT。"""
    user = User(
        username="normal",
        password_hash=get_password_hash("Normal@1234"),
        is_admin=False,
        is_active=True,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return create_access_token(
        data={"sub": user.id, "token_version": user.token_version}
    )


async def _seed_stats_data(db_session):
    """创建统计数据测试数据，返回预期计数字典。"""
    # Publisher（Extension 外键依赖）
    pub = Publisher(name="com.ongres", display_name="ONGRES")
    db_session.add(pub)
    await db_session.flush()

    # Repository Sources（2 个：1 启用 / 1 禁用）
    src1 = RepositorySource(
        name="official", url="https://ext.stackgres.io/repo", enabled=True
    )
    src2 = RepositorySource(
        name="third-party", url="https://example.com/repo", enabled=False
    )
    db_session.add_all([src1, src2])
    await db_session.flush()

    # Extensions（2 个：1 自定义 / 1 非自定义）
    ext1 = Extension(
        name="postgis", publisher_id=pub.id, source_id=src1.id, is_custom=False
    )
    ext2 = Extension(
        name="my-ext", publisher_id=pub.id, source_id=src1.id, is_custom=True
    )
    db_session.add_all([ext1, ext2])
    await db_session.flush()

    # Versions
    v1 = ExtensionVersion(extension_id=ext1.id, version="3.4", channel="stable")
    v2 = ExtensionVersion(extension_id=ext2.id, version="1.0", channel="stable")
    db_session.add_all([v1, v2])
    await db_session.flush()

    # Builds（4 个：3 缓存 / 1 未缓存）
    b1 = ExtensionBuild(
        version_id=v1.id, postgres_version="16.4", arch="x86_64", os="linux",
        flavor="pg", package_path="postgis/3.4/pg16.tar.gz", cached=True,
    )
    b2 = ExtensionBuild(
        version_id=v1.id, postgres_version="15.9", arch="x86_64", os="linux",
        flavor="pg", package_path="postgis/3.4/pg15.tar.gz", cached=True,
    )
    b3 = ExtensionBuild(
        version_id=v2.id, postgres_version="16.4", arch="x86_64", os="linux",
        flavor="pg", package_path="my-ext/1.0/pg16.tar.gz", cached=True,
    )
    b4 = ExtensionBuild(
        version_id=v2.id, postgres_version="15.9", arch="x86_64", os="linux",
        flavor="pg", package_path="my-ext/1.0/pg15.tar.gz", cached=False,
    )
    db_session.add_all([b1, b2, b3, b4])

    # Whitelist（2 条）
    w1 = GlobalWhitelist(extension_name="postgis")
    w2 = GlobalWhitelist(extension_name="pgaudit")
    db_session.add_all([w1, w2])

    # Sync Tasks（3 个：1 running / 1 completed / 1 failed）
    base = datetime(2026, 1, 1, 12, 0, 0)
    t1 = SyncTask(
        source_id=src1.id, status="running", total=10, downloaded=3,
        failed=0, started_at=base,
    )
    t2 = SyncTask(
        source_id=src1.id, status="completed", total=5, downloaded=5,
        failed=0, started_at=base - timedelta(hours=1),
    )
    t3 = SyncTask(
        source_id=src2.id, status="failed", total=8, downloaded=2,
        failed=6, started_at=base - timedelta(hours=2),
    )
    db_session.add_all([t1, t2, t3])

    await db_session.commit()

    return {
        "extensions": {"total": 2, "custom": 1},
        "packages": {"total": 4, "cached": 3},
        "sources": {"total": 2, "enabled": 1},
        "whitelist": {"total": 2},
        "sync": {"total_tasks": 3, "running": 1},
    }


def _assert_stats_keys(data):
    """断言 stats 响应 data 包含所有预期 key。"""
    # extensions
    assert "extensions" in data
    assert "total" in data["extensions"]
    assert "custom" in data["extensions"]

    # packages
    assert "packages" in data
    assert "total" in data["packages"]
    assert "cached" in data["packages"]

    # sources
    assert "sources" in data
    assert "total" in data["sources"]
    assert "enabled" in data["sources"]

    # whitelist
    assert "whitelist" in data
    assert "total" in data["whitelist"]

    # sync
    assert "sync" in data
    assert "total_tasks" in data["sync"]
    assert "running" in data["sync"]
    assert "recent" in data["sync"]

    # disk
    assert "disk" in data
    for key in (
        "total_bytes", "used_bytes", "free_bytes", "usage_percent", "file_count",
    ):
        assert key in data["disk"]

    # proxy_mode
    assert "proxy_mode" in data


@pytest.mark.integration
class TestDashboardStats:
    async def test_stats_no_auth(self, client):
        """无认证访问 → 401。"""
        resp = await client.get("/api/v1/dashboard/stats")
        assert resp.status_code == 401

    async def test_stats_empty(self, client, db_session, patch_publish_settings):
        """有认证但无数据 → 200，所有计数为 0。"""
        token = await _admin_token(db_session)
        resp = await client.get(
            "/api/v1/dashboard/stats",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == 0
        data = body["data"][0]
        _assert_stats_keys(data)

        assert data["extensions"]["total"] == 0
        assert data["extensions"]["custom"] == 0
        assert data["packages"]["total"] == 0
        assert data["packages"]["cached"] == 0
        assert data["sources"]["total"] == 0
        assert data["sources"]["enabled"] == 0
        assert data["whitelist"]["total"] == 0
        assert data["sync"]["total_tasks"] == 0
        assert data["sync"]["running"] == 0
        assert data["sync"]["recent"] == []

    async def test_stats_with_data(self, client, db_session, patch_publish_settings):
        """有认证且有数据 → 200，正确计数。"""
        expected = await _seed_stats_data(db_session)
        token = await _admin_token(db_session)
        resp = await client.get(
            "/api/v1/dashboard/stats",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        data = resp.json()["data"][0]
        _assert_stats_keys(data)

        assert data["extensions"]["total"] == expected["extensions"]["total"]
        assert data["extensions"]["custom"] == expected["extensions"]["custom"]
        assert data["packages"]["total"] == expected["packages"]["total"]
        assert data["packages"]["cached"] == expected["packages"]["cached"]
        assert data["sources"]["total"] == expected["sources"]["total"]
        assert data["sources"]["enabled"] == expected["sources"]["enabled"]
        assert data["whitelist"]["total"] == expected["whitelist"]["total"]
        assert data["sync"]["total_tasks"] == expected["sync"]["total_tasks"]
        assert data["sync"]["running"] == expected["sync"]["running"]

        # recent 最多 5 条，按 started_at 倒序
        recent = data["sync"]["recent"]
        assert len(recent) == 3
        # 最近一条应是 running 任务
        assert recent[0]["status"] == "running"
        assert recent[0]["source_name"] == "official"
        # 验证 recent 条目结构
        first = recent[0]
        assert "id" in first
        assert "source_name" in first
        assert "status" in first
        assert "total" in first
        assert "downloaded" in first
        assert "failed" in first
        assert "started_at" in first


@pytest.mark.integration
class TestCacheEvict:
    async def test_evict_no_auth(self, client):
        """无认证 → 401。"""
        resp = await client.post("/api/v1/dashboard/cache/evict")
        assert resp.status_code == 401

    async def test_evict_normal_user_forbidden(self, client, db_session):
        """普通用户 → 403（需要 admin）。"""
        token = await _normal_token(db_session)
        resp = await client.post(
            "/api/v1/dashboard/cache/evict",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 403

    async def test_evict_full(self, client, db_session, patch_publish_settings):
        """admin + mode=full → 200，调用 run_full_eviction。"""
        token = await _admin_token(db_session)
        with patch(
            "app.services.cache_eviction.run_full_eviction",
            new_callable=AsyncMock,
            return_value={"total_evicted": 5, "total_freed_bytes": 1024},
        ) as mock_run:
            resp = await client.post(
                "/api/v1/dashboard/cache/evict?mode=full",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.status_code == 200
        mock_run.assert_awaited_once()
        data = resp.json()["data"][0]
        assert data["total_evicted"] == 5
        assert data["total_freed_bytes"] == 1024
        assert "disk_after" in data

    async def test_evict_disk(self, client, db_session, patch_publish_settings):
        """admin + mode=disk → 200，调用 evict_by_disk_threshold。"""
        token = await _admin_token(db_session)
        with patch(
            "app.services.cache_eviction.evict_by_disk_threshold",
            new_callable=AsyncMock,
            return_value={"evicted": 2, "freed_bytes": 512},
        ) as mock_evict:
            resp = await client.post(
                "/api/v1/dashboard/cache/evict?mode=disk",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.status_code == 200
        mock_evict.assert_awaited_once()
        data = resp.json()["data"][0]
        assert data["disk_threshold"]["evicted"] == 2
        assert data["disk_threshold"]["freed_bytes"] == 512
        assert "disk_after" in data

    async def test_evict_ttl(self, client, db_session, patch_publish_settings):
        """admin + mode=ttl → 200，调用 evict_by_ttl。"""
        token = await _admin_token(db_session)
        with patch(
            "app.services.cache_eviction.evict_by_ttl",
            new_callable=AsyncMock,
            return_value={"evicted": 1, "freed_bytes": 256},
        ) as mock_evict:
            resp = await client.post(
                "/api/v1/dashboard/cache/evict?mode=ttl",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.status_code == 200
        mock_evict.assert_awaited_once()
        data = resp.json()["data"][0]
        assert data["ttl"]["evicted"] == 1
        assert data["ttl"]["freed_bytes"] == 256
        assert "disk_after" in data

    async def test_evict_versions(self, client, db_session, patch_publish_settings):
        """admin + mode=versions → 200，调用 evict_old_versions。"""
        token = await _admin_token(db_session)
        with patch(
            "app.services.cache_eviction.evict_old_versions",
            new_callable=AsyncMock,
            return_value={"evicted": 3, "freed_bytes": 1024},
        ) as mock_evict:
            resp = await client.post(
                "/api/v1/dashboard/cache/evict?mode=versions",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.status_code == 200
        mock_evict.assert_awaited_once()
        data = resp.json()["data"][0]
        assert data["old_versions"]["evicted"] == 3
        assert data["old_versions"]["freed_bytes"] == 1024
        assert "disk_after" in data

    async def test_evict_invalid_mode(self, client, db_session, patch_publish_settings):
        """admin + mode=invalid → 200，result 含 error。"""
        token = await _admin_token(db_session)
        resp = await client.post(
            "/api/v1/dashboard/cache/evict?mode=invalid",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        data = resp.json()["data"][0]
        assert "error" in data
        assert "invalid" in data["error"]
        assert "disk_after" in data
