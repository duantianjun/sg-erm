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
