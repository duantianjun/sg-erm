# -*- coding: utf-8 -*-
"""审计中间件集成测试。

验证：认证请求的 actor 显示为 user:<name>（对应已修复的 lesson：
"操作者显示 anonymous"）。测试走 principal 路径，避开 _get_actor
fallback 中的 get_current_user(token, session) bug（spec 记录单独修）。
"""
import pytest
from sqlalchemy import select

from app.models import AuditLog, User
from app.services.auth_service import create_access_token, get_password_hash


async def _login_as(db_session, username="admin", is_admin=True):
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


@pytest.mark.integration
class TestAuditMiddleware:
    async def test_authenticated_request_records_user_actor(self, client, db_session):
        """认证请求的审计日志 actor 应为 user:admin（非 anonymous）。"""
        user, token = await _login_as(db_session)
        # 访问受保护接口（whitelist 需要 admin）
        resp = await client.get(
            "/api/v1/whitelist", headers={"Authorization": f"Bearer {token}"}
        )
        assert resp.status_code == 200

        # 查审计日志
        result = await db_session.execute(select(AuditLog))
        logs = result.scalars().all()
        assert len(logs) >= 1
        # 至少有一条 actor 是 user:admin（而非 anonymous）
        actors = [log.actor for log in logs]
        assert "user:admin" in actors

    async def test_audit_log_contains_action_and_status(self, client, db_session):
        user, token = await _login_as(db_session)
        await client.get(
            "/api/v1/whitelist", headers={"Authorization": f"Bearer {token}"}
        )
        result = await db_session.execute(select(AuditLog))
        log = result.scalars().first()
        assert log.action.startswith("get.whitelist")
        assert log.result == "success"
        assert log.client_ip is not None

    async def test_skipped_paths_not_audited(self, client, db_session):
        """/health 在 SKIP_PATHS 中，不应产生审计日志。"""
        await client.get("/health")
        result = await db_session.execute(select(AuditLog))
        assert result.scalars().all() == []
