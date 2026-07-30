# SG-ERM 测试体系建设设计

> 日期: 2026-07-30
> 状态: 已通过 brainstorming 审批，待 spec review

---

## 1. 背景与目标

SG-ERM 设计完成度已达 91%（见 REVIEW.md），核心功能稳定运行，但项目缺少自动化测试体系：无 `tests/` 目录、无测试依赖、无 CI 校验。当前所有回归依赖人工验证，风险随功能迭代累积。

本设计目标：**建立分层测试体系，覆盖安全关键核心与引擎核心的关键路径，为后续迭代提供回归安全网。**

### 已确认的约束（来自 brainstorming）

| # | 决策 | 选项 |
|---|------|------|
| 1 | 测试范围 | 单元 + 集成测试（不做 e2e） |
| 2 | 集成测试 DB | 内存 SQLite + StaticPool |
| 3 | 完成标准 | 关键路径优先，不设硬性覆盖率门禁 |
| 4 | 架构方案 | 方案 A — conftest 注入环境变量 + FastAPI 依赖覆盖，零生产改动 |

---

## 2. 核心难点：导入时副作用

`app/config.py:122` 在模块导入时执行 `settings = Settings()`，要求 `SG_ERM_SECRET_KEY` 等环境变量必须存在；`app/database.py:35` 在导入时用 `settings.db_url` 创建引擎。任何 `from app.* import ...` 都会触发这两个副作用。

**解决方案**：在根 `tests/conftest.py` 顶部（任何 `app.*` 导入之前）注入测试环境变量。pytest 保证根 conftest 先于测试模块加载，app 模块随后导入即可拿到合法配置。

---

## 3. 目录结构

```
sg-erm/
├── tests/
│   ├── conftest.py                 # 根：环境变量注入（必须在 app.* 导入前）
│   ├── unit/                       # 纯函数测试，无 DB 无网络
│   │   ├── conftest.py
│   │   ├── test_auth_service.py    # 密码哈希/JWT/Token 工具
│   │   ├── test_crypto_service.py  # RSA 加解密/签名
│   │   ├── test_naming.py
│   │   └── test_index_aggregator.py
│   ├── integration/                # DB + API 测试
│   │   ├── conftest.py             # db_session / client fixture
│   │   ├── test_auth_api.py
│   │   ├── test_tokens_api.py
│   │   ├── test_whitelist_api.py
│   │   ├── test_sources_api.py
│   │   ├── test_sync_engine.py
│   │   ├── test_proxy_engine.py
│   │   ├── test_publish_service.py
│   │   └── test_audit_middleware.py
│   └── fixtures/                   # 测试数据（样例 index.json、mini .tgz）
├── requirements-dev.txt
└── pytest.ini
```

---

## 4. Fixture 机制

### 4.1 根 conftest（环境注入）

```python
# tests/conftest.py
import os
import secrets
import tempfile

# 必须在任何 app.* 导入前执行
os.environ.setdefault("SG_ERM_SECRET_KEY", "test-" + secrets.token_hex(16))  # ≥32 字节
os.environ.setdefault("SG_ERM_DATA_DIR", tempfile.mkdtemp(prefix="sg-erm-test-"))
os.environ.setdefault("SG_ERM_SCHEDULER_ENABLED", "false")  # 禁调度器/健康检查
```

### 4.2 `db_session` fixture（函数级，服务层集成测试）

每测试一个全新内存库，零隔离污染。服务函数大多签名是 `async def f(db: AsyncSession, ...)`，直接传入即可。

```python
@pytest_asyncio.fixture
async def db_session():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        future=True,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()
```

### 4.3 `client` fixture（函数级，API 测试）

**关键决策**：用 `httpx.ASGITransport` 而非 `starlette.TestClient`，**不触发 lifespan** —— 从而绕开 `init_db` / `start_scheduler` / `start_health_checker` / `_init_default_admin`。测试数据由测试自己通过 `db_session` 显式构造，更确定、更快、无副作用。

```python
@pytest_asyncio.fixture
async def client(db_session):
    from app.main import app
    from app.database import get_db

    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()
```

需要登录态的 API 测试：用 `db_session` 建用户，再用 `create_access_token` 造 JWT 注入 `Authorization` 头；API Token 测试同理。

---

## 5. 测试范围与分期

### Phase 1 — 安全关键核心（最高优先级）

对应项目硬约束与已修复的 lessons。

| 测试文件 | 关键路径 |
|---------|---------|
| `unit/test_auth_service.py` | 密码哈希往返；JWT 含 `token_version`/`type`/`exp`；`create_access_token` vs `create_refresh_token` 类型隔离；`generate_api_token` 前缀 `sgerm_` + 长度；`get_token_prefix` 切 8 字符；`hash_api_token`/`verify_api_token` 往返 |
| `unit/test_crypto_service.py` | `encrypt_private_key`/`decrypt_private_key` 往返；错密码失败；`generate_key_pair` 返回合法 PEM 对；`sign_data` 用公钥验证通过；`sign_sha256_file` 对 tmp .tgz 签名且 SHA256 正确 |
| `integration/test_auth_api.py` | login 成功/密码错/用户禁用；refresh 用 access_token 应失败、用 refresh_token 成功；change-password 后旧 JWT 失效（token_version 递增）；users CRUD 仅 admin |
| `integration/test_whitelist_api.py` | CRUD；**空白名单时同步请求全拒绝**（硬约束）；包名提取 `postgis-3.4-pg16.4` → `postgis` |
| `integration/test_audit_middleware.py` | principal 由 `require_auth` 写入 `request.state`；中间件在 `call_next` 之后读取；操作者显示 `user:admin` 而非 `anonymous`（对应已修复的 lesson） |

### Phase 2 — 引擎核心

- `test_sync_engine.py`：`_collect_packages` 过滤、`_cleanup_removed_packages` 比对删除、dry-run 不落盘（mock aiohttp 上游）
- `test_proxy_engine.py`：HIT 直返 / MISS 上游拉取缓存 / 404；`X-Cache-Status` 头；strict/proxy_only/hybrid 模式切换；代理拉取**不走白名单**（对应已改的代理逻辑）
- `test_publish_service.py`：`validate_tgz` 校验 .control；`build_tar_package` 生成 .sha256+.tgz；`update_local_index` 写 publicKey；`create_custom_publisher` RSA 密钥对

### Phase 3 — 剩余 API

`test_tokens_api.py`（prefix 索引、过期）、`test_sources_api.py`（CRUD、health-check、aggregate-index）、`test_sync_api.py`（trigger/tasks/policies）、`test_extensions_api.py`、`test_audit_api.py`（筛选/统计）

---

## 6. Mock 边界

- **上游 HTTP**（StackGres 仓库）：用 `aioresponses` mock `aiohttp`（项目上游客户端是 aiohttp，非 httpx），不触真实网络
- **文件系统**：用 `tmp_path` 造临时 .tgz / index.json
- **时间**：需要固定时间的断言用 `freezegun` 或直接传 `datetime`

---

## 7. 依赖与配置

### `requirements-dev.txt`

```
pytest>=8.0
pytest-asyncio>=0.23
httpx>=0.27          # ASGITransport + API 测试
pytest-cov>=4.1      # 覆盖率可见（不设 fail-under，仅报告）
aioresponses>=0.7    # aiohttp 上游 mock
freezegun>=1.4       # 时间冻结（JWT exp / TTL 测试）
```

### `pytest.ini`

```ini
[pytest]
asyncio_mode = auto
testpaths = tests
python_files = test_*.py
addopts = -ra --strict-markers --cov=app --cov-report=term-missing
markers =
    unit: 纯函数单元测试
    integration: 含 DB / API 的集成测试
```

---

## 8. 明确排除项（避免范围蔓延）

1. **不重构** `app/config.py` / `app/database.py` 的导入时副作用 —— 那是方案 B（懒加载重构）的事，作为独立技术债项另立项
2. **不修** `auth_service.py:272` 的 `get_current_user(token, db)` 缺 `request` 参数 bug —— 先记录，测试中绕开，单独修复
3. **不做 e2e** —— 不启真实 uvicorn、不连真实上游
4. **不做 CI 集成** —— 本次只产测试代码与配置；GitHub Actions 可作为后续独立任务

---

## 9. 已知风险

| 风险 | 影响 | 缓解 |
|------|------|------|
| `get_current_principal` 存在 bug（缺 request 参数） | 该函数相关路径无法直接测 | 测试中通过直接构造 `request.state.principal` 绕开，bug 单独修 |
| 内存 SQLite 与生产 SQLite 文件模式有差异（WAL 等） | WAL/并发特性测不到 | 符合本次选型决策，可接受；并发场景留待 e2e |
| `aioresponses` 对 `aiohttp` 复杂场景（流式下载、Range 206）的兼容性 | 断点续传类测试可能需要更细粒度 mock | Phase 2 启动时验证，必要时改用 `pytest-httpserver` 起本地真实 HTTP |
| `httpx.ASGITransport` 不跑 lifespan | lifespan 启动逻辑无测试覆盖 | 显式排除；lifespan 逻辑（init_db/scheduler/health_checker）由 e2e 或手动覆盖 |

---

## 10. 验证方式

设计完成后的验证标准：
- `pytest` 在仓库根目录可一键运行，全绿
- `pytest --cov=app` 输出覆盖率报告（仅可见，不门禁）
- Phase 1 测试全部通过，覆盖 auth/crypto/whitelist/audit 的关键路径
- 生产代码零改动（仅新增 `tests/`、`requirements-dev.txt`、`pytest.ini`）
