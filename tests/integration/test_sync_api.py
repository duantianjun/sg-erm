# -*- coding: utf-8 -*-
"""同步任务 API 集成测试。"""
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from app.models import RepositorySource, SyncPolicy, SyncTask, User
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


async def _create_source(db_session, name="test-source", url="https://test.com/repo"):
    """创建仓库源并返回 source_id。"""
    source = RepositorySource(name=name, url=url)
    db_session.add(source)
    await db_session.commit()
    await db_session.refresh(source)
    return source.id


@pytest.mark.integration
class TestSyncTasks:
    """同步任务列表测试。"""

    async def test_list_empty(self, client, db_session):
        """空任务列表。"""
        token = await _admin_token(db_session)
        resp = await client.get(
            "/api/v1/sync/tasks",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        assert resp.json()["data"] == []

    async def test_list_with_tasks(self, client, db_session):
        """列表包含同步任务。"""
        token = await _admin_token(db_session)
        source_id = await _create_source(db_session)

        # 创建同步任务
        task = SyncTask(
            source_id=source_id,
            status="completed",
            started_at=datetime.now(timezone.utc),
            finished_at=datetime.now(timezone.utc),
        )
        db_session.add(task)
        await db_session.commit()
        await db_session.refresh(task)

        resp = await client.get(
            "/api/v1/sync/tasks",
            headers={"Authorization": f"Bearer {token}"},
        )
        items = resp.json()["data"]
        assert len(items) == 1
        assert items[0]["source_id"] == source_id
        assert items[0]["status"] == "completed"


@pytest.mark.integration
class TestSyncTrigger:
    """同步触发测试。"""

    async def test_trigger_creates_task(self, client, db_session):
        """触发同步创建任务。"""
        token = await _admin_token(db_session)
        source_id = await _create_source(db_session)

        # Mock sync_engine.run 返回模拟任务
        mock_task = SyncTask(
            id="test-task-id",
            source_id=source_id,
            status="running",
            started_at=datetime.now(timezone.utc),
        )

        with patch(
            "app.api.sync.sync_engine.run",
            new_callable=AsyncMock,
            return_value=mock_task,
        ):
            resp = await client.post(
                "/api/v1/sync/trigger",
                json={"source_id": source_id},
                headers={"Authorization": f"Bearer {token}"},
            )

        assert resp.json()["code"] == 0
        data = resp.json()["data"][0]
        assert data["task_id"] == "test-task-id"
        assert data["status"] == "running"

    async def test_trigger_nonexistent_source(self, client, db_session):
        """触发不存在的源返回 404。"""
        token = await _admin_token(db_session)

        resp = await client.post(
            "/api/v1/sync/trigger",
            json={"source_id": "nonexistent-id"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 404


@pytest.mark.integration
class TestSyncCancel:
    """同步取消测试。"""

    async def test_cancel_running_task(self, client, db_session):
        """取消运行中的任务。"""
        token = await _admin_token(db_session)
        source_id = await _create_source(db_session)

        # 创建运行中的任务
        task = SyncTask(
            source_id=source_id,
            status="running",
            started_at=datetime.now(timezone.utc),
        )
        db_session.add(task)
        await db_session.commit()
        await db_session.refresh(task)

        # Mock sync_engine.cancel 返回 True
        with patch(
            "app.api.sync.sync_engine.cancel",
            new_callable=AsyncMock,
            return_value=True,
        ):
            resp = await client.post(
                f"/api/v1/sync/cancel/{task.id}",
                headers={"Authorization": f"Bearer {token}"},
            )

        assert resp.json()["code"] == 0
        assert resp.json()["data"][0]["task_id"] == task.id


@pytest.mark.integration
class TestPolicies:
    """同步策略测试。"""

    async def test_create_policy(self, client, db_session):
        """创建同步策略。"""
        token = await _admin_token(db_session)
        source_id = await _create_source(db_session)

        resp = await client.post(
            "/api/v1/sync/policies",
            json={
                "name": "test-policy",
                "source_id": source_id,
                "filters": {"extensions": {"include": ["postgis"]}},
                "schedule": "0 * * * *",
                "enabled": True,
            },
            headers={"Authorization": f"Bearer {token}"},
        )

        assert resp.json()["code"] == 0
        data = resp.json()["data"][0]
        assert "id" in data

        # 验证数据库
        policy = await db_session.get(SyncPolicy, data["id"])
        assert policy is not None
        assert policy.name == "test-policy"
        assert policy.source_id == source_id

    async def test_update_policy(self, client, db_session):
        """更新同步策略。"""
        token = await _admin_token(db_session)
        source_id = await _create_source(db_session)

        # 创建策略
        policy = SyncPolicy(
            name="policy-to-update",
            source_id=source_id,
            enabled=True,
        )
        db_session.add(policy)
        await db_session.commit()
        await db_session.refresh(policy)

        # 更新策略
        resp = await client.put(
            f"/api/v1/sync/policies/{policy.id}",
            json={"name": "updated-policy", "enabled": False},
            headers={"Authorization": f"Bearer {token}"},
        )

        assert resp.json()["code"] == 0

        # 验证数据库更新
        await db_session.refresh(policy)
        assert policy.name == "updated-policy"
        assert policy.enabled is False

    async def test_delete_policy(self, client, db_session):
        """删除同步策略。"""
        token = await _admin_token(db_session)
        source_id = await _create_source(db_session)

        # 创建策略
        policy = SyncPolicy(
            name="policy-to-delete",
            source_id=source_id,
            enabled=True,
        )
        db_session.add(policy)
        await db_session.commit()
        await db_session.refresh(policy)
        policy_id = policy.id

        # 删除策略
        resp = await client.delete(
            f"/api/v1/sync/policies/{policy_id}",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert resp.json()["code"] == 0

        # 验证数据库删除
        deleted = await db_session.get(SyncPolicy, policy_id)
        assert deleted is None