# SG-ERM 测试体系 Phase 3 — 剩余 API 测试实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 完成 Phase 3 API 集成测试：tokens/sources/sync/extensions/audit 五个模块，新增约 32 个测试，整体覆盖率 ≥ 50%

**Architecture:** 沿用 Phase 1/2 fixture（httpx.ASGITransport + 内存 SQLite），通过 HTTP 调用验证响应状态码、JSON 结构和数据库状态，无需 mock 上游 HTTP（不涉及 sync_engine/proxy_engine 网络调用）

**Tech Stack:** pytest 8 + pytest-asyncio（auto 模式）+ httpx（ASGITransport）+ 内存 SQLite（aiosqlite）

**Spec:** [2026-07-30-test-system-phase3-design.md](file:///e:/stackgres/sg-erm/docs/superpowers/specs/2026-07-30-test-system-phase3-design.md)

---

## 文件结构

| 文件 | 职责 |
|------|------|
| `tests/integration/test_tokens_api.py`（新建） | API Token CRUD + 过期验证 + prefix 索引查询 + 权限隔离 |
| `tests/integration/test_sources_api.py`（新建） | RepositorySource CRUD + health-check 触发 + aggregate-index 聚合 |
| `tests/integration/test_sync_api.py`（新建） | 同步任务 trigger/tasks/cancel + policies 管理 |
| `tests/integration/test_extensions_api.py`（新建） | 扩展列表 + 详情查询 + 过滤（publisher/keyword） |
| `tests/integration/test_audit_api.py`（新建） | 审计日志筛选 + 统计汇总 |

---

## Task 1: test_tokens_api.py — API Token CRUD

**Files:**
- Create: `e:/stackgres/sg-erm/tests/integration/test_tokens_api.py`

参考源码：[app/api/tokens.py](file:///e:/stackgres/sg-erm/app/api/tokens.py)

关键点：
- 所有端点需 admin JWT（`router` 无全局 `dependencies`，但每个路由都 `Depends(require_admin)`）
- 创建 Token 时明文只返回一次（`data[0]["token"]` 需断言存在）
- 过期验证通过设置 `expires_at` 字段模拟

- [ ] **Step 1: 写失败测试**

```python
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
```

- [ ] **Step 2: 运行测试验证失败**

Run: `pytest tests/integration/test_tokens_api.py -v`
Expected: 文件不存在或导入失败

- [ ] **Step 3: 创建测试文件**

创建文件：`e:/stackgres/sg-erm/tests/integration/test_tokens_api.py`，内容如 Step 1

- [ ] **Step 4: 运行测试验证通过**

Run: `pytest tests/integration/test_tokens_api.py -v`
Expected: 7 passed

- [ ] **Step 5: 提交**

```bash
git add tests/integration/test_tokens_api.py
git commit -m "test: tokens_api 集成测试（CRUD+过期+权限）"
```

---

## Task 2: test_sources_api.py — RepositorySource CRUD

**Files:**
- Create: `e:/stackgres/sg-erm/tests/integration/test_sources_api.py`

参考源码：[app/api/sources.py](file:///e:/stackgres/sg-erm/app/api/sources.py)

关键点：
- 所有端点需 admin JWT（`router` 全局 `dependencies=[Depends(require_admin)]`）
- health-check/aggregate-index 触发后返回结果（不验证具体逻辑，只验证调用成功）

- [ ] **Step 1: 写失败测试**

```python
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
        assert data["priority"] == 100  # 默认优先级


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
            "app.api.sources.run_health_check",
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
            "app.api.sources.build_aggregated_index",
            new_callable=AsyncMock,
            return_value="/data/repo/index.json",
        ):
            resp = await client.post(
                "/api/v1/sources/aggregate-index",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.json()["code"] == 0
            assert "path" in resp.json()["data"][0]
```

- [ ] **Step 2: 运行测试验证失败**

Run: `pytest tests/integration/test_sources_api.py -v`
Expected: 文件不存在或导入失败

- [ ] **Step 3: 创建测试文件**

创建文件：`e:/stackgres/sg-erm/tests/integration/test_sources_api.py`，内容如 Step 1

- [ ] **Step 4: 运行测试验证通过**

Run: `pytest tests/integration/test_sources_api.py -v`
Expected: 7 passed

- [ ] **Step 5: 提交**

```bash
git add tests/integration/test_sources_api.py
git commit -m "test: sources_api 集成测试（CRUD+health-check+aggregate-index）"
```

---

## Task 3: test_sync_api.py — 同步任务和策略

**Files:**
- Create: `e:/stackgres/sg-erm/tests/integration/test_sync_api.py`

参考源码：[app/api/sync.py](file:///e:/stackgres/sg-erm/app/api/sync.py)

关键点：
- 所有端点需 admin JWT（`router` 全局 `dependencies=[Depends(require_admin)]`）
- trigger 会启动 sync_engine.run()，需 mock 返回模拟任务
- policies CRUD 需关联 RepositorySource

- [ ] **Step 1: 写失败测试**

```python
# -*- coding: utf-8 -*-
"""同步任务和策略 API 集成测试。"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select

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


async def _create_source(db_session, name="official"):
    """创建仓库源。"""
    source = RepositorySource(
        name=name,
        url="https://ext.stackgres.io/repo",
        enabled=True,
        health_status="healthy",
    )
    db_session.add(source)
    await db_session.commit()
    await db_session.refresh(source)
    return source


@pytest.mark.integration
class TestSyncTasks:
    async def test_list_empty(self, client, db_session):
        """空任务列表。"""
        token = await _admin_token(db_session)
        resp = await client.get(
            "/api/v1/sync/tasks", headers={"Authorization": f"Bearer {token}"}
        )
        assert resp.status_code == 200
        assert resp.json()["data"] == []

    async def test_list_with_tasks(self, client, db_session):
        """列表包含同步任务。"""
        token = await _admin_token(db_session)
        source = await _create_source(db_session)
        task = SyncTask(
            source_id=source.id,
            status="completed",
            total=10,
            downloaded=8,
            failed=1,
            skipped=1,
        )
        db_session.add(task)
        await db_session.commit()
        resp = await client.get(
            "/api/v1/sync/tasks", headers={"Authorization": f"Bearer {token}"}
        )
        items = resp.json()["data"]
        assert len(items) == 1
        assert items[0]["status"] == "completed"


@pytest.mark.integration
class TestSyncTrigger:
    async def test_trigger_creates_task(self, client, db_session):
        """触发同步创建任务。"""
        token = await _admin_token(db_session)
        source = await _create_source(db_session)

        # Mock sync_engine.run
        mock_task = MagicMock()
        mock_task.id = "task-123"
        mock_task.status = "running"
        with patch(
            "app.api.sync.sync_engine.run",
            new_callable=AsyncMock,
            return_value=mock_task,
        ):
            resp = await client.post(
                "/api/v1/sync/trigger",
                json={"source_id": source.id, "dry_run": True},
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.json()["code"] == 0
            data = resp.json()["data"][0]
            assert data["task_id"] == "task-123"
            assert data["status"] == "running"

    async def test_trigger_nonexistent_source(self, client, db_session):
        """触发不存在的源返回 404。"""
        token = await _admin_token(db_session)
        resp = await client.post(
            "/api/v1/sync/trigger",
            json={"source_id": "nonexistent"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 404


@pytest.mark.integration
class TestSyncCancel:
    async def test_cancel_running_task(self, client, db_session):
        """取消运行中的任务。"""
        token = await _admin_token(db_session)
        source = await _create_source(db_session)
        task = SyncTask(
            source_id=source.id,
            status="running",
            total=10,
            downloaded=5,
        )
        db_session.add(task)
        await db_session.commit()
        await db_session.refresh(task)

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


@pytest.mark.integration
class TestPolicies:
    async def test_create_policy(self, client, db_session):
        """创建同步策略。"""
        token = await _admin_token(db_session)
        source = await _create_source(db_session)
        with patch("app.api.sync.reload_jobs"):
            resp = await client.post(
                "/api/v1/sync/policies",
                json={"name": "daily-sync", "source_id": source.id},
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.json()["code"] == 0
            policy_id = resp.json()["data"][0]["id"]
            # 验证数据库
            policy = await db_session.get(SyncPolicy, policy_id)
            assert policy.name == "daily-sync"

    async def test_update_policy(self, client, db_session):
        """更新同步策略。"""
        token = await _admin_token(db_session)
        source = await _create_source(db_session)
        policy = SyncPolicy(name="old-name", source_id=source.id)
        db_session.add(policy)
        await db_session.commit()
        await db_session.refresh(policy)

        with patch("app.api.sync.reload_jobs"):
            resp = await client.put(
                f"/api/v1/sync/policies/{policy.id}",
                json={"name": "new-name", "enabled": False},
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.json()["code"] == 0
            await db_session.refresh(policy)
            assert policy.name == "new-name"
            assert policy.enabled is False

    async def test_delete_policy(self, client, db_session):
        """删除同步策略。"""
        token = await _admin_token(db_session)
        source = await _create_source(db_session)
        policy = SyncPolicy(name="to-delete", source_id=source.id)
        db_session.add(policy)
        await db_session.commit()
        await db_session.refresh(policy)

        with patch("app.api.sync.reload_jobs"):
            resp = await client.delete(
                f"/api/v1/sync/policies/{policy.id}",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.json()["code"] == 0
            result = await db_session.execute(
                select(SyncPolicy).where(SyncPolicy.id == policy.id)
            )
            assert result.scalar_one_or_none() is None
```

- [ ] **Step 2: 运行测试验证失败**

Run: `pytest tests/integration/test_sync_api.py -v`
Expected: 文件不存在或导入失败

- [ ] **Step 3: 创建测试文件**

创建文件：`e:/stackgres/sg-erm/tests/integration/test_sync_api.py`，内容如 Step 1

- [ ] **Step 4: 运行测试验证通过**

Run: `pytest tests/integration/test_sync_api.py -v`
Expected: 8 passed

- [ ] **Step 5: 提交**

```bash
git add tests/integration/test_sync_api.py
git commit -m "test: sync_api 集成测试（tasks/trigger/cancel/policies）"
```

---

## Task 4: test_extensions_api.py — 扩展目录

**Files:**
- Create: `e:/stackgres/sg-erm/tests/integration/test_extensions_api.py`

参考源码：[app/api/extensions.py](file:///e:/stackgres/sg-erm/app/api/extensions.py)

关键点：
- 所有端点需登录（`Depends(require_auth)`）
- 扩展详情需包含版本和构建信息
- 批量删除需验证磁盘文件和数据库记录

- [ ] **Step 1: 写失败测试**

```python
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
```

- [ ] **Step 2: 运行测试验证失败**

Run: `pytest tests/integration/test_extensions_api.py -v`
Expected: 文件不存在或导入失败

- [ ] **Step 3: 创建测试文件**

创建文件：`e:/stackgres/sg-erm/tests/integration/test_extensions_api.py`，内容如 Step 1

- [ ] **Step 4: 运行测试验证通过**

Run: `pytest tests/integration/test_extensions_api.py -v`
Expected: 7 passed

- [ ] **Step 5: 提交**

```bash
git add tests/integration/test_extensions_api.py
git commit -m "test: extensions_api 集成测试（list/detail/filter/auth）"
```

---

## Task 5: test_audit_api.py — 审计日志

**Files:**
- Create: `e:/stackgres/sg-erm/tests/integration/test_audit_api.py`

参考源码：[app/api/audit.py](file:///e:/stackgres/sg-erm/app/api/audit.py)

关键点：
- 所有端点需 admin JWT（`Depends(require_admin)`）
- 日志筛选支持 action/result/start_date/end_date
- 统计返回 total/success/failure/recent_24h

- [ ] **Step 1: 写失败测试**

```python
# -*- coding: utf-8 -*-
"""审计日志 API 集成测试。"""
from datetime import datetime, timedelta, timezone

import pytest

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


async def _create_audit_logs(db_session):
    """创建审计日志。"""
    logs = [
        AuditLog(
            actor="user:admin",
            action="post.tokens",
            resource="tokens",
            result="success",
            timestamp=datetime.now(timezone.utc) - timedelta(hours=1),
        ),
        AuditLog(
            actor="user:admin",
            action="delete.tokens",
            resource="tokens/123",
            result="failure",
            timestamp=datetime.now(timezone.utc) - timedelta(hours=2),
        ),
        AuditLog(
            actor="user:normal",
            action="get.extensions",
            resource="extensions",
            result="success",
            timestamp=datetime.now(timezone.utc) - timedelta(days=2),
        ),
    ]
    for log in logs:
        db_session.add(log)
    await db_session.commit()


@pytest.mark.integration
class TestAuditLogsList:
    async def test_list_empty(self, client, db_session):
        """空日志列表。"""
        token = await _admin_token(db_session)
        resp = await client.get(
            "/api/v1/audit/logs", headers={"Authorization": f"Bearer {token}"}
        )
        assert resp.status_code == 200
        assert resp.json()["data"] == []

    async def test_list_with_logs(self, client, db_session):
        """列表包含日志。"""
        token = await _admin_token(db_session)
        await _create_audit_logs(db_session)
        resp = await client.get(
            "/api/v1/audit/logs", headers={"Authorization": f"Bearer {token}"}
        )
        items = resp.json()["data"]
        assert len(items) == 3

    async def test_filter_by_action(self, client, db_session):
        """按动作过滤。"""
        token = await _admin_token(db_session)
        await _create_audit_logs(db_session)
        resp = await client.get(
            "/api/v1/audit/logs?action=tokens",
            headers={"Authorization": f"Bearer {token}"},
        )
        items = resp.json()["data"]
        assert len(items) == 2  # post.tokens + delete.tokens

    async def test_filter_by_result(self, client, db_session):
        """按结果过滤。"""
        token = await _admin_token(db_session)
        await _create_audit_logs(db_session)
        resp = await client.get(
            "/api/v1/audit/logs?result=success",
            headers={"Authorization": f"Bearer {token}"},
        )
        items = resp.json()["data"]
        assert len(items) == 2  # 两条 success

    async def test_filter_by_date_range(self, client, db_session):
        """按日期范围过滤。"""
        token = await _admin_token(db_session)
        await _create_audit_logs(db_session)
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
        resp = await client.get(
            f"/api/v1/audit/logs?start_date={yesterday}&end_date={today}",
            headers={"Authorization": f"Bearer {token}"},
        )
        items = resp.json()["data"]
        # 只返回今天和昨天的日志（2条）
        assert len(items) == 2


@pytest.mark.integration
class TestAuditStats:
    async def test_stats_empty(self, client, db_session):
        """空统计。"""
        token = await _admin_token(db_session)
        resp = await client.get(
            "/api/v1/audit/stats", headers={"Authorization": f"Bearer {token}"}
        )
        data = resp.json()["data"][0]
        assert data["total"] == 0
        assert data["success"] == 0
        assert data["failure"] == 0
        assert data["recent_24h"] == 0

    async def test_stats_with_logs(self, client, db_session):
        """有日志的统计。"""
        token = await _admin_token(db_session)
        await _create_audit_logs(db_session)
        resp = await client.get(
            "/api/v1/audit/stats", headers={"Authorization": f"Bearer {token}"}
        )
        data = resp.json()["data"][0]
        assert data["total"] == 3
        assert data["success"] == 2
        assert data["failure"] == 1
        assert data["recent_24h"] == 2  # 两条在 24 小时内


@pytest.mark.integration
class TestAuditAuth:
    async def test_non_admin_cannot_access(self, client, db_session):
        """非管理员无法访问。"""
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
            "/api/v1/audit/logs", headers={"Authorization": f"Bearer {token}"}
        )
        assert resp.status_code == 403
```

- [ ] **Step 2: 运行测试验证失败**

Run: `pytest tests/integration/test_audit_api.py -v`
Expected: 文件不存在或导入失败

- [ ] **Step 3: 创建测试文件**

创建文件：`e:/stackgres/sg-erm/tests/integration/test_audit_api.py`，内容如 Step 1

- [ ] **Step 4: 运行测试验证通过**

Run: `pytest tests/integration/test_audit_api.py -v`
Expected: 9 passed

- [ ] **Step 5: 提交**

```bash
git add tests/integration/test_audit_api.py
git commit -m "test: audit_api 集成测试（logs/stats/filter/auth）"
```

---

## Task 6: 全量运行 + 覆盖率验证

**Files:** 无（仅运行）

- [ ] **Step 1: 全量运行 Phase 3 测试**

Run: `pytest tests/integration/test_tokens_api.py tests/integration/test_sources_api.py tests/integration/test_sync_api.py tests/integration/test_extensions_api.py tests/integration/test_audit_api.py -v`
Expected: 32 passed

- [ ] **Step 2: 运行所有测试（Phase 1 + Phase 2 + Phase 3）**

Run: `pytest -v`
Expected: 全部 passed（Phase 1: 48 + Phase 2: 38 + Phase 3: 32 = 118 tests）

- [ ] **Step 3: 检查覆盖率**

Run: `pytest --cov=app --cov-report=term-missing`
Expected: 整体覆盖率 ≥ 50%

- [ ] **Step 4: 提交（如有修复）**

```bash
git add -A
git commit -m "test: Phase 3 测试全量通过，覆盖率 ≥ 50%"
```

---

## 自查（Self-Review）

**1. Spec 覆盖**：
- ✅ §1 tokens_api（CRUD + prefix 索引 + 过期 + 权限） → Task 1
- ✅ §1 sources_api（CRUD + health-check + aggregate-index） → Task 2
- ✅ §1 sync_api（trigger + tasks + cancel + policies） → Task 3
- ✅ §1 extensions_api（list + detail + filter） → Task 4
- ✅ §1 audit_api（logs + stats + filter） → Task 5
- ✅ §4 无需 mock 上游 HTTP → 所有测试通过 db_session 直接插入数据或 mock sync_engine
- ✅ §6 验证标准（全部通过 + 覆盖率 ≥ 50%） → Task 6

**2. 类型一致性**：
- `_admin_token(db_session)` 在 Task 1/2/3/5 用法一致 ✅
- `success()` 返回 `data` 为 list，断言用 `data[0]` ✅
- RepositorySource/ApiToken/SyncPolicy/SyncTask/Extension/AuditLog 模型字段与源码一致 ✅

**3. 无占位符**：
- 所有代码步骤包含完整测试代码 ✅
- 所有运行命令包含预期输出 ✅
- 无 TBD/TODO/类似 Task N 等占位符 ✅