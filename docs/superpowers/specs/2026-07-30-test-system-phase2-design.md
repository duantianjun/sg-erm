# SG-ERM 测试体系 Phase 2 — 引擎核心测试设计

> 日期: 2026-07-30
> 状态: 已通过 brainstorming 审批
> 前置: Phase 1 已完成（auth/crypto/whitelist/audit），48 测试全绿，覆盖率 36%

---

## 1. 背景与目标

Phase 1 建立了测试基础设施（conftest fixture、内存 SQLite、ASGITransport client）并覆盖了安全关键路径。Phase 2 聚焦**引擎核心**——sync_engine、proxy_engine、publish_service 三个模块的关键路径，验证同步过滤/清理、代理缓存/模式切换、扩展发布/签名等核心业务逻辑。

### 设计约束（沿用 Phase 1）

| # | 决策 | 来源 |
|---|------|------|
| 1 | 测试范围 | 单元（纯函数）+ 集成（DB + 文件系统 + mock HTTP），不做 e2e |
| 2 | 集成测试 DB | 内存 SQLite + StaticPool |
| 3 | 完成标准 | 关键路径优先，不设硬性覆盖率门禁 |
| 4 | 上游 HTTP mock | `aioresponses`（spec section 6 已定） |
| 5 | 文件系统 | `tmp_path` fixture 造临时 .tgz / index.json / repo_dir |
| 6 | 不重构 | `publish_extension` 的全局 `settings.repo_dir` 耦合保持原样，测试用 monkeypatch |

---

## 2. 模块测试设计

### 2.1 sync_engine

**文件**: `app/services/sync_engine.py`

#### 2.1.1 `_collect_packages` — 纯函数单元测试

**测试文件**: `tests/unit/test_sync_engine.py`

**测试目标**: 过滤逻辑（publisher/arch/os/extension include/exclude）+ 去重

**关键路径**:
- `publisher_filter` 不包含的 publisher 被跳过 (L388-389)
- `ext_exclude` 中的扩展名被跳过 (L392-393)
- `ext_include` 非空时，不在其中的扩展名被跳过 (L394-395)
- `ext_include` 为 None（空集合）时不做扩展过滤
- `arch_set` 非空时，不在其中的 arch 被跳过 (L408-409)
- `os_set` 非空时，不在其中的 os 被跳过 (L410-411)
- 去重：相同 `(publisher, arch, os, pkg_name)` 只保留一个 (L413-421)
- 返回项包含完整字段：`publisher/arch/os/package_name/extension_name/version/flavor/pg_version/build/local_path`

**Mock 边界**: 无（纯函数，直接传入 index_data dict + filters dict）

#### 2.1.2 dry-run 模式 — 集成测试

**测试文件**: `tests/integration/test_sync_engine.py`

**测试目标**: dry-run 不下载、不写元数据、不清理，`diff_summary` 标记 `dry_run: True`

**关键路径**:
- `_execute(task_id, source_id, policy_id, dry_run=True)` 走 L173-188 分支
- `diff_summary = {"dry_run": True, "packages": total, "removed": 0}`
- `downloaded=0`, `skipped=total`, `removed=0`
- `status="completed"`, `finished_at` 非空
- 不调用 `_download_packages` / `_update_db_metadata` / `_cleanup_removed_packages` / `_update_local_index`

**Mock 边界**:
- patch `SyncEngine._fetch_index` 返回固定 index_data
- patch `SyncEngine._get_filters` 返回固定 filters
- `config.repo_dir = tmp_path`（构造函数注入）
- `session_factory` 指向内存 SQLite
- 验证：查询 SyncTask 记录的 diff_summary；检查 tmp_path 下无下载文件

#### 2.1.3 `_cleanup_removed_packages` — 集成测试

**测试文件**: `tests/integration/test_sync_engine.py`

**测试目标**: 比对删除上游已移除的包文件 + 更新 ExtensionBuild.cached=False

**关键路径**:
- 构造 repo_dir 下若干 `.tar` 文件（含上游仍有的 + 上游已移除的）
- 上游 packages 列表只包含"仍有的"
- 已移除的文件被 unlink (L795)
- 对应 ExtensionBuild.cached 被设为 False (L800, `_uncache_build`)
- 返回删除数量
- OSError 不中断（L801-802）

**Mock 边界**:
- `tmp_path` 构造 repo_dir 和 publisher 子目录
- 内存 DB 插入 Publisher（`is_custom=False`）+ ExtensionBuild 记录
- `config.repo_dir = tmp_path`

---

### 2.2 proxy_engine

**文件**: `app/services/proxy_engine.py`

#### 2.2.1 服务层 — HIT/MISS/NOT_FOUND 返回值

**测试文件**: `tests/integration/test_proxy_engine.py`

**测试目标**: `handle_package_request` 返回 `(file_path, status)` 元组

**关键路径**:
- **HIT**: `tmp_path` 下预置 .tar 文件（size > 0），验证返回 `(Path, "HIT")` 且不发起 HTTP (L88-92)
- **MISS**: 本地无文件，`aioresponses` mock 上游返回 200 + 文件内容，验证拉取并缓存，返回 `(Path, "MISS")` (L99-162)
- **NOT_FOUND（strict 模式）**: `config.proxy_mode="strict"`，本地无文件，验证返回 `(None, "404")` 且不发起 HTTP (L95-97)
- **NOT_FOUND（上游 404）**: `aioresponses` mock 上游返回 404，验证返回 `(None, "404")` (L127-129)
- **NOT_FOUND（无上游）**: `_get_upstream_url` 返回 None，验证返回 `(None, "404")` (L101-103)

**Mock 边界**:
- 构造 `ProxyEngine(config=test_config, session_factory=内存 factory)`
- `aioresponses` mock `get_package_url(...)` 的响应
- patch `ProxyEngine._get_upstream_url` 返回固定 URL 或 None
- 优先用内存 DB 验证 `_mark_cached` 副作用；`_update_access_time` 失败非致命可忽略

#### 2.2.2 白名单绕过验证（安全测试点）

**测试目标**: 代理拉取不查 GlobalWhitelist（对应已改的代理逻辑）

**关键路径**:
- 内存 DB 中 GlobalWhitelist 为空（或不含目标扩展名）
- 调用 `handle_package_request`，验证仍能走 MISS 路径拉取
- 断言全程无 GlobalWhitelist 查询

**Mock 边界**: 同 2.2.1

#### 2.2.3 API 层 — X-Cache-Status 头

**测试文件**: `tests/integration/test_proxy_engine.py`

**测试目标**: 通过 `client` fixture 调 `GET /{publisher}/{arch}/{os}/{package_name}.tar`，验证响应头

**关键路径**:
- HIT: 响应头 `X-Cache-Status: HIT`
- MISS: 响应头 `X-Cache-Status: MISS`
- 404: 无 `X-Cache-Status` 头，返回 404

**Mock 边界**:
- patch `app.main.proxy_engine` 为测试实例（全局单例，L384）
- 或 patch `app.services.proxy_engine.proxy_engine`
- `aioresponses` mock 上游

---

### 2.3 publish_service

**文件**: `app/services/publish_service.py`

#### 2.3.1 `validate_tgz` — 纯函数单元测试

**测试文件**: `tests/unit/test_publish_service.py`

**测试目标**: .control 文件校验

**关键路径**:
- 含 `.control` 文件 → `(True, "")` (L54-59)
- 不含 `.control` 文件 → `(False, "扩展包中未找到 .control 文件")` (L55-57)
- 无效 tar.gz → `(False, "无效的 tar.gz 文件: ...")` (L60-62)

**Mock 边界**: 无（用 `tarfile` + `tmp_path` 构造测试 tgz）

#### 2.3.2 `build_tar_package` — 纯函数单元测试

**测试文件**: `tests/unit/test_publish_service.py`

**测试目标**: 生成 .tar 包含 .tgz + .sha256 两个成员

**关键路径**:
- 调用后 dest_path 存在
- tar 包内含两个成员，arcname 正确
- 父目录自动创建（`_ensure_dir`）

**Mock 边界**: 无（`tmp_path` 构造源文件和目标路径）

#### 2.3.3 `update_local_index` — 纯函数单元测试

**测试文件**: `tests/unit/test_publish_service.py`

**测试目标**: publicKey 写入逻辑 + extension/version/availableFor 结构

**关键路径**:
- **public_key=None**: publisher 条目不含 `publicKey` 字段 (L108-110)
- **public_key 非空 + 新 publisher**: 创建条目并写入 `publicKey` (L105-109)
- **public_key 非空 + 已存在且不同**: 更新 `publicKey` (L111-112)
- **public_key 非空 + 已存在且相同**: 不更新
- extension/version/availableFor 去重 (L134-155)
- channels 更新 (L158-161)

**Mock 边界**: 无（`tmp_path` 构造 repo_dir，预置或预空 index.json）

#### 2.3.4 `create_custom_publisher` — 集成测试

**测试文件**: `tests/integration/test_publish_service.py`

**测试目标**: RSA 密钥对生成 + Publisher 记录创建

**关键路径**:
- 调用后 Publisher 表有记录，`is_custom=True`
- `public_key` 是合法 PEM
- `private_key` 是加密后的密文（非明文 PEM）
- `display_name` 默认为 name

**Mock 边界**:
- patch `app.services.publish_service.generate_key_pair` 返回固定 PEM（避免 RSA 生成耗时，已在 test_crypto_service 验证真实函数可用）
- 内存 DB session

#### 2.3.5 `publish_extension` — 端到端集成测试

**测试文件**: `tests/integration/test_publish_service.py`

**测试目标**: 完整发布流程：校验 → 解密私钥 → 签名 → 打包 → 更新 index → 写 DB

**关键路径**:
- 成功路径：返回 `{"success": True, "package_path": ...}`
- repo_dir 下生成 .tar 文件
- index.json 更新含 publisher + extension + version + availableFor
- DB 有 Extension + ExtensionVersion + ExtensionBuild（`cached=True, verified=True`）
- 校验失败（无 .control）→ `{"success": False, "error": "..."}`
- publisher 不存在 → `{"success": False, "error": "..."}`

**Mock 边界**:
- monkeypatch `app.services.publish_service.settings.repo_dir` → `tmp_path`（全局耦合痛点）
- 或 monkeypatch 整个 `settings` 对象
- 内存 DB session
- `tmp_path` 构造合法 .tgz（含 .control）+ 预置 Publisher（含加密私钥）

---

## 3. 新增 Fixture

### 3.1 `repo_dir`（session 级，集成测试）

```python
@pytest.fixture
def repo_dir(tmp_path):
    """临时仓库目录，替代 settings.repo_dir。"""
    d = tmp_path / "repo"
    d.mkdir()
    return d
```

### 3.2 `test_config`（集成测试）

```python
@pytest.fixture
def test_config(repo_dir):
    """构造测试用 Settings，repo_dir 指向临时目录。"""
    from app.config import Settings
    return Settings(repo_dir=repo_dir)
```

### 3.3 `publish_service` settings patch

```python
@pytest.fixture
def patch_publish_settings(monkeypatch, repo_dir):
    """monkeypatch publish_service 的全局 settings.repo_dir。"""
    from app.services import publish_service
    monkeypatch.setattr(publish_service.settings, "repo_dir", repo_dir)
    yield
```

---

## 4. 文件结构

```
tests/
├── unit/
│   ├── test_sync_engine.py        # _collect_packages 纯函数
│   └── test_publish_service.py    # validate_tgz / build_tar_package / update_local_index
└── integration/
    ├── test_sync_engine.py       # dry-run / _cleanup_removed_packages
    ├── test_proxy_engine.py      # HIT/MISS/NOT_FOUND / 模式 / 白名单绕过 / X-Cache-Status
    └── test_publish_service.py    # create_custom_publisher / publish_extension 端到端
```

---

## 5. 测试范围汇总

| 模块 | 测试文件 | 测试数（估） | 关键路径 |
|------|---------|-------------|---------|
| sync_engine | unit + integration | ~12 | `_collect_packages` 过滤/去重；dry-run；`_cleanup_removed_packages` |
| proxy_engine | integration | ~10 | HIT/MISS/NOT_FOUND；strict 模式；白名单绕过；X-Cache-Status 头 |
| publish_service | unit + integration | ~12 | `validate_tgz`；`build_tar_package`；`update_local_index` 三分支；`create_custom_publisher`；`publish_extension` 端到端 |

**预计新增 ~34 个测试**，覆盖率提升目标：sync_engine 60%+，proxy_engine 70%+，publish_service 60%+。

---

## 6. 明确排除项（沿用 spec section 8）

1. 不重构 `publish_extension` 的全局 `settings.repo_dir` 耦合 — 测试用 monkeypatch 绕开
2. 不测断点续传 206/416 复杂场景 — spec 风险表标注 `aioresponses` 兼容性待 Phase 2 启动时验证，若不兼容则跳过
3. 不测 lifespan 启动逻辑
4. 不测 `proxy_only` 与 `hybrid` 的行为差异（代码中无独立分支，行为一致）

---

## 7. 已知风险

| 风险 | 影响 | 缓解 |
|------|------|------|
| `aioresponses` 对流式下载/Range 206 兼容性 | 断点续传测试可能失败 | Phase 2 不测断点续传；若 MISS 基础路径也失败则改用 `pytest-httpserver` |
| `publish_extension` monkeypatch `settings.repo_dir` 不够干净 | 其他模块读 settings.repo_dir 受影响 | fixture 作用域控制在函数级，测试后恢复 |
| proxy_engine 全局单例 patch 时机 | `app.main` 导入时已绑定 | 在 `client` fixture 中 patch `app.main.proxy_engine` |
| `_collect_packages` 依赖 `get_local_path` 等 naming 函数 | naming 已有单测，可信任 | 直接用真实 naming 函数 |
