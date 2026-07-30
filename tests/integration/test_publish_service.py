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
