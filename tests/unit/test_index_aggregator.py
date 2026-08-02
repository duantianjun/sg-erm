# -*- coding: utf-8 -*-
"""index_aggregator 单元测试。

覆盖：
- 纯函数: _target_key / _merge_into / _merge_version / _write_json
- 异步函数: aggregate_indices / build_aggregated_index（mock DB / 文件系统）

注意：_merge_into / _merge_version 内部用 _priority 字段判断优先级。
_merge_into 结束时会从 extension 顶层字段剥离 _priority，但 versions 内的
_priority 保留。因此测试优先级覆盖逻辑时，通过手动构造带 _priority 的
merged 字典来正确验证（而非依赖两次 _merge_into 调用间已丢失的 _priority）。
"""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import *  # noqa: F401,F403  注册所有模型到 Base.metadata
from app.models import RepositorySource
from app.services import index_aggregator
from app.services.index_aggregator import (
    _merge_into,
    _merge_version,
    _target_key,
    _write_json,
    aggregate_indices,
    build_aggregated_index,
)


# ─── 测试数据工厂 ──────────────────────────────────────────────
def _make_target(arch="x86_64", os="linux", flavor="pg", pg="16.4", build="1"):
    """构造单个 availableFor 条目。"""
    return {
        "arch": arch,
        "os": os,
        "flavor": flavor,
        "postgresVersion": pg,
        "build": build,
    }


def _make_version(version, available_for):
    """构造单个 version 条目。"""
    return {"version": version, "availableFor": available_for}


def _make_publisher(pub_id, name=None, public_key=""):
    """构造单个 publisher 条目。"""
    return {
        "id": pub_id,
        "name": name or pub_id,
        "publicKey": public_key,
    }


def _make_ext(name, publisher_id="pub1", description="", versions=None, **meta):
    """构造单个 extension 条目。"""
    ext = {
        "name": name,
        "publisher": {"id": publisher_id},
        "description": description,
        "abstract": "",
        "tags": [],
        "url": "",
        "source": "",
        "license": "",
        "channels": {},
        "versions": versions or [],
    }
    ext.update(meta)
    return ext


def _make_source(priority=100, name="src", url="https://x.test/repo", source_id="src-1"):
    """构造轻量 source 对象（仅暴露 _merge_into 用到的 priority/name 字段）。"""
    return SimpleNamespace(id=source_id, name=name, url=url, priority=priority)


def _empty_merged():
    """构造空聚合结果。"""
    return {"publishers": [], "extensions": []}


# ─── 内存 DB fixture ───────────────────────────────────────────
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
async def db_factory(db_engine):
    """基于内存引擎的 session factory（用于 patch async_session_factory）。"""
    return async_sessionmaker(
        db_engine, class_=AsyncSession, expire_on_commit=False
    )


# ─── 1. _target_key ────────────────────────────────────────────
@pytest.mark.unit
class TestTargetKey:
    def test_normal_input(self):
        """正常输入 → 'arch_os_flavor_pgVersion_build' 格式。"""
        target = _make_target()
        assert _target_key(target) == "x86_64_linux_pg_16.4_1"

    def test_empty_dict(self):
        """空字典 → '____'（5 个空串用 _ 连接）。"""
        assert _target_key({}) == "____"

    def test_partial_fields_missing(self):
        """部分字段缺失 → 缺失字段视为空串。"""
        target = {"arch": "x86_64", "os": "linux"}
        # flavor/postgresVersion/build 缺失 → 空串
        assert _target_key(target) == "x86_64_linux___"

    def test_all_fields_present_custom_values(self):
        """自定义字段值 → 拼接顺序为 arch_os_flavor_pgVersion_build。"""
        target = {
            "arch": "arm64",
            "os": "darwin",
            "flavor": "bf",
            "postgresVersion": "15.2",
            "build": "3",
        }
        assert _target_key(target) == "arm64_darwin_bf_15.2_3"


# ─── 2. _merge_into ────────────────────────────────────────────
@pytest.mark.unit
class TestMergeInto:
    def test_merge_new_publisher(self):
        """合并新 publisher → 添加到 merged['publishers']，内部字段不外泄。"""
        merged = _empty_merged()
        source_index = {
            "publishers": [_make_publisher("pub1", name="Pub 1", public_key="key1")],
            "extensions": [],
        }
        _merge_into(merged, source_index, _make_source(priority=100, name="src1"))

        assert len(merged["publishers"]) == 1
        pub = merged["publishers"][0]
        assert pub["id"] == "pub1"
        assert pub["name"] == "Pub 1"
        assert pub["publicKey"] == "key1"
        # 内部 _priority / _source_name 字段不应外泄
        assert "_priority" not in pub
        assert "_source_name" not in pub

    def test_high_priority_overrides_public_key(self):
        """同 publisher_id + 高优先级 → publicKey 被覆盖。"""
        # 模拟低优先级源已合并（保留 _priority 内部字段）
        merged = {
            "publishers": [{
                "id": "pub1", "name": "Pub 1", "publicKey": "low_key",
                "_priority": 200, "_source_name": "low",
            }],
            "extensions": [],
        }
        source_index = {
            "publishers": [_make_publisher("pub1", public_key="high_key")],
            "extensions": [],
        }
        _merge_into(merged, source_index, _make_source(priority=10, name="high"))

        pub = merged["publishers"][0]
        assert pub["publicKey"] == "high_key"

    def test_low_priority_does_not_override_public_key(self):
        """同 publisher_id + 低优先级 → publicKey 不被覆盖。"""
        merged = {
            "publishers": [{
                "id": "pub1", "name": "Pub 1", "publicKey": "high_key",
                "_priority": 10, "_source_name": "high",
            }],
            "extensions": [],
        }
        source_index = {
            "publishers": [_make_publisher("pub1", public_key="low_key")],
            "extensions": [],
        }
        _merge_into(merged, source_index, _make_source(priority=200, name="low"))

        pub = merged["publishers"][0]
        assert pub["publicKey"] == "high_key"

    def test_merge_new_extension(self):
        """合并新 extension → 添加到 merged['extensions']，内部字段不外泄。"""
        merged = _empty_merged()
        source_index = {
            "publishers": [],
            "extensions": [_make_ext(
                "postgis", description="PostGIS extension",
                versions=[_make_version("3.4.0", [_make_target()])],
            )],
        }
        _merge_into(merged, source_index, _make_source(priority=100, name="src1"))

        assert len(merged["extensions"]) == 1
        ext = merged["extensions"][0]
        assert ext["name"] == "postgis"
        assert ext["description"] == "PostGIS extension"
        assert len(ext["versions"]) == 1
        assert ext["versions"][0]["version"] == "3.4.0"
        # 内部字段不应外泄
        for k in ext:
            assert not k.startswith("_")

    def test_high_priority_overrides_extension_metadata(self):
        """同名 extension + 高优先级 → description 等元数据被覆盖。"""
        merged = {
            "publishers": [],
            "extensions": [{
                "name": "postgis", "publisher": {"id": "pub1"},
                "description": "low desc", "abstract": "", "tags": [],
                "url": "", "source": "", "license": "", "channels": {},
                "versions": [], "_priority": 200, "_source_name": "low",
            }],
        }
        source_index = {
            "publishers": [],
            "extensions": [_make_ext(
                "postgis", description="high desc", tags=["geo"],
            )],
        }
        _merge_into(merged, source_index, _make_source(priority=10, name="high"))

        ext = merged["extensions"][0]
        assert ext["description"] == "high desc"
        assert ext["tags"] == ["geo"]

    def test_low_priority_does_not_override_metadata(self):
        """同名 extension + 低优先级 → 元数据不被覆盖。"""
        merged = {
            "publishers": [],
            "extensions": [{
                "name": "postgis", "publisher": {"id": "pub1"},
                "description": "high desc", "abstract": "", "tags": ["geo"],
                "url": "", "source": "", "license": "", "channels": {},
                "versions": [], "_priority": 10, "_source_name": "high",
            }],
        }
        source_index = {
            "publishers": [],
            "extensions": [_make_ext(
                "postgis", description="low desc", tags=["other"],
            )],
        }
        _merge_into(merged, source_index, _make_source(priority=200, name="low"))

        ext = merged["extensions"][0]
        assert ext["description"] == "high desc"
        assert ext["tags"] == ["geo"]

    def test_same_name_extension_versions_deep_merged(self):
        """同名 extension 不同版本 → 所有版本保留（深度合并）。"""
        merged = _empty_merged()
        idx_a = {
            "publishers": [],
            "extensions": [_make_ext("postgis", versions=[
                _make_version("3.4.0", [_make_target(build="1")])
            ])],
        }
        idx_b = {
            "publishers": [],
            "extensions": [_make_ext("postgis", versions=[
                _make_version("3.5.0", [_make_target(build="1")])
            ])],
        }
        _merge_into(merged, idx_a, _make_source(priority=100, name="a"))
        _merge_into(merged, idx_b, _make_source(priority=100, name="b"))

        assert len(merged["extensions"]) == 1
        ext = merged["extensions"][0]
        versions = {v["version"] for v in ext["versions"]}
        assert versions == {"3.4.0", "3.5.0"}

    def test_publisher_without_id_skipped(self):
        """publisher 缺少 id → 跳过。"""
        merged = _empty_merged()
        source_index = {
            "publishers": [{"name": "no-id", "publicKey": "k"}],
            "extensions": [],
        }
        _merge_into(merged, source_index, _make_source())
        assert merged["publishers"] == []

    def test_extension_without_name_skipped(self):
        """extension 缺少 name → 跳过。"""
        merged = _empty_merged()
        source_index = {
            "publishers": [],
            "extensions": [{"description": "no name"}],
        }
        _merge_into(merged, source_index, _make_source())
        assert merged["extensions"] == []


# ─── 3. _merge_version ─────────────────────────────────────────
@pytest.mark.unit
class TestMergeVersion:
    def test_new_version_appended(self):
        """新版本 → 直接添加到 versions 列表。"""
        ext_entry = {"versions": []}
        _merge_version(ext_entry, _make_version("3.4.0", [_make_target()]), 100)

        assert len(ext_entry["versions"]) == 1
        assert ext_entry["versions"][0]["version"] == "3.4.0"
        assert len(ext_entry["versions"][0]["availableFor"]) == 1
        assert ext_entry["versions"][0]["_priority"] == 100

    def test_same_version_same_target_high_priority_overrides(self):
        """同版本号同 target + 高优先级 → 覆盖已有 target 内容。"""
        ext_entry = {"versions": []}
        # 低优先级先添加（target 带 url 额外字段便于观察覆盖）
        target_low = {**_make_target(), "url": "low_url"}
        target_high = {**_make_target(), "url": "high_url"}

        _merge_version(ext_entry, _make_version("3.4.0", [target_low]), 200)
        _merge_version(ext_entry, _make_version("3.4.0", [target_high]), 10)

        assert len(ext_entry["versions"]) == 1
        ver = ext_entry["versions"][0]
        # target 不重复
        assert len(ver["availableFor"]) == 1
        # 内容被高优先级覆盖
        assert ver["availableFor"][0]["url"] == "high_url"
        # 版本 _priority 更新为高优先级
        assert ver["_priority"] == 10

    def test_same_version_same_target_low_priority_no_override(self):
        """同版本号同 target + 低优先级 → 不覆盖，_priority 不变。"""
        ext_entry = {"versions": []}
        target_high = {**_make_target(), "url": "high_url"}
        target_low = {**_make_target(), "url": "low_url"}

        _merge_version(ext_entry, _make_version("3.4.0", [target_high]), 10)
        _merge_version(ext_entry, _make_version("3.4.0", [target_low]), 200)

        ver = ext_entry["versions"][0]
        assert len(ver["availableFor"]) == 1
        assert ver["availableFor"][0]["url"] == "high_url"
        assert ver["_priority"] == 10

    def test_same_version_different_target_all_kept(self):
        """同版本号不同 target → 全部保留。"""
        ext_entry = {"versions": []}
        _merge_version(
            ext_entry,
            _make_version("3.4.0", [_make_target(arch="x86_64")]),
            100,
        )
        _merge_version(
            ext_entry,
            _make_version("3.4.0", [_make_target(arch="arm64")]),
            100,
        )

        assert len(ext_entry["versions"]) == 1
        ver = ext_entry["versions"][0]
        assert len(ver["availableFor"]) == 2
        archs = {t["arch"] for t in ver["availableFor"]}
        assert archs == {"x86_64", "arm64"}

    def test_empty_available_for_handled(self):
        """availableFor 为空 → 版本仍添加，availableFor 为空列表。"""
        ext_entry = {"versions": []}
        _merge_version(ext_entry, {"version": "1.0", "availableFor": []}, 100)

        assert len(ext_entry["versions"]) == 1
        assert ext_entry["versions"][0]["availableFor"] == []


# ─── 4. aggregate_indices ──────────────────────────────────────
@pytest.mark.unit
class TestAggregateIndices:
    async def test_no_enabled_sources_returns_empty(self, db_factory):
        """无启用源 → 返回空结构 {'publishers': [], 'extensions': []}。"""
        with patch.object(index_aggregator, "async_session_factory", db_factory):
            result = await aggregate_indices()

        assert result == {"publishers": [], "extensions": []}

    async def test_aggregates_multiple_sources(self, db_factory):
        """mock _fetch_source_index 返回数据 → 正确聚合两个源。"""
        async with db_factory() as session:
            session.add(RepositorySource(
                id="s1", name="src1", url="https://a.test/repo",
                priority=10, enabled=True,
            ))
            session.add(RepositorySource(
                id="s2", name="src2", url="https://b.test/repo",
                priority=20, enabled=True,
            ))
            await session.commit()

        idx1 = {
            "publishers": [_make_publisher("pub1", name="Pub 1", public_key="k1")],
            "extensions": [_make_ext(
                "postgis", description="PostGIS",
                versions=[_make_version("3.4.0", [_make_target()])],
            )],
        }
        idx2 = {
            "publishers": [_make_publisher("pub2", name="Pub 2", public_key="k2")],
            "extensions": [_make_ext(
                "timescaledb", description="TimescaleDB",
                versions=[_make_version("2.0", [_make_target()])],
            )],
        }

        async def fake_fetch(src):
            return {"s1": idx1, "s2": idx2}[src.id]

        with patch.object(index_aggregator, "async_session_factory", db_factory), \
             patch.object(index_aggregator, "_fetch_source_index",
                          AsyncMock(side_effect=fake_fetch)):
            result = await aggregate_indices()

        # 两个 publisher 都被聚合
        pub_ids = {p["id"] for p in result["publishers"]}
        assert pub_ids == {"pub1", "pub2"}
        # 两个 extension 都被聚合
        ext_names = {e["name"] for e in result["extensions"]}
        assert ext_names == {"postgis", "timescaledb"}
        # 两个源状态都是 ok
        statuses = {s["id"]: s["status"] for s in result["sources"]}
        assert statuses == {"s1": "ok", "s2": "ok"}
        # 包含 aggregatedAt 时间戳
        assert "aggregatedAt" in result

    async def test_source_fetch_failure_marks_error(self, db_factory):
        """某源获取失败 → 该源标记 error，其他源正常聚合。"""
        async with db_factory() as session:
            session.add(RepositorySource(
                id="s1", name="ok-src", url="https://a.test/repo",
                priority=10, enabled=True,
            ))
            session.add(RepositorySource(
                id="s2", name="bad-src", url="https://b.test/repo",
                priority=20, enabled=True,
            ))
            await session.commit()

        idx_ok = {
            "publishers": [_make_publisher("pub1", public_key="k1")],
            "extensions": [],
        }

        async def fake_fetch(src):
            if src.id == "s2":
                raise RuntimeError("network error")
            return idx_ok

        with patch.object(index_aggregator, "async_session_factory", db_factory), \
             patch.object(index_aggregator, "_fetch_source_index",
                          AsyncMock(side_effect=fake_fetch)):
            result = await aggregate_indices()

        # 失败源标记 error，成功源标记 ok
        statuses = {s["id"]: s["status"] for s in result["sources"]}
        assert statuses == {"s1": "ok", "s2": "error"}
        bad_src = next(s for s in result["sources"] if s["id"] == "s2")
        assert "network error" in bad_src["error"]
        # 成功源的 publisher 仍被聚合
        assert len(result["publishers"]) == 1
        assert result["publishers"][0]["id"] == "pub1"


# ─── 5. build_aggregated_index ─────────────────────────────────
@pytest.mark.unit
class TestBuildAggregatedIndex:
    async def test_writes_index_file(self, tmp_path):
        """mock aggregate_indices + tmp_path → 写入 index.json 文件。"""
        aggregated = {
            "publishers": [_make_publisher("pub1", public_key="k1")],
            "extensions": [],
        }
        fake_settings = SimpleNamespace(repo_dir=tmp_path)

        with patch.object(index_aggregator, "aggregate_indices",
                          AsyncMock(return_value=aggregated)), \
             patch.object(index_aggregator, "settings", fake_settings):
            path = await build_aggregated_index()

        assert path is not None
        assert path.exists()
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["publishers"][0]["id"] == "pub1"
        assert data["publishers"][0]["publicKey"] == "k1"
        # 路径符合 v2/index.json
        assert path.name == "index.json"
        assert path.parent.name == "v2"

    async def test_returns_none_on_failure(self, tmp_path):
        """aggregate_indices 失败 → 返回 None。"""
        fake_settings = SimpleNamespace(repo_dir=tmp_path)

        with patch.object(index_aggregator, "aggregate_indices",
                          AsyncMock(side_effect=RuntimeError("boom"))), \
             patch.object(index_aggregator, "settings", fake_settings):
            path = await build_aggregated_index()

        assert path is None


# ─── 6. _write_json ────────────────────────────────────────────
@pytest.mark.unit
class TestWriteJson:
    def test_writes_valid_json(self, tmp_path):
        """正常写入 → 文件内容与输入一致，中文不被转义。"""
        path = tmp_path / "out.json"
        data = {"a": 1, "b": ["x", "y"], "中文": "测试"}

        _write_json(path, data)

        text = path.read_text(encoding="utf-8")
        assert json.loads(text) == data
        # ensure_ascii=False → 中文直接写入而非 \u 转义
        assert "测试" in text
        assert "\\u" not in text

    def test_overwrites_existing_file(self, tmp_path):
        """已存在文件 → 覆盖旧内容。"""
        path = tmp_path / "out.json"
        path.write_text("old content", encoding="utf-8")

        _write_json(path, {"new": True})

        assert json.loads(path.read_text(encoding="utf-8")) == {"new": True}

    def test_writes_indented_json(self, tmp_path):
        """indent=2 → 输出为多行缩进格式。"""
        path = tmp_path / "out.json"
        _write_json(path, {"a": 1, "b": 2})

        text = path.read_text(encoding="utf-8")
        # 缩进格式应包含换行与两个空格缩进
        assert "\n" in text
        assert '  "a": 1' in text
