# -*- coding: utf-8 -*-
"""审计日志 API 集成测试。

覆盖审计日志查询、统计和权限控制。
"""
import pytest
from datetime import datetime, timedelta, timezone
from sqlalchemy import select

from app.models import AuditLog, User
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


async def _normal_user_token(db_session):
    """创建普通用户并返回 JWT。"""
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


async def _create_audit_log(
    db_session,
    actor="admin",
    action="sync.start",
    resource="/api/v1/sync",
    result="success",
    timestamp=None,
):
    """辅助函数：创建审计日志。"""
    log = AuditLog(
        actor=actor,
        action=action,
        resource=resource,
        detail={"test": "data"},
        result=result,
        client_ip="127.0.0.1",
        timestamp=timestamp or datetime.now(timezone.utc),
    )
    db_session.add(log)
    await db_session.commit()
    await db_session.refresh(log)
    return log


@pytest.mark.integration
class TestAuditLogsList:
    """审计日志列表查询测试。"""

    async def test_empty_logs_list(self, client, db_session):
        """空日志列表返回空数组。"""
        token = await _admin_token(db_session)
        resp = await client.get(
            "/api/v1/audit/logs", headers={"Authorization": f"Bearer {token}"}
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == 0
        assert body["data"] == []
        assert body["count"] == 0

    async def test_logs_list_contains_entries(self, client, db_session):
        """日志列表包含审计记录。"""
        token = await _admin_token(db_session)
        # 创建测试日志
        await _create_audit_log(db_session, action="sync.start")
        await _create_audit_log(db_session, action="sync.complete")

        resp = await client.get(
            "/api/v1/audit/logs", headers={"Authorization": f"Bearer {token}"}
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == 0
        assert len(body["data"]) == 2
        assert body["count"] == 2
        # 验证字段存在
        log = body["data"][0]
        assert "id" in log
        assert "timestamp" in log
        assert "actor" in log
        assert "action" in log
        assert "resource" in log
        assert "result" in log

    async def test_filter_by_action(self, client, db_session):
        """按动作类型过滤。"""
        token = await _admin_token(db_session)
        await _create_audit_log(db_session, action="sync.start")
        await _create_audit_log(db_session, action="sync.complete")
        await _create_audit_log(db_session, action="publish")

        resp = await client.get(
            "/api/v1/audit/logs?action=sync",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        body = resp.json()
        # 应包含 sync.start 和 sync.complete
        assert len(body["data"]) == 2

    async def test_filter_by_result(self, client, db_session):
        """按结果过滤。"""
        token = await _admin_token(db_session)
        await _create_audit_log(db_session, result="success")
        await _create_audit_log(db_session, result="success")
        await _create_audit_log(db_session, result="failure")

        resp = await client.get(
            "/api/v1/audit/logs?result=success",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["data"]) == 2
        # 所有返回的日志 result 应为 success
        for log in body["data"]:
            assert log["result"] == "success"

    async def test_filter_by_date_range(self, client, db_session):
        """按日期范围过滤。"""
        token = await _admin_token(db_session)

        # 创建不同时间的日志
        today = datetime.now(timezone.utc)
        yesterday = today - timedelta(days=1)
        week_ago = today - timedelta(days=7)

        await _create_audit_log(db_session, timestamp=today)
        await _create_audit_log(db_session, timestamp=yesterday)
        await _create_audit_log(db_session, timestamp=week_ago)

        # 查询最近 2 天的日志
        start_date = (today - timedelta(days=1)).strftime("%Y-%m-%d")
        end_date = today.strftime("%Y-%m-%d")

        resp = await client.get(
            f"/api/v1/audit/logs?start_date={start_date}&end_date={end_date}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        body = resp.json()
        # 应包含今天和昨天的日志（2 条）
        assert len(body["data"]) == 2


@pytest.mark.integration
class TestAuditStats:
    """审计统计测试。"""

    async def test_empty_stats(self, client, db_session):
        """空统计返回全零。"""
        token = await _admin_token(db_session)
        resp = await client.get(
            "/api/v1/audit/stats", headers={"Authorization": f"Bearer {token}"}
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == 0
        stats = body["data"][0]
        assert stats["total"] == 0
        assert stats["success"] == 0
        assert stats["failure"] == 0
        assert stats["recent_24h"] == 0

    async def test_stats_with_logs(self, client, db_session):
        """有日志的统计。"""
        token = await _admin_token(db_session)
        # 创建测试日志
        await _create_audit_log(db_session, result="success")
        await _create_audit_log(db_session, result="success")
        await _create_audit_log(db_session, result="failure")

        resp = await client.get(
            "/api/v1/audit/stats", headers={"Authorization": f"Bearer {token}"}
        )
        assert resp.status_code == 200
        body = resp.json()
        stats = body["data"][0]
        assert stats["total"] == 3
        assert stats["success"] == 2
        assert stats["failure"] == 1
        assert stats["recent_24h"] == 3  # 都是最近创建的


@pytest.mark.integration
class TestAuditAuth:
    """审计 API 权限测试。"""

    async def test_non_admin_cannot_access_logs(self, client, db_session):
        """非管理员无法访问审计日志列表。"""
        token = await _normal_user_token(db_session)
        resp = await client.get(
            "/api/v1/audit/logs", headers={"Authorization": f"Bearer {token}"}
        )
        assert resp.status_code == 403

    async def test_non_admin_cannot_access_stats(self, client, db_session):
        """非管理员无法访问审计统计。"""
        token = await _normal_user_token(db_session)
        resp = await client.get(
            "/api/v1/audit/stats", headers={"Authorization": f"Bearer {token}"}
        )
        assert resp.status_code == 403