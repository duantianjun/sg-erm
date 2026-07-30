# SG-ERM 测试体系 Phase 2 — 引擎核心测试实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 sync_engine / proxy_engine / publish_service 三个引擎核心模块建立关键路径测试，预计新增 ~34 个测试。

**Architecture:** 沿用 Phase 1 的 fixture 体系（内存 SQLite + ASGITransport + aioresponses）。纯函数走单元测试，涉及 DB/文件系统/HTTP 的走集成测试。引擎实例通过构造函数注入 test config（`SimpleNamespace`）和内存 session_factory，避免全局单例耦合。

**Tech Stack:** pytest, pytest-asyncio, httpx (ASGITransport), aioresponses, aiosqlite, SQLAlchemy 2.0 async

**Spec:** `docs/superpowers/specs/2026-07-30-test-system-phase2-design.md`

---

## 文件结构

```
tests/
├── unit/
│   ├── test_sync_engine.py        # 新建 — _collect_packages 纯函数测试
│   └── test_publish_service.py     # 新建 — validate_tgz / build_tar_package / update_local_index
└── integration/
    ├── conftest.py                # 修改 — 新增 repo_dir / test_config / patch_publish_settings fixture
    ├── test_sync_engine.py        # 新建 — dry-run / _cleanup_removed_packages
    ├── test_proxy_engine.py       # 新建 — HIT/MISS/NOT_FOUND / 模式 / 白名单绕过 / X-Cache-Status
    └── test_publish_service.py    # 新建 — create_custom_publisher / publish_extension 端到端
```

---

## Task 1: sync_engine `_collect_packages` 单元测试

**Files:**
- Create: `tests/unit/test_sync_engine.py`

**Files to read for context:**
- `app/services/sync_engine.py:357-438` — `_collect_packages` 实现
- `app/services/naming.py:21-92` — `get_package_name` / `get_local_path` / `get_publisher_name` 等

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_sync_engine.py`:

```python
# -*- coding: utf-8 -*-
"""sync_engine._collect_packages 纯函数单元测试。

覆盖过滤逻辑：publisher/arch/os/extension include+exclude + 去重。
"""
import pytest

from app.services.sync_engine import SyncEngine


def _make_index_data(extensions):
    """构造 index.json 结构的测试数据。"""
    return {"extensions": extensions}


def _make_ext(name, publisher, versions):
    """构造单个 extension 条目。"""
    return {"name": name, "publisher": publisher, "versions": versions}


def _make_ver(version, available_for):
    """构造单个 version 条目。"""
    return {"version": version, "availableFor": available_for}


def _make_target(flavor="pg", pg="16.4", arch="x86_64", os="linux", build=None):
    """构造单个 availableFor 条目。"""
    return {
        "flavor": flavor,
        "postgresVersion": pg,
        "arch": arch,
        "os": os,
        "build": build,
    }


@pytest.fixture
def engine():
    """SyncEngine 实例（_collect_packages 是纯函数，不触碰 session_factory/config）。"""
    return SyncEngine()


@pytest.mark.unit
class TestCollectPackages:
    def test_basic_collection(self, engine):
        """单扩展单版本单构建 → 返回 1 个包，字段完整。"""
        index = _make_index_data([
            _make_ext("postgis", "com.ongres", [
                _make_ver("3.4", [_make_target()])
            ])
        ])
        filters = {"extensions": {"include": ["postgis"], "exclude": []}}

        packages = engine._collect_packages(index, filters)

        assert len(packages) == 1
        pkg = packages[0]
        assert pkg["publisher"] == "com.ongres"
        assert pkg["arch"] == "x86_64"
        assert pkg["os"] == "linux"
        assert pkg["package_name"] == "postgis-3.4-pg16.4"
        assert pkg["extension_name"] == "postgis"
        assert pkg["version"] == "3.4"
        assert pkg["flavor"] == "pg"
        assert pkg["pg_version"] == "16.4"
        assert pkg["build"] is None
        assert pkg["local_path"] == "com.ongres/x86_64/linux/postgis-3.4-pg16.4.tar"

    def test_publisher_filter_excludes_others(self, engine):
        """publisher_filter 不包含的 publisher 被跳过。"""
        index = _make_index_data([
            _make_ext("postgis", "com.ongres", [_make_ver("3.4", [_make_target()])]),
            _make_ext("other", "org.other", [_make_ver("1.0", [_make_target()])]),
        ])
        filters = {"publisher": ["com.ongres"]}

        packages = engine._collect_packages(index, filters)

        assert len(packages) == 1
        assert packages[0]["extension_name"] == "postgis"

    def test_ext_exclude_skips_extension(self, engine):
        """ext_exclude 中的扩展名被跳过。"""
        index = _make_index_data([
            _make_ext("postgis", "com.ongres", [_make_ver("3.4", [_make_target()])]),
            _make_ext("timescaledb", "com.ongres", [_make_ver("2.0", [_make_target()])]),
        ])
        filters = {"extensions": {"exclude": ["timescaledb"]}}

        packages = engine._collect_packages(index, filters)

        assert len(packages) == 1
        assert packages[0]["extension_name"] == "postgis"

    def test_ext_include_filters_to_whitelist(self, engine):
        """ext_include 非空时，不在其中的扩展名被跳过。"""
        index = _make_index_data([
            _make_ext("postgis", "com.ongres", [_make_ver("3.4", [_make_target()])]),
            _make_ext("timescaledb", "com.ongres", [_make_ver("2.0", [_make_target()])]),
        ])
        filters = {"extensions": {"include": ["postgis"], "exclude": []}}

        packages = engine._collect_packages(index, filters)

        assert len(packages) == 1
        assert packages[0]["extension_name"] == "postgis"

    def test_empty_ext_include_does_not_filter(self, engine):
        """ext_include 为 None（未设置）时不做扩展过滤。"""
        index = _make_index_data([
            _make_ext("postgis", "com.ongres", [_make_ver("3.4", [_make_target()])]),
            _make_ext("timescaledb", "com.ongres", [_make_ver("2.0", [_make_target()])]),
        ])
        filters = {}  # 无 extensions 键

        packages = engine._collect_packages(index, filters)

        assert len(packages) == 2

    def test_arch_filter_excludes_non_matching(self, engine):
        """arch_set 非空时，不在其中的 arch 被跳过。"""
        index = _make_index_data([
            _make_ext("postgis", "com.ongres", [
                _make_ver("3.4", [
                    _make_target(arch="x86_64"),
                    _make_target(arch="arm64"),
                ])
            ])
        ])
        filters = {"arch": ["x86_64"]}

        packages = engine._collect_packages(index, filters)

        assert len(packages) == 1
        assert packages[0]["arch"] == "x86_64"

    def test_os_filter_excludes_non_matching(self, engine):
        """os_set 非空时，不在其中的 os 被跳过。"""
        index = _make_index_data([
            _make_ext("postgis", "com.ongres", [
                _make_ver("3.4", [
                    _make_target(os="linux"),
                    _make_target(os="darwin"),
                ])
            ])
        ])
        filters = {"os": ["linux"]}

        packages = engine._collect_packages(index, filters)

        assert len(packages) == 1
        assert packages[0]["os"] == "linux"

    def test_dedup_same_publisher_arch_os_pkg(self, engine):
        """相同 (publisher, arch, os, pkg_name) 只保留一个。"""
        index = _make_index_data([
            _make_ext("postgis", "com.ongres", [
                _make_ver("3.4", [
                    _make_target(),
                    _make_target(),  # 完全相同的 target
                ])
            ])
        ])
        filters = {}

        packages = engine._collect_packages(index, filters)

        assert len(packages) == 1

    def test_build_in_package_name(self, engine):
        """build 非空时包名含 -build-{build}。"""
        index = _make_index_data([
            _make_ext("postgis", "com.ongres", [
                _make_ver("3.4", [_make_target(build="6.51")])
            ])
        ])
        filters = {}

        packages = engine._collect_packages(index, filters)

        assert len(packages) == 1
        assert packages[0]["package_name"] == "postgis-3.4-pg16.4-build-6.51"
        assert packages[0]["build"] == "6.51"

    def test_empty_index_returns_empty_list(self, engine):
        """空 index_data 返回空列表。"""
        packages = engine._collect_packages({"extensions": []}, {})
        assert packages == []
```

- [ ] **Step 2: Run tests to verify they pass (code already exists)**

Run: `python -m pytest tests/unit/test_sync_engine.py -v --no-cov`
Expected: PASS (10 tests) — `_collect_packages` 已实现，测试验证现有行为

- [ ] **Step 3: Commit**

```bash
git add tests/unit/test_sync_engine.py
git commit -m "test: add unit tests for sync_engine _collect_packages"
```

---

## Task 2: publish_service 纯函数单元测试

**Files:**
- Create: `tests/unit/test_publish_service.py`

**Files to read for context:**
- `app/services/publish_service.py:41-173` — `validate_tgz` / `build_tar_package` / `update_local_index`
- `app/services/publish_service.py:1-40` — imports 和 `_ensure_dir`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_publish_service.py`:

```python
# -*- coding: utf-8 -*-
"""publish_service 纯函数单元测试。

覆盖：validate_tgz / build_tar_package / update_local_index
"""
import json
import os
import tarfile

import pytest

from app.services.publish_service import (
    build_tar_package,
    update_local_index,
    validate_tgz,
)


def _make_tgz(path, files):
    """构造 tar.gz 测试文件。

    Args:
        path: 目标 .tgz 路径
        files: dict of {name: content_bytes}
    """
    with tarfile.open(path, "w:gz") as tf:
        for name, content in files.items():
            import io
            info = tarfile.TarInfo(name=name)
            info.size = len(content)
            tf.addfile(info, io.BytesIO(content))


@pytest.mark.unit
class TestValidateTgz:
    def test_valid_tgz_with_control_file(self, tmp_path):
        """含 .control 文件 → (True, "")。"""
        tgz = tmp_path / "ext.tgz"
        _make_tgz(str(tgz), {"postgis.control": b"[extension]"})
        valid, error = validate_tgz(str(tgz))
        assert valid is True
        assert error == ""

    def test_tgz_without_control_file(self, tmp_path):
        """不含 .control 文件 → (False, "...未找到...")。"""
        tgz = tmp_path / "ext.tgz"
        _make_tgz(str(tgz), {"README.md": b"hello"})
        valid, error = validate_tgz(str(tgz))
        assert valid is False
        assert "未找到 .control" in error

    def test_invalid_tgz_file(self, tmp_path):
        """无效 tar.gz → (False, "...无效的 tar.gz...")。"""
        bad = tmp_path / "bad.tgz"
        bad.write_bytes(b"not a tar.gz file")
        valid, error = validate_tgz(str(bad))
        assert valid is False
        assert "无效的 tar.gz" in error


@pytest.mark.unit
class TestBuildTarPackage:
    def test_creates_tar_with_two_members(self, tmp_path):
        """build_tar_package 生成 .tar 包含 .tgz + .sha256 两个成员。"""
        tgz = tmp_path / "ext.tgz"
        _make_tgz(str(tgz), {"postgis.control": b"[ext]"})
        sha = tmp_path / "ext.sha256"
        sha.write_bytes(b"base64signature==")
        dest_dir = tmp_path / "dest"
        dest = dest_dir / "ext.tar"

        build_tar_package(str(tgz), str(sha), str(dest))

        assert dest.exists()
        with tarfile.open(str(dest), "r") as tf:
            names = tf.getnames()
            assert "ext.tgz" in names
            assert "ext.sha256" in names
            assert len(names) == 2

    def test_creates_parent_dir_if_missing(self, tmp_path):
        """父目录不存在时自动创建。"""
        tgz = tmp_path / "ext.tgz"
        _make_tgz(str(tgz), {"postgis.control": b"[ext]"})
        sha = tmp_path / "ext.sha256"
        sha.write_bytes(b"sig")
        dest = tmp_path / "deeply" / "nested" / "dest.tar"

        build_tar_package(str(tgz), str(sha), str(dest))

        assert dest.exists()


@pytest.mark.unit
class TestUpdateLocalIndex:
    def test_public_key_none_omits_publicKey_field(self, tmp_path):
        """public_key=None 时 publisher 条目不含 publicKey 字段。"""
        update_local_index(
            repo_dir=tmp_path,
            ext_name="postgis",
            publisher_name="custom-pub",
            version="3.4",
            channel="stable",
            flavor="pg",
            pg_version="16.4",
            arch="x86_64",
            os_name="linux",
            build_num=None,
            package_path="custom-pub/x86_64/linux/postgis-3.4-pg16.4.tar",
            public_key=None,
        )
        index = json.loads((tmp_path / "v2" / "index.json").read_text())
        pub = index["publishers"][0]
        assert "publicKey" not in pub
        assert pub["id"] == "custom-pub"

    def test_new_publisher_with_public_key(self, tmp_path):
        """public_key 非空 + 新 publisher → 创建条目并写入 publicKey。"""
        pubkey = "-----BEGIN PUBLIC KEY-----\nfake\n-----END PUBLIC KEY-----"
        update_local_index(
            repo_dir=tmp_path,
            ext_name="postgis",
            publisher_name="custom-pub",
            version="3.4",
            channel="stable",
            flavor="pg",
            pg_version="16.4",
            arch="x86_64",
            os_name="linux",
            build_num=None,
            package_path="custom-pub/x86_64/linux/postgis-3.4-pg16.4.tar",
            public_key=pubkey,
        )
        index = json.loads((tmp_path / "v2" / "index.json").read_text())
        assert index["publishers"][0]["publicKey"] == pubkey

    def test_existing_publisher_updates_different_key(self, tmp_path):
        """public_key 非空 + 已存在且不同 → 更新 publicKey。"""
        old_key = "-----BEGIN PUBLIC KEY-----\nold\n-----END PUBLIC KEY-----"
        new_key = "-----BEGIN PUBLIC KEY-----\nnew\n-----END PUBLIC KEY-----"
        # 第一次写入
        update_local_index(
            repo_dir=tmp_path, ext_name="ext1", publisher_name="pub",
            version="1.0", channel="stable", flavor="pg", pg_version="16.4",
            arch="x86_64", os_name="linux", build_num=None,
            package_path="pub/x86_64/linux/ext1-1.0-pg16.4.tar",
            public_key=old_key,
        )
        # 第二次写入不同 key
        update_local_index(
            repo_dir=tmp_path, ext_name="ext2", publisher_name="pub",
            version="1.0", channel="stable", flavor="pg", pg_version="16.4",
            arch="x86_64", os_name="linux", build_num=None,
            package_path="pub/x86_64/linux/ext2-1.0-pg16.4.tar",
            public_key=new_key,
        )
        index = json.loads((tmp_path / "v2" / "index.json").read_text())
        assert len(index["publishers"]) == 1
        assert index["publishers"][0]["publicKey"] == new_key

    def test_existing_publisher_same_key_no_change(self, tmp_path):
        """public_key 非空 + 已存在且相同 → 不更新。"""
        pubkey = "-----BEGIN PUBLIC KEY-----\nsame\n-----END PUBLIC KEY-----"
        update_local_index(
            repo_dir=tmp_path, ext_name="ext1", publisher_name="pub",
            version="1.0", channel="stable", flavor="pg", pg_version="16.4",
            arch="x86_64", os_name="linux", build_num=None,
            package_path="pub/x86_64/linux/ext1-1.0-pg16.4.tar",
            public_key=pubkey,
        )
        update_local_index(
            repo_dir=tmp_path, ext_name="ext2", publisher_name="pub",
            version="2.0", channel="stable", flavor="pg", pg_version="16.4",
            arch="x86_64", os_name="linux", build_num=None,
            package_path="pub/x86_64/linux/ext2-2.0-pg16.4.tar",
            public_key=pubkey,
        )
        index = json.loads((tmp_path / "v2" / "index.json").read_text())
        assert index["publishers"][0]["publicKey"] == pubkey
        assert len(index["extensions"]) == 2

    def test_availableFor_dedup(self, tmp_path):
        """相同 (flavor, pg, arch, os) 组合不重复添加。"""
        for _ in range(2):
            update_local_index(
                repo_dir=tmp_path, ext_name="ext", publisher_name="pub",
                version="1.0", channel="stable", flavor="pg", pg_version="16.4",
                arch="x86_64", os_name="linux", build_num=None,
                package_path="pub/x86_64/linux/ext-1.0-pg16.4.tar",
                public_key=None,
            )
        index = json.loads((tmp_path / "v2" / "index.json").read_text())
        ext = index["extensions"][0]
        assert len(ext["versions"][0]["availableFor"]) == 1

    def test_channels_updated(self, tmp_path):
        """channels[channel] = version 被写入。"""
        update_local_index(
            repo_dir=tmp_path, ext_name="ext", publisher_name="pub",
            version="3.4", channel="stable", flavor="pg", pg_version="16.4",
            arch="x86_64", os_name="linux", build_num=None,
            package_path="pub/x86_64/linux/ext-3.4-pg16.4.tar",
            public_key=None,
        )
        index = json.loads((tmp_path / "v2" / "index.json").read_text())
        ext = index["extensions"][0]
        assert ext["channels"]["stable"] == "3.4"
```

- [ ] **Step 2: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_publish_service.py -v --no-cov`
Expected: PASS (12 tests)

- [ ] **Step 3: Commit**

```bash
git add tests/unit/test_publish_service.py
git commit -m "test: add unit tests for publish_service pure functions"
```

---

## Task 3: 集成测试 fixture 扩展

**Files:**
- Modify: `tests/integration/conftest.py` (追加 fixture)

**Files to read for context:**
- `tests/integration/conftest.py` — 现有 db_engine / db_session / client fixture
- `app/config.py:105-118` — Settings 的 repo_dir 属性

- [ ] **Step 1: Add new fixtures to conftest**

在 `tests/integration/conftest.py` 末尾追加：

```python
# ─── Phase 2 新增 fixture ─────────────────────────────────────
from types import SimpleNamespace


@pytest_asyncio.fixture
def repo_dir(tmp_path):
    """临时仓库根目录，替代 settings.repo_dir。"""
    d = tmp_path / "repo"
    d.mkdir()
    return d


@pytest.fixture
def test_config(repo_dir):
    """测试用配置对象（SimpleNamespace，避免触发 Settings 校验）。

    sync_engine / proxy_engine 构造函数只读 repo_dir / proxy_mode /
    sync_download_timeout / sync_concurrency 字段。
    """
    return SimpleNamespace(
        repo_dir=repo_dir,
        proxy_mode="hybrid",
        sync_download_timeout=10,
        sync_concurrency=4,
        upstream_repo_url="https://upstream.test/repo",
    )


@pytest.fixture
def patch_publish_settings(monkeypatch, repo_dir):
    """monkeypatch publish_service 的全局 settings.repo_dir。

    publish_extension 直接读 settings.repo_dir（未通过参数注入），
    测试时需 patch 到临时目录。
    """
    from app.services import publish_service
    monkeypatch.setattr(publish_service.settings, "repo_dir", repo_dir)
    yield
```

- [ ] **Step 2: Verify fixtures load without error**

Run: `python -m pytest tests/integration/ --collect-only --no-cov -q | head -5`
Expected: No errors, existing tests still collected

- [ ] **Step 3: Commit**

```bash
git add tests/integration/conftest.py
git commit -m "test: add repo_dir/test_config/patch_publish_settings fixtures"
```

---

## Task 4: sync_engine 集成测试（dry-run + cleanup）

**Files:**
- Create: `tests/integration/test_sync_engine.py`

**Files to read for context:**
- `app/services/sync_engine.py:132-284` — `_execute` 实现
- `app/services/sync_engine.py:753-829` — `_cleanup_removed_packages` / `_uncache_build`
- `app/models/` — `RepositorySource`, `SyncTask`, `Publisher`, `ExtensionBuild` 模型字段

- [ ] **Step 1: Write the failing tests**

Create `tests/integration/test_sync_engine.py`:

```python
# -*- coding: utf-8 -*-
"""sync_engine 集成测试：dry-run 模式 + _cleanup_removed_packages。"""
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.models import ExtensionBuild, Publisher, RepositorySource, SyncTask
from app.services.sync_engine import SyncEngine


def _make_index_data():
    """构造测试用 index.json 数据。"""
    return {
        "extensions": [
            {
                "name": "postgis",
                "publisher": "com.ongres",
                "versions": [
                    {
                        "version": "3.4",
                        "availableFor": [
                            {
                                "flavor": "pg",
                                "postgresVersion": "16.4",
                                "arch": "x86_64",
                                "os": "linux",
                                "build": None,
                            }
                        ],
                    }
                ],
            }
        ]
    }


async def _setup_source(db_session):
    """在内存 DB 创建 RepositorySource，返回 source_id。"""
    source = RepositorySource(name="test-source", url="https://upstream.test/repo")
    db_session.add(source)
    await db_session.commit()
    await db_session.refresh(source)
    return source.id


@pytest.mark.integration
class TestDryRun:
    async def test_dry_run_reports_packages_without_downloading(
        self, db_engine, db_session, repo_dir, test_config, monkeypatch
    ):
        """dry-run 模式：标记 diff_summary，不下载文件，不清理。"""
        source_id = await _setup_source(db_session)

        # 构造 SyncEngine，session_factory 指向内存库
        from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession
        factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
        engine = SyncEngine(session_factory=factory, config=test_config)

        # patch _fetch_index 返回固定 index
        async def fake_fetch_index(source_url):
            return _make_index_data()
        monkeypatch.setattr(engine, "_fetch_index", fake_fetch_index)

        # patch _get_filters 返回固定 filters（绕过白名单查询）
        async def fake_get_filters(session, policy_id):
            return {"extensions": {"include": ["postgis"], "exclude": []}}
        monkeypatch.setattr(engine, "_get_filters", fake_get_filters)

        # 直接调 _execute（绕过 run 的 asyncio.create_task）
        # 需要先创建 SyncTask 记录
        task = SyncTask(
            source_id=source_id,
            status="running",
            started_at=datetime.now(timezone.utc),
        )
        db_session.add(task)
        await db_session.commit()
        await db_session.refresh(task)

        await engine._execute(task.id, source_id, policy_id=None, dry_run=True)

        # 验证 SyncTask 被更新
        await db_session.refresh(task)
        assert task.status == "completed"
        assert task.diff_summary["dry_run"] is True
        assert task.diff_summary["packages"] == 1
        assert task.diff_summary["removed"] == 0
        assert task.finished_at is not None

        # 验证 repo_dir 下无下载文件
        assert list(repo_dir.rglob("*.tar")) == []


@pytest.mark.integration
class TestCleanupRemovedPackages:
    async def test_removes_files_not_in_upstream(
        self, db_engine, db_session, repo_dir, test_config
    ):
        """上游已移除的 .tar 文件被删除，ExtensionBuild.cached=False。"""
        from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession
        factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
        engine = SyncEngine(session_factory=factory, config=test_config)

        # 插入非自定义 Publisher
        publisher = Publisher(name="com.ongres", is_custom=False)
        db_session.add(publisher)
        await db_session.commit()
        await db_session.refresh(publisher)

        # 在 repo_dir 下造两个 .tar 文件
        keep_path = repo_dir / "com.ongres" / "x86_64" / "linux" / "keep-1.0-pg16.4.tar"
        keep_path.parent.mkdir(parents=True)
        keep_path.write_bytes(b"keep")
        keep_rel = "com.ongres/x86_64/linux/keep-1.0-pg16.4.tar"

        remove_path = repo_dir / "com.ongres" / "x86_64" / "linux" / "gone-1.0-pg16.4.tar"
        remove_path.write_bytes(b"gone")
        remove_rel = "com.ongres/x86_64/linux/gone-1.0-pg16.4.tar"

        # 插入对应 ExtensionBuild 记录（cached=True）
        from app.models import Extension, ExtensionVersion
        ext = Extension(name="gone", publisher_id=publisher.id)
        db_session.add(ext)
        await db_session.flush()
        ver = ExtensionVersion(extension_id=ext.id, version="1.0")
        db_session.add(ver)
        await db_session.flush()
        build = ExtensionBuild(
            version_id=ver.id,
            postgres_version="16.4",
            arch="x86_64",
            os="linux",
            flavor="pg",
            package_path=remove_rel,
            cached=True,
        )
        db_session.add(build)
        await db_session.commit()

        # 上游 packages 只包含 keep
        upstream_packages = [{"local_path": keep_rel}]

        removed = await engine._cleanup_removed_packages(upstream_packages, source_id="any")

        assert removed == 1
        assert not remove_path.exists()
        assert keep_path.exists()

        # 验证 ExtensionBuild.cached 被设为 False
        await db_session.refresh(build)
        assert build.cached is False

    async def test_preserves_custom_publisher_files(
        self, db_engine, db_session, repo_dir, test_config
    ):
        """自定义 Publisher 的文件不被清理。"""
        from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession
        factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
        engine = SyncEngine(session_factory=factory, config=test_config)

        # 自定义 Publisher
        custom_pub = Publisher(name="my-custom", is_custom=True)
        db_session.add(custom_pub)
        await db_session.commit()

        custom_file = repo_dir / "my-custom" / "x86_64" / "linux" / "ext-1.0-pg16.4.tar"
        custom_file.parent.mkdir(parents=True)
        custom_file.write_bytes(b"custom")

        removed = await engine._cleanup_removed_packages([], source_id="any")
        assert removed == 0
        assert custom_file.exists()
```

- [ ] **Step 2: Run tests to verify they pass**

Run: `python -m pytest tests/integration/test_sync_engine.py -v --no-cov`
Expected: PASS (3 tests)

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_sync_engine.py
git commit -m "test: add integration tests for sync_engine dry-run and cleanup"
```

---

## Task 5: proxy_engine 服务层集成测试

**Files:**
- Create: `tests/integration/test_proxy_engine.py`

**Files to read for context:**
- `app/services/proxy_engine.py:48-174` — `handle_package_request`
- `app/services/proxy_engine.py:238-259` — `_get_upstream_url`
- `app/services/naming.py:40-53` — `get_package_url`

- [ ] **Step 1: Write the failing tests**

Create `tests/integration/test_proxy_engine.py`:

```python
# -*- coding: utf-8 -*-
"""proxy_engine 集成测试：HIT/MISS/NOT_FOUND + 模式切换 + 白名单绕过。"""
import pytest
from aioresponses import aioresponses
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import GlobalWhitelist
from app.services.naming import get_package_url
from app.services.proxy_engine import HIT, MISS, NOT_FOUND, ProxyEngine


def _make_engine(db_engine, test_config):
    """构造测试用 ProxyEngine 实例。"""
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    return ProxyEngine(session_factory=factory, config=test_config)


@pytest.mark.integration
class TestHitMissNotFound:
    async def test_hit_returns_cached_file(self, db_engine, db_session, repo_dir, test_config):
        """本地已有文件 → HIT，不发起 HTTP。"""
        engine = _make_engine(db_engine, test_config)
        # 预置缓存文件
        pkg_path = repo_dir / "com.ongres" / "x86_64" / "linux" / "postgis-3.4-pg16.4.tar"
        pkg_path.parent.mkdir(parents=True)
        pkg_path.write_bytes(b"cached content")

        file_path, status = await engine.handle_package_request(
            "com.ongres", "x86_64", "linux", "postgis-3.4-pg16.4"
        )

        assert status == HIT
        assert file_path is not None
        assert file_path.exists()

    async def test_miss_fetches_from_upstream_and_caches(
        self, db_engine, db_session, repo_dir, test_config, monkeypatch
    ):
        """本地无文件 → 从上游拉取 → MISS。"""
        engine = _make_engine(db_engine, test_config)

        # patch _get_upstream_url 返回固定 URL
        async def fake_get_upstream():
            return "https://upstream.test/repo"
        monkeypatch.setattr(engine, "_get_upstream_url", fake_get_upstream)

        # patch _mark_cached 避免数据库副作用（_mark_cached 需要完整 Extension 链）
        async def fake_mark_cached(publisher, arch, os_name, package_name):
            pass
        monkeypatch.setattr(engine, "_mark_cached", fake_mark_cached)

        upstream_url = get_package_url(
            "https://upstream.test/repo", "com.ongres", "x86_64", "linux", "postgis-3.4-pg16.4"
        )

        with aioresponses() as m:
            m.get(upstream_url, status=200, body=b"downloaded content")
            file_path, status = await engine.handle_package_request(
                "com.ongres", "x86_64", "linux", "postgis-3.4-pg16.4"
            )

        assert status == MISS
        assert file_path is not None
        assert file_path.exists()
        assert file_path.read_bytes() == b"downloaded content"

    async def test_strict_mode_returns_404_without_fetching(
        self, db_engine, db_session, repo_dir, monkeypatch
    ):
        """strict 模式 + 本地无文件 → NOT_FOUND，不发起 HTTP。"""
        from types import SimpleNamespace
        strict_config = SimpleNamespace(
            repo_dir=repo_dir,
            proxy_mode="strict",
            sync_download_timeout=10,
            sync_concurrency=4,
            upstream_repo_url="https://upstream.test/repo",
        )
        engine = _make_engine(db_engine, strict_config)

        # 验证不调用 _get_upstream_url
        call_count = 0
        async def fake_get_upstream():
            nonlocal call_count
            call_count += 1
            return "https://upstream.test/repo"
        monkeypatch.setattr(engine, "_get_upstream_url", fake_get_upstream)

        file_path, status = await engine.handle_package_request(
            "com.ongres", "x86_64", "linux", "postgis-3.4-pg16.4"
        )

        assert status == NOT_FOUND
        assert file_path is None
        assert call_count == 0

    async def test_upstream_404_returns_not_found(
        self, db_engine, db_session, repo_dir, test_config, monkeypatch
    ):
        """上游返回 404 → NOT_FOUND。"""
        engine = _make_engine(db_engine, test_config)
        async def fake_get_upstream():
            return "https://upstream.test/repo"
        monkeypatch.setattr(engine, "_get_upstream_url", fake_get_upstream)

        upstream_url = get_package_url(
            "https://upstream.test/repo", "com.ongres", "x86_64", "linux", "postgis-3.4-pg16.4"
        )

        with aioresponses() as m:
            m.get(upstream_url, status=404)
            file_path, status = await engine.handle_package_request(
                "com.ongres", "x86_64", "linux", "postgis-3.4-pg16.4"
            )

        assert status == NOT_FOUND
        assert file_path is None

    async def test_no_upstream_returns_not_found(
        self, db_engine, db_session, repo_dir, test_config, monkeypatch
    ):
        """无可用上游 → NOT_FOUND。"""
        engine = _make_engine(db_engine, test_config)
        async def fake_get_upstream():
            return None
        monkeypatch.setattr(engine, "_get_upstream_url", fake_get_upstream)

        file_path, status = await engine.handle_package_request(
            "com.ongres", "x86_64", "linux", "postgis-3.4-pg16.4"
        )

        assert status == NOT_FOUND
        assert file_path is None

    async def test_path_traversal_returns_404(
        self, db_engine, db_session, repo_dir, test_config
    ):
        """路径段含 .. → NOT_FOUND（路径遍历防御）。"""
        engine = _make_engine(db_engine, test_config)
        file_path, status = await engine.handle_package_request(
            "..", "x86_64", "linux", "postgis-3.4-pg16.4"
        )
        assert status == NOT_FOUND
        assert file_path is None


@pytest.mark.integration
class TestWhitelistBypass:
    async def test_proxy_does_not_check_whitelist(
        self, db_engine, db_session, repo_dir, test_config, monkeypatch
    ):
        """代理拉取不查 GlobalWhitelist — 空白名单时仍能 MISS 拉取。"""
        # 确认白名单为空
        result = await db_session.execute(select(GlobalWhitelist))
        assert result.scalars().all() == []

        engine = _make_engine(db_engine, test_config)
        async def fake_get_upstream():
            return "https://upstream.test/repo"
        monkeypatch.setattr(engine, "_get_upstream_url", fake_get_upstream)
        async def fake_mark_cached(*args):
            pass
        monkeypatch.setattr(engine, "_mark_cached", fake_mark_cached)

        upstream_url = get_package_url(
            "https://upstream.test/repo", "any-pub", "x86_64", "linux", "anything-1.0-pg16.4"
        )

        with aioresponses() as m:
            m.get(upstream_url, status=200, body=b"data")
            file_path, status = await engine.handle_package_request(
                "any-pub", "x86_64", "linux", "anything-1.0-pg16.4"
            )

        assert status == MISS
        assert file_path.exists()
```

- [ ] **Step 2: Run tests to verify they pass**

Run: `python -m pytest tests/integration/test_proxy_engine.py -v --no-cov`
Expected: PASS (7 tests)

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_proxy_engine.py
git commit -m "test: add integration tests for proxy_engine service layer"
```

---

## Task 6: proxy_engine API 层 X-Cache-Status 测试

**Files:**
- Modify: `tests/integration/test_proxy_engine.py` (追加 TestXCacheStatusHeader 类)

**Files to read for context:**
- `app/main.py:410-440` — `/{publisher}/{arch}/{os}/{package_name}.tar` 路由
- `app/main.py:33` — `proxy_engine` 全局单例导入

- [ ] **Step 1: Add API-layer tests**

在 `tests/integration/test_proxy_engine.py` 末尾追加：

```python
from types import SimpleNamespace

import app.database as db_module
import app.main as main_module
from app.services import proxy_engine as pe_module


@pytest.mark.integration
class TestXCacheStatusHeader:
    """通过 FastAPI client 验证 X-Cache-Status 响应头。

    client fixture 已 patch db_module.async_session_factory 为测试 factory，
    因此直接复用即可。全局单例 app.main.proxy_engine 需在测试内替换。
    """

    def _make_test_proxy(self, repo_dir, proxy_mode="hybrid"):
        """构造测试用 ProxyEngine 实例，复用 client fixture 已 patch 的 session_factory。"""
        config = SimpleNamespace(
            repo_dir=repo_dir,
            proxy_mode=proxy_mode,
            sync_download_timeout=10,
            sync_concurrency=4,
            upstream_repo_url="https://upstream.test/repo",
        )
        return pe_module.ProxyEngine(
            session_factory=db_module.async_session_factory,
            config=config,
        )

    async def test_hit_response_has_x_cache_status_header(
        self, client, repo_dir
    ):
        """HIT 响应包含 X-Cache-Status: HIT 头。"""
        test_proxy = self._make_test_proxy(repo_dir, proxy_mode="hybrid")

        # 预置缓存文件
        pkg_path = repo_dir / "com.ongres" / "x86_64" / "linux" / "postgis-3.4-pg16.4.tar"
        pkg_path.parent.mkdir(parents=True)
        pkg_path.write_bytes(b"cached")

        original_proxy = main_module.proxy_engine
        main_module.proxy_engine = test_proxy
        try:
            resp = await client.get(
                "/com.ongres/x86_64/linux/postgis-3.4-pg16.4.tar"
            )
            assert resp.status_code == 200
            assert resp.headers.get("x-cache-status") == "HIT"
        finally:
            main_module.proxy_engine = original_proxy

    async def test_strict_mode_miss_returns_404_without_header(
        self, client, repo_dir
    ):
        """strict 模式未命中 → 404，无 X-Cache-Status 头。"""
        test_proxy = self._make_test_proxy(repo_dir, proxy_mode="strict")

        original_proxy = main_module.proxy_engine
        main_module.proxy_engine = test_proxy
        try:
            resp = await client.get(
                "/com.ongres/x86_64/linux/nonexistent-1.0-pg16.4.tar"
            )
            assert resp.status_code == 404
            assert "x-cache-status" not in resp.headers
        finally:
            main_module.proxy_engine = original_proxy
```

- [ ] **Step 2: Run tests to verify they pass**

Run: `python -m pytest tests/integration/test_proxy_engine.py::TestXCacheStatusHeader -v --no-cov`
Expected: PASS (2 tests)

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_proxy_engine.py
git commit -m "test: add API-layer X-Cache-Status header tests for proxy_engine"
```

---

## Task 7: publish_service 集成测试

**Files:**
- Create: `tests/integration/test_publish_service.py`

**Files to read for context:**
- `app/services/publish_service.py:176-431` — `publish_extension` / `create_custom_publisher`
- `app/services/crypto_service.py:78-102` — `generate_key_pair`
- `app/services/crypto_service.py:147-155` — `get_system_password`

- [ ] **Step 1: Write the failing tests**

Create `tests/integration/test_publish_service.py`:

```python
# -*- coding: utf-8 -*-
"""publish_service 集成测试：create_custom_publisher + publish_extension 端到端。"""
import io
import os
import tarfile

import pytest
from sqlalchemy import select

from app.models import Extension, ExtensionBuild, ExtensionVersion, Publisher
from app.services.publish_service import create_custom_publisher, publish_extension


def _make_valid_tgz(path):
    """构造含 .control 文件的合法 .tgz。"""
    with tarfile.open(path, "w:gz") as tf:
        info = tarfile.TarInfo(name="postgis.control")
        content = b"[extension]\nname=postgis\n"
        info.size = len(content)
        tf.addfile(info, io.BytesIO(content))


@pytest.mark.integration
class TestCreateCustomPublisher:
    async def test_creates_publisher_with_rsa_keypair(self, db_session):
        """创建自定义 Publisher，含 RSA 公钥和加密私钥。"""
        publisher = await create_custom_publisher(db_session, name="my-pub")

        assert publisher.id is not None
        assert publisher.name == "my-pub"
        assert publisher.display_name == "my-pub"
        assert publisher.is_custom is True
        # 公钥是合法 PEM
        assert "BEGIN PUBLIC KEY" in publisher.public_key
        # 私钥是加密后的密文（非明文 PEM）
        assert "BEGIN PRIVATE KEY" not in publisher.private_key
        assert publisher.private_key != publisher.public_key

    async def test_display_name_defaults_to_name(self, db_session):
        """display_name 默认为 name。"""
        publisher = await create_custom_publisher(
            db_session, name="another", display_name="Display Name"
        )
        assert publisher.display_name == "Display Name"


@pytest.mark.integration
class TestPublishExtension:
    async def test_successful_publish_creates_all_records(
        self, db_session, repo_dir, patch_publish_settings
    ):
        """完整发布流程：生成 .tar + 更新 index.json + 创建 DB 记录。"""
        # 1. 创建 publisher
        publisher = await create_custom_publisher(db_session, name="custom-pub")

        # 2. 构造合法 .tgz
        tgz_path = str(repo_dir / "upload.tgz")
        _make_valid_tgz(tgz_path)

        # 3. 发布
        result = await publish_extension(
            session=db_session,
            publisher_id=publisher.id,
            tgz_path=tgz_path,
            ext_name="postgis",
            version="3.4",
            flavor="pg",
            pg_version="16.4",
            arch="x86_64",
            os_name="linux",
            build_num=None,
        )

        assert result["success"] is True
        assert result["package_path"]
        assert os.path.exists(result["package_path"])

        # 4. 验证 index.json 更新
        import json
        index = json.loads((repo_dir / "v2" / "index.json").read_text())
        assert any(p["id"] == "custom-pub" for p in index["publishers"])
        ext = next(e for e in index["extensions"] if e["name"] == "postgis")
        assert ext["versions"][0]["version"] == "3.4"

        # 5. 验证 DB 记录
        ext_result = await db_session.execute(
            select(Extension).where(Extension.name == "postgis")
        )
        ext = ext_result.scalar_one()
        assert ext.is_custom is True

        ver_result = await db_session.execute(
            select(ExtensionVersion).where(ExtensionVersion.extension_id == ext.id)
        )
        ver = ver_result.scalar_one()
        assert ver.version == "3.4"

        build_result = await db_session.execute(
            select(ExtensionBuild).where(ExtensionBuild.version_id == ver.id)
        )
        build = build_result.scalar_one()
        assert build.cached is True
        assert build.verified is True
        assert build.package_path.endswith("postgis-3.4-pg16.4.tar")

    async def test_invalid_tgz_returns_failure(self, db_session, repo_dir, patch_publish_settings):
        """tgz 校验失败 → 返回 success=False。"""
        publisher = await create_custom_publisher(db_session, name="bad-pub")

        # 构造不含 .control 的 tgz
        bad_tgz = str(repo_dir / "bad.tgz")
        with tarfile.open(bad_tgz, "w:gz") as tf:
            info = tarfile.TarInfo(name="README.md")
            content = b"no control file"
            info.size = len(content)
            tf.addfile(info, io.BytesIO(content))

        result = await publish_extension(
            session=db_session,
            publisher_id=publisher.id,
            tgz_path=bad_tgz,
            ext_name="bad",
            version="1.0",
            flavor="pg",
            pg_version="16.4",
            arch="x86_64",
            os_name="linux",
            build_num=None,
        )

        assert result["success"] is False
        assert "未找到 .control" in result["error"]

    async def test_nonexistent_publisher_returns_failure(
        self, db_session, repo_dir, patch_publish_settings
    ):
        """publisher 不存在 → 返回 success=False。"""
        tgz_path = str(repo_dir / "ext.tgz")
        _make_valid_tgz(tgz_path)

        result = await publish_extension(
            session=db_session,
            publisher_id="nonexistent-uuid",
            tgz_path=tgz_path,
            ext_name="ext",
            version="1.0",
            flavor="pg",
            pg_version="16.4",
            arch="x86_64",
            os_name="linux",
            build_num=None,
        )

        assert result["success"] is False
        assert "发布者不存在" in result["error"]
```

- [ ] **Step 2: Run tests to verify they pass**

Run: `python -m pytest tests/integration/test_publish_service.py -v --no-cov`
Expected: PASS (5 tests)

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_publish_service.py
git commit -m "test: add integration tests for publish_service"
```

---

## Task 8: 全量运行 + 覆盖率验证

- [ ] **Step 1: Run full test suite**

Run: `python -m pytest -v`
Expected: All tests pass (Phase 1: 48 + Phase 2: ~34 = ~82 tests)

- [ ] **Step 2: Check coverage for engine modules**

Run: `python -m pytest --cov=app --cov-report=term-missing -q`
Expected:
- `app/services/sync_engine.py` 覆盖率 60%+
- `app/services/proxy_engine.py` 覆盖率 70%+
- `app/services/publish_service.py` 覆盖率 60%+
- 整体覆盖率较 Phase 1 (36%) 有明显提升

- [ ] **Step 3: Commit final state**

```bash
git add -A
git commit -m "test: Phase 2 complete — engine core test coverage"
```

---

## Self-Review Notes

**Spec coverage check:**
- ✅ sync_engine `_collect_packages` 过滤/去重 — Task 1
- ✅ sync_engine dry-run — Task 4
- ✅ sync_engine `_cleanup_removed_packages` — Task 4
- ✅ proxy_engine HIT/MISS/NOT_FOUND — Task 5
- ✅ proxy_engine strict 模式 — Task 5
- ✅ proxy_engine 白名单绕过 — Task 5
- ✅ proxy_engine X-Cache-Status 头 — Task 6
- ✅ publish_service `validate_tgz` — Task 2
- ✅ publish_service `build_tar_package` — Task 2
- ✅ publish_service `update_local_index` 三分支 — Task 2
- ✅ publish_service `create_custom_publisher` — Task 7
- ✅ publish_service `publish_extension` 端到端 — Task 7

**Known risks:**
- `aioresponses` 对 200 + body 场景兼容性良好（基础 MISS 路径不涉及 Range/206）
