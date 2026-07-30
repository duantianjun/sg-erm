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
