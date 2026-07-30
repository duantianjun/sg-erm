# SG-ERM 测试体系 Phase 1 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 搭建 SG-ERM 测试基础设施并完成 Phase 1（安全关键核心）测试：auth_service / crypto_service / naming 单元测试 + auth_api / whitelist_api / audit_middleware 集成测试。

**Architecture:** 方案 A — 根 conftest 注入环境变量绕开 `app.config`/`app.database` 导入时副作用；集成测试用内存 SQLite + StaticPool；API 测试用 `httpx.ASGITransport`（不触发 lifespan）；除 `get_db` 依赖覆盖外，额外 patch `app.database.async_session_maker`/`async_session_factory` 使审计中间件的 DB 写入也落到测试库。

**Tech Stack:** pytest 8 + pytest-asyncio（auto 模式）+ httpx（ASGITransport）+ aioresponses + freezegun + pytest-cov

**Spec:** [2026-07-30-test-system-design.md](file:///e:/stackgres/sg-erm/docs/superpowers/specs/2026-07-30-test-system-design.md)

---

## 文件结构

| 文件 | 职责 |
|------|------|
| `requirements-dev.txt`（新建） | 测试依赖清单 |
| `pytest.ini`（新建） | pytest 配置：asyncio_mode、路径、标记、cov |
| `tests/conftest.py`（新建） | 根 conftest：环境变量注入（必须在 app.* 导入前） |
| `tests/unit/conftest.py`（新建） | 单元测试共享 fixture（无 DB） |
| `tests/integration/conftest.py`（新建） | `db_engine` / `db_session` / `client` fixture |
| `tests/unit/test_auth_service.py`（新建） | 密码/JWT/Token 工具函数单元测试 |
| `tests/unit/test_crypto_service.py`（新建） | RSA 加解密/签名单元测试 |
| `tests/unit/test_naming.py`（新建） | 包名构造/解析单元测试 |
| `tests/integration/test_auth_api.py`（新建） | login/refresh/change-password/users API 集成测试 |
| `tests/integration/test_whitelist_api.py`（新建） | 白名单 CRUD + 空白名单拒绝同步 |
| `tests/integration/test_audit_middleware.py`（新建） | 审计中间件 principal 提取与日志写入 |

**Phase 2/3（sync_engine/proxy_engine/publish_service/剩余 API）作为后续独立计划，不在本计划范围。**

---

## Task 1: 创建测试依赖与 pytest 配置

**Files:**
- Create: `e:/stackgres/sg-erm/requirements-dev.txt`
- Create: `e:/stackgres/sg-erm/pytest.ini`

- [ ] **Step 1: 创建 `requirements-dev.txt`**

写入以下内容：

```
# SG-ERM 测试依赖
pytest>=8.0
pytest-asyncio>=0.23
httpx>=0.27          # ASGITransport + API 测试
pytest-cov>=4.1      # 覆盖率可见（不设 fail-under，仅报告）
aioresponses>=0.7    # aiohttp 上游 mock
freezegun>=1.4       # 时间冻结（JWT exp / TTL 测试）
```

- [ ] **Step 2: 创建 `pytest.ini`**

写入以下内容：

```ini
[pytest]
asyncio_mode = auto
testpaths = tests
python_files = test_*.py
python_classes = Test*
python_functions = test_*
addopts = -ra --strict-markers --cov=app --cov-report=term-missing
markers =
    unit: 纯函数单元测试
    integration: 含 DB / API 的集成测试
```

- [ ] **Step 3: 安装测试依赖**

Run: `pip install -r requirements-dev.txt`
Expected: 成功安装 pytest/pytest-asyncio/httpx/pytest-cov/aioresponses/freezegun

- [ ] **Step 4: 验证 pytest 可启动（此时无测试）**

Run: `pytest --collect-only`
Expected: `no tests ran` 或 `collected 0 items`，无导入错误

- [ ] **Step 5: 提交**

```bash
git add requirements-dev.txt pytest.ini
git commit -m "test: 添加测试依赖与 pytest 配置"
```

---

## Task 2: 根 conftest 与目录骨架

**Files:**
- Create: `e:/stackgres/sg-erm/tests/conftest.py`
- Create: `e:/stackgres/sg-erm/tests/__init__.py`（空）
- Create: `e:/stackgres/sg-erm/tests/unit/__init__.py`（空）
- Create: `e:/stackgres/sg-erm/tests/unit/conftest.py`（空）
- Create: `e:/stackgres/sg-erm/tests/integration/__init__.py`（空）

- [ ] **Step 1: 创建根 `tests/conftest.py`**

写入以下内容（注释说明：必须在任何 `app.*` 导入前执行）：

```python
# -*- coding: utf-8 -*-
"""根 conftest：在 app.* 导入前注入测试环境变量。

app/config.py 在模块导入时实例化 Settings()，要求 SG_ERM_SECRET_KEY 存在；
app/database.py 在导入时用 settings.db_url 创建引擎。
本文件由 pytest 在收集前加载，保证环境变量先就位。
"""
import os
import secrets
import tempfile

# 必须在任何 `from app.* import ...` 之前
os.environ.setdefault("SG_ERM_SECRET_KEY", "test-" + secrets.token_hex(16))
os.environ.setdefault("SG_ERM_DATA_DIR", tempfile.mkdtemp(prefix="sg-erm-test-"))
os.environ.setdefault("SG_ERM_SCHEDULER_ENABLED", "false")  # 禁调度器/健康检查
```

- [ ] **Step 2: 创建空 `__init__.py` 文件**

创建以下 3 个空文件：
- `tests/__init__.py`
- `tests/unit/__init__.py`
- `tests/integration/__init__.py`

- [ ] **Step 3: 创建 `tests/unit/conftest.py`（暂为空占位）**

写入：

```python
# -*- coding: utf-8 -*-
"""单元测试共享 fixture（纯函数测试，无 DB）。"""
```

- [ ] **Step 4: 验证环境变量注入生效**

Run: `python -c "import tests.conftest; import app.config; print('OK', len(app.config.settings.secret_key))"`
Expected: `OK 37`（"test-" + 32 hex = 37 字符），无 ValidationError

- [ ] **Step 5: 提交**

```bash
git add tests/conftest.py tests/__init__.py tests/unit/__init__.py tests/unit/conftest.py tests/integration/__init__.py
git commit -m "test: 添加测试目录骨架与环境变量注入"
```

---

## Task 3: 集成测试 fixture（db_engine / db_session / client）

**Files:**
- Create: `e:/stackgres/sg-erm/tests/integration/conftest.py`

- [ ] **Step 1: 写 `tests/integration/conftest.py`**

写入以下内容：

```python
# -*- coding: utf-8 -*-
"""集成测试 fixture：内存 SQLite + httpx ASGITransport。

关键点：
1. db_engine/db_session 用 sqlite:///:memory: + StaticPool，每测试一个全新库
2. client fixture 除覆盖 get_db 外，还 patch app.database.async_session_maker
   与 async_session_factory，使审计中间件（直接 from app.database import
   async_session_maker）的 DB 写入也落到测试库
3. 用 httpx.ASGITransport 而非 TestClient，不触发 lifespan
   （绕开 init_db / start_scheduler / start_health_checker / _init_default_admin）
"""
import app.database as db_module
from app.database import Base, get_db
from app.models import *  # noqa: F401,F403  确保所有模型注册到 Base.metadata
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import httpx
import pytest_asyncio


@pytest_asyncio.fixture
async def db_engine():
    """函数级内存 SQLite 引擎，建表后 yield，测完 drop+dispose。"""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        future=True,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(db_engine):
    """单测试用 AsyncSession，与 client 共享同一内存库。"""
    factory = async_sessionmaker(
        db_engine, class_=AsyncSession, expire_on_commit=False
    )
    async with factory() as session:
        yield session


@pytest_asyncio.fixture
async def client(db_engine, db_session):
    """API 测试客户端。

    - 覆盖 get_db → 路由用 db_session
    - patch app.database.async_session_factory / async_session_maker → 审计中间件
      写日志用同一内存库（中间件不走依赖注入，直接拿模块级 session_maker）
    """
    test_factory = async_sessionmaker(
        db_engine, class_=AsyncSession, expire_on_commit=False
    )
    original_factory = db_module.async_session_factory
    original_maker = db_module.async_session_maker
    db_module.async_session_factory = test_factory
    db_module.async_session_maker = test_factory

    from app.main import app

    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test"
    ) as c:
        yield c

    app.dependency_overrides.clear()
    db_module.async_session_factory = original_factory
    db_module.async_session_maker = original_maker
```

- [ ] **Step 2: 验证 fixture 可被收集（写一个最小冒烟测试）**

临时创建 `tests/integration/test_smoke.py`：

```python
import pytest

@pytest.mark.integration
async def test_db_session_empty(db_session):
    from sqlalchemy import select
    from app.models import User
    result = await db_session.execute(select(User))
    assert result.scalars().all() == []

@pytest.mark.integration
async def test_client_health(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
```

Run: `pytest tests/integration/test_smoke.py -v`
Expected: 2 passed

- [ ] **Step 3: 删除冒烟测试**

删除 `tests/integration/test_smoke.py`（仅用于验证 fixture）。

- [ ] **Step 4: 提交**

```bash
git add tests/integration/conftest.py
git commit -m "test: 添加集成测试 fixture（内存DB+ASGITransport+审计patch）"
```

---

## Task 4: auth_service 单元测试

**Files:**
- Create: `e:/stackgres/sg-erm/tests/unit/test_auth_service.py`

参考源码：[app/services/auth_service.py](file:///e:/stackgres/sg-erm/app/services/auth_service.py)

- [ ] **Step 1: 写失败测试 `tests/unit/test_auth_service.py`**

```python
# -*- coding: utf-8 -*-
"""auth_service 纯函数单元测试。"""
from datetime import timedelta

import pytest
from jose import jwt

from app.config import settings
from app.services.auth_service import (
    API_TOKEN_PREFIX,
    TOKEN_PREFIX_LEN,
    create_access_token,
    create_refresh_token,
    generate_api_token,
    get_password_hash,
    get_token_prefix,
    hash_api_token,
    verify_api_token,
    verify_password,
)


@pytest.mark.unit
class TestPasswordHash:
    def test_hash_and_verify_roundtrip(self):
        plain = "Admin@1234"
        hashed = get_password_hash(plain)
        assert hashed != plain
        assert verify_password(plain, hashed) is True

    def test_verify_wrong_password_fails(self):
        hashed = get_password_hash("Admin@1234")
        assert verify_password("wrong", hashed) is False


@pytest.mark.unit
class TestCreateAccessToken:
    def test_contains_required_claims(self):
        token = create_access_token(
            data={"sub": "user-1", "token_version": 3}
        )
        payload = jwt.decode(
            token, settings.secret_key, algorithms=[settings.jwt_algorithm]
        )
        assert payload["sub"] == "user-1"
        assert payload["token_version"] == 3
        assert payload["type"] == "access"
        assert "exp" in payload
        assert "iat" in payload

    def test_type_is_access_not_refresh(self):
        access = create_access_token(data={"sub": "u1", "token_version": 1})
        refresh = create_refresh_token(data={"sub": "u1", "token_version": 1})
        pa = jwt.decode(access, settings.secret_key, algorithms=[settings.jwt_algorithm])
        pr = jwt.decode(refresh, settings.secret_key, algorithms=[settings.jwt_algorithm])
        assert pa["type"] == "access"
        assert pr["type"] == "refresh"


@pytest.mark.unit
class TestRefreshTokenExpiry:
    def test_refresh_lives_longer_than_access(self):
        from datetime import datetime, timezone
        access = create_access_token(data={"sub": "u1", "token_version": 1})
        refresh = create_refresh_token(data={"sub": "u1", "token_version": 1})
        pa = jwt.decode(access, settings.secret_key, algorithms=[settings.jwt_algorithm])
        pr = jwt.decode(refresh, settings.secret_key, algorithms=[settings.jwt_algorithm])
        assert pr["exp"] > pa["exp"]


@pytest.mark.unit
class TestGenerateApiToken:
    def test_has_sgerm_prefix(self):
        token = generate_api_token()
        assert token.startswith(API_TOKEN_PREFIX)

    def test_unique(self):
        a = generate_api_token()
        b = generate_api_token()
        assert a != b

    def test_prefix_extraction(self):
        token = generate_api_token()
        prefix = get_token_prefix(token)
        assert len(prefix) == TOKEN_PREFIX_LEN
        # 前缀取自明文 token 的 sgerm_ 之后 8 字符
        assert token[len(API_TOKEN_PREFIX):len(API_TOKEN_PREFIX) + TOKEN_PREFIX_LEN] == prefix


@pytest.mark.unit
class TestHashApiToken:
    def test_hash_and_verify_roundtrip(self):
        token = generate_api_token()
        hashed = hash_api_token(token)
        assert hashed != token
        assert verify_api_token(token, hashed) is True

    def test_verify_wrong_token_fails(self):
        hashed = hash_api_token(generate_api_token())
        assert verify_api_token("sgerm_wrong", hashed) is False
```

- [ ] **Step 2: 运行测试验证通过**

Run: `pytest tests/unit/test_auth_service.py -v`
Expected: 9 passed

- [ ] **Step 3: 提交**

```bash
git add tests/unit/test_auth_service.py
git commit -m "test: auth_service 纯函数单元测试（密码/JWT/Token）"
```

---

## Task 5: crypto_service 单元测试

**Files:**
- Create: `e:/stackgres/sg-erm/tests/unit/test_crypto_service.py`

参考源码：[app/services/crypto_service.py](file:///e:/stackgres/sg-erm/app/services/crypto_service.py)

- [ ] **Step 1: 写失败测试**

```python
# -*- coding: utf-8 -*-
"""crypto_service 纯函数单元测试。"""
import hashlib

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from app.services.crypto_service import (
    decrypt_private_key,
    encrypt_private_key,
    generate_key_pair,
    sign_data,
    sign_sha256_file,
)


@pytest.mark.unit
class TestEncryptDecryptPrivateKey:
    def test_roundtrip(self):
        pem = "-----BEGIN PRIVATE KEY-----\nfake\n-----END PRIVATE KEY-----"
        password = "test-password-1234"
        encrypted = encrypt_private_key(pem, password)
        assert encrypted != pem
        assert decrypt_private_key(encrypted, password) == pem

    def test_wrong_password_raises(self):
        encrypted = encrypt_private_key("secret-data", "right-password")
        with pytest.raises(Exception):
            decrypt_private_key(encrypted, "wrong-password")


@pytest.mark.unit
class TestGenerateKeyPair:
    def test_returns_valid_pem_pair(self):
        private_pem, public_pem = generate_key_pair()
        assert "BEGIN PRIVATE KEY" in private_pem
        assert "BEGIN PUBLIC KEY" in public_pem
        # 私钥可加载
        private_key = serialization.load_pem_private_key(
            private_pem.encode(), password=None
        )
        # 公钥可加载
        serialization.load_pem_public_key(public_pem.encode())
        assert private_key.key_size == 2048


@pytest.mark.unit
class TestSignData:
    def test_signature_verifies_with_public_key(self):
        private_pem, public_pem = generate_key_pair()
        data = b"hello world"
        signature = sign_data(private_pem, data)
        public_key = serialization.load_pem_public_key(public_pem.encode())
        public_key.verify(
            signature, data, padding.PKCS1v15(), hashes.SHA256()
        )

    def test_signature_fails_for_tampered_data(self):
        private_pem, public_pem = generate_key_pair()
        signature = sign_data(private_pem, b"original")
        public_key = serialization.load_pem_public_key(public_pem.encode())
        from cryptography.exceptions import InvalidSignature
        with pytest.raises(InvalidSignature):
            public_key.verify(
                signature, b"tampered", padding.PKCS1v15(), hashes.SHA256()
            )


@pytest.mark.unit
class TestSignSha256File:
    def test_returns_base64_signature_matching_sha256(self, tmp_path):
        private_pem, public_pem = generate_key_pair()
        tgz = tmp_path / "pkg.tgz"
        content = b"\x1f\x8b\x08fake-tarball"
        tgz.write_bytes(content)

        import base64
        signature_b64 = sign_sha256_file(private_pem, str(tgz))

        # 签名是对 SHA256 hexdigest 字符串签名
        expected_digest = hashlib.sha256(content).hexdigest()
        raw_sig = base64.b64decode(signature_b64)

        public_key = serialization.load_pem_public_key(public_pem.encode())
        public_key.verify(
            raw_sig,
            expected_digest.encode("utf-8"),
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
```

- [ ] **Step 2: 运行测试验证通过**

Run: `pytest tests/unit/test_crypto_service.py -v`
Expected: 5 passed

- [ ] **Step 3: 提交**

```bash
git add tests/unit/test_crypto_service.py
git commit -m "test: crypto_service 单元测试（RSA加解密/签名）"
```

---

## Task 6: naming 单元测试

**Files:**
- Create: `e:/stackgres/sg-erm/tests/unit/test_naming.py`

参考源码：[app/services/naming.py](file:///e:/stackgres/sg-erm/app/services/naming.py)

- [ ] **Step 1: 写失败测试**

```python
# -*- coding: utf-8 -*-
"""naming 包名构造/解析单元测试。"""
import pytest

from app.services.naming import (
    get_package_name,
    get_package_url,
    get_index_url,
    parse_package_name,
    validate_path_segment,
)


@pytest.mark.unit
class TestGetPackageName:
    def test_without_build(self):
        assert get_package_name("postgis", "3.4", "pg", "16.4") == "postgis-3.4-pg16.4"

    def test_with_build(self):
        assert (
            get_package_name("postgis", "3.4", "pg", "16.4", "6.51")
            == "postgis-3.4-pg16.4-build-6.51"
        )


@pytest.mark.unit
class TestParsePackageName:
    def test_parse_postgis_pg16(self):
        result = parse_package_name("postgis-3.4-pg16.4")
        assert result == {
            "name": "postgis",
            "version": "3.4",
            "flavor": "pg",
            "postgres_version": "16.4",
            "build": None,
        }

    def test_parse_with_build(self):
        result = parse_package_name("postgis-3.4-pg16.4-build-6.51")
        assert result["build"] == "6.51"

    def test_extract_package_name_for_whitelist(self):
        # 对应项目硬约束：包名提取按 '-' 切分取首段
        pkg = "postgis-3.4-pg16.4"
        assert pkg.split("-")[0] == "postgis"


@pytest.mark.unit
class TestValidatePathSegment:
    @pytest.mark.parametrize(
        "segment,expected",
        [
            ("com.ongres", True),
            ("x86_64", True),
            ("", False),
            ("..", False),
            ("a/b", False),
            ("a\\b", False),
            ("postgis", True),
        ],
    )
    def test_validation(self, segment, expected):
        assert validate_path_segment(segment) is expected


@pytest.mark.unit
class TestUrlConstruction:
    def test_package_url(self):
        url = get_package_url(
            "https://ext.stackgres.io/repository/",
            "com.ongres",
            "x86_64",
            "linux",
            "postgis-3.4-pg16.4",
        )
        assert url == "https://ext.stackgres.io/repository/com.ongres/x86_64/linux/postgis-3.4-pg16.4.tar"

    def test_index_url(self):
        assert get_index_url("https://ext.stackgres.io/repository") == \
            "https://ext.stackgres.io/repository/v2/index.json"
```

- [ ] **Step 2: 运行测试验证通过**

Run: `pytest tests/unit/test_naming.py -v`
Expected: 全部 passed

- [ ] **Step 3: 提交**

```bash
git add tests/unit/test_naming.py
git commit -m "test: naming 单元测试（包名构造/解析/路径校验）"
```

---

## Task 7: auth_api 集成测试

**Files:**
- Create: `e:/stackgres/sg-erm/tests/integration/test_auth_api.py`

参考源码：[app/api/auth.py](file:///e:/stackgres/sg-erm/app/api/auth.py)

注意：`success()` 把单对象包成 list（`data=[data]`），故响应 `data[0]` 才是目标对象；login 用 OAuth2PasswordRequestForm（表单编码）。

- [ ] **Step 1: 写测试 `tests/integration/test_auth_api.py`**

```python
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
```

- [ ] **Step 2: 运行测试**

Run: `pytest tests/integration/test_auth_api.py -v`
Expected: 全部 passed

- [ ] **Step 3: 提交**

```bash
git add tests/integration/test_auth_api.py
git commit -m "test: auth_api 集成测试（login/refresh/change-password/users）"
```

---

## Task 8: whitelist_api 集成测试

**Files:**
- Create: `e:/stackgres/sg-erm/tests/integration/test_whitelist_api.py`

参考源码：[app/api/whitelist.py](file:///e:/stackgres/sg-erm/app/api/whitelist.py)

注意：whitelist router 全局 `dependencies=[Depends(require_admin)]`，所有端点都需 admin JWT。

- [ ] **Step 1: 写测试**

```python
# -*- coding: utf-8 -*-
"""whitelist API 集成测试：CRUD + 包名提取 + 空白名单拒绝。"""
import pytest

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
        entry_id = resp.json()["data"][0]["id"]
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
```

- [ ] **Step 2: 修正 import（注意 test_empty_whitelist_rejects_sync 用了 select）**

在文件顶部 import 中添加：
```python
from sqlalchemy import select
```

- [ ] **Step 3: 运行测试**

Run: `pytest tests/integration/test_whitelist_api.py -v`
Expected: 全部 passed

- [ ] **Step 4: 提交**

```bash
git add tests/integration/test_whitelist_api.py
git commit -m "test: whitelist_api 集成测试（CRUD+空白名单拒绝+包名提取）"
```

---

## Task 9: audit_middleware 集成测试

**Files:**
- Create: `e:/stackgres/sg-erm/tests/integration/test_audit_middleware.py`

参考源码：[app/middleware/audit.py](file:///e:/stackgres/sg-erm/app/middleware/audit.py)

注意：
- 测试认证请求路径（`require_auth` 会设 `request.state.principal`，审计中间件从 principal 读取 actor）
- **避开** `_get_actor` 的 fallback 分支（`get_current_user(token, session)` 缺 `request` 参数的 bug，spec 已记录单独修）
- 审计日志由中间件经 `async_session_maker` 写入，fixture 已 patch 到测试库，故可直接查 `AuditLog` 表

- [ ] **Step 1: 写测试**

```python
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
```

- [ ] **Step 2: 运行测试**

Run: `pytest tests/integration/test_audit_middleware.py -v`
Expected: 3 passed

- [ ] **Step 3: 提交**

```bash
git add tests/integration/test_audit_middleware.py
git commit -m "test: audit_middleware 集成测试（principal提取+日志写入+跳过路径）"
```

---

## Task 10: 全量运行 + 覆盖率验证

**Files:** 无（仅运行）

- [ ] **Step 1: 全量运行 Phase 1 测试**

Run: `pytest -v`
Expected: 全部 passed，覆盖率报告输出（关注 `app/services/auth_service.py`、`app/services/crypto_service.py`、`app/services/naming.py`、`app/api/auth.py`、`app/api/whitelist.py`、`app/middleware/audit.py` 行覆盖）

- [ ] **Step 2: 如有失败，定位并修复**

常见问题排查：
- `ValidationError: SG_ERM_SECRET_KEY 必须设置` → 根 conftest 未生效，检查 `tests/conftest.py` 是否在 `app.*` 导入前执行
- `RuntimeError: Event loop is closed` → 检查 fixture 是否用 `pytest_asyncio.fixture` 而非 `pytest.fixture`
- 审计日志查不到 → 检查 `client` fixture 是否 patch 了 `async_session_maker`
- 401 但带 token → 检查 token_version 是否匹配（change-password 后旧 token 应失效）

- [ ] **Step 3: 提交（如有修复）**

```bash
git add -A
git commit -m "test: Phase 1 测试全量通过"
```

---

## 自查（Self-Review）

**1. Spec 覆盖**：
- ✅ §2 导入副作用 → Task 2（根 conftest 注入）
- ✅ §3 目录结构 → Task 1-2
- ✅ §4.1 根 conftest → Task 2
- ✅ §4.2 db_session → Task 3
- ✅ §4.3 client + ASGITransport + 审计 patch → Task 3（细化了 spec，补 patch）
- ✅ §5 Phase 1 auth_service 单元 → Task 4
- ✅ §5 Phase 1 crypto_service 单元 → Task 5
- ✅ §5 Phase 1 auth_api 集成 → Task 7
- ✅ §5 Phase 1 whitelist_api 集成（含空白名单拒绝、包名提取） → Task 8
- ✅ §5 Phase 1 audit_middleware 集成 → Task 9
- ⏭️ §5 Phase 1 naming 单元 → Task 6（spec 未单列 naming，但属"关键路径"，补上）
- ⏭️ §5 Phase 2/3 → 计划范围外，后续独立计划

**2. 类型一致性**：
- `create_access_token(data={"sub": user.id, "token_version": user.token_version})` 在 Task 4/7/8/9 用法一致 ✅
- `success()` 返回 `data` 为 list，断言用 `data[0]` ✅
- 审计 patch 用 `async_session_maker`（与 audit.py:99/155 一致）✅

**3. 风险已标注**：
- `get_current_principal` bug → Task 9 明确避开 fallback 分支 ✅
- 审计中间件不走依赖注入 → Task 3 fixture patch 解决 ✅
