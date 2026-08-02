# -*- coding: utf-8 -*-
"""repo_files API 集成测试。

覆盖本地仓库文件浏览、删除、重新下载、SHA256 验证和一致性检查。
"""
import hashlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select

from app.models import (
    AuditLog,
    Extension,
    ExtensionBuild,
    ExtensionVersion,
    Publisher,
    User,
)
from app.services.auth_service import create_access_token, get_password_hash


# ─── 辅助函数 ──────────────────────────────────────────────────

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


def _auth_headers(token):
    return {"Authorization": f"Bearer {token}"}


async def _create_build(
    db_session,
    *,
    ext_name="postgis",
    version="3.4",
    pg_version="16.4",
    arch="x86_64",
    os_name="linux",
    publisher_name="com.ongres",
    flavor="pg",
    build_no="6.51",
    cached=True,
    sha256=None,
    repo_dir=None,
    content=b"package content",
):
    """创建完整链路 Publisher → Extension → ExtensionVersion → ExtensionBuild。

    自动 get-or-create Publisher/Extension/ExtensionVersion，
    保证可重复调用（publisher_name/ext_name/version 可复用）。
    若传入 repo_dir 则同时在磁盘创建文件。
    """
    # get or create publisher
    result = await db_session.execute(
        select(Publisher).where(Publisher.name == publisher_name)
    )
    publisher = result.scalar_one_or_none()
    if not publisher:
        publisher = Publisher(name=publisher_name, display_name=publisher_name)
        db_session.add(publisher)
        await db_session.commit()
        await db_session.refresh(publisher)

    # get or create extension
    result = await db_session.execute(
        select(Extension).where(Extension.name == ext_name)
    )
    ext = result.scalar_one_or_none()
    if not ext:
        ext = Extension(
            name=ext_name,
            description=f"{ext_name} extension",
            publisher_id=publisher.id,
            license="PostgreSQL",
        )
        db_session.add(ext)
        await db_session.commit()
        await db_session.refresh(ext)

    # get or create version
    result = await db_session.execute(
        select(ExtensionVersion).where(
            ExtensionVersion.extension_id == ext.id,
            ExtensionVersion.version == version,
        )
    )
    ver = result.scalar_one_or_none()
    if not ver:
        ver = ExtensionVersion(
            extension_id=ext.id,
            version=version,
            channel="stable",
        )
        db_session.add(ver)
        await db_session.commit()
        await db_session.refresh(ver)

    # 计算 package_path
    base_name = f"{ext_name}-{version}-{flavor}{pg_version}"
    if build_no:
        base_name += f"-build-{build_no}"
    package_path = f"{publisher_name}/{arch}/{os_name}/{base_name}.tar"

    build = ExtensionBuild(
        version_id=ver.id,
        postgres_version=pg_version,
        arch=arch,
        os=os_name,
        flavor=flavor,
        build=build_no,
        package_path=package_path,
        package_size=len(content),
        sha256=sha256,
        cached=cached,
    )
    db_session.add(build)
    await db_session.commit()
    await db_session.refresh(build)

    # 可选：在磁盘创建文件
    if repo_dir is not None:
        file_path = repo_dir / package_path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_bytes(content)

    return publisher, ext, ver, build


def _make_repo_file(repo_dir, package_path, content=b"package content"):
    """在 repo_dir 下创建磁盘文件，返回完整路径。"""
    file_path = repo_dir / package_path
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_bytes(content)
    return file_path


# ─── 包列表 ────────────────────────────────────────────────────

@pytest.mark.integration
class TestListPackages:
    async def test_no_auth_returns_401(self, client):
        """无认证 → 401。"""
        resp = await client.get("/api/v1/repo-files/packages")
        assert resp.status_code == 401

    async def test_empty_database(self, client, db_session):
        """空数据库 → 200, 空列表。"""
        token = await _admin_token(db_session)
        resp = await client.get(
            "/api/v1/repo-files/packages", headers=_auth_headers(token)
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == 0
        assert body["data"] == []
        assert body["count"] == 0

    async def test_list_with_cached_build(
        self, client, db_session, patch_publish_settings, repo_dir
    ):
        """有 cached build → 200, 返回包列表（含 file_exists 字段）。"""
        token = await _admin_token(db_session)
        _, _, _, build = await _create_build(db_session, repo_dir=repo_dir)

        resp = await client.get(
            "/api/v1/repo-files/packages", headers=_auth_headers(token)
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == 0
        assert body["count"] == 1
        item = body["data"][0]
        assert item["build_id"] == build.id
        assert item["publisher"] == "com.ongres"
        assert item["arch"] == "x86_64"
        assert item["os"] == "linux"
        assert item["extension_name"] == "postgis"
        assert item["version"] == "3.4"
        assert item["postgres_version"] == "16.4"
        assert item["flavor"] == "pg"
        assert item["build"] == "6.51"
        assert item["cached"] is True
        assert item["file_exists"] is True

    async def test_filter_by_publisher(
        self, client, db_session, patch_publish_settings, repo_dir
    ):
        """publisher 过滤。"""
        token = await _admin_token(db_session)
        _, _, _, build1 = await _create_build(
            db_session, ext_name="postgis", publisher_name="com.ongres",
            repo_dir=repo_dir,
        )
        _, _, _, build2 = await _create_build(
            db_session, ext_name="pgvector", publisher_name="com.example",
            repo_dir=repo_dir,
        )

        resp = await client.get(
            "/api/v1/repo-files/packages?publisher=com.ongres",
            headers=_auth_headers(token),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 1
        assert body["data"][0]["publisher"] == "com.ongres"
        assert body["data"][0]["build_id"] == build1.id

    async def test_filter_by_arch(
        self, client, db_session, patch_publish_settings, repo_dir
    ):
        """arch 过滤。"""
        token = await _admin_token(db_session)
        _, _, _, build1 = await _create_build(
            db_session, ext_name="postgis", arch="x86_64",
            repo_dir=repo_dir,
        )
        _, _, _, build2 = await _create_build(
            db_session, ext_name="postgis", arch="aarch64",
            repo_dir=repo_dir,
        )

        resp = await client.get(
            "/api/v1/repo-files/packages?arch=x86_64",
            headers=_auth_headers(token),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 1
        assert body["data"][0]["arch"] == "x86_64"
        assert body["data"][0]["build_id"] == build1.id

    async def test_keyword_search(
        self, client, db_session, patch_publish_settings, repo_dir
    ):
        """keyword 搜索（匹配扩展名）。"""
        token = await _admin_token(db_session)
        _, _, _, _ = await _create_build(
            db_session, ext_name="postgis", repo_dir=repo_dir,
        )
        _, _, _, _ = await _create_build(
            db_session, ext_name="pgvector", repo_dir=repo_dir,
        )

        resp = await client.get(
            "/api/v1/repo-files/packages?keyword=postgis",
            headers=_auth_headers(token),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 1
        assert body["data"][0]["extension_name"] == "postgis"


# ─── 目录树 ────────────────────────────────────────────────────

@pytest.mark.integration
class TestGetTree:
    async def test_empty_database(self, client, db_session):
        """空数据库 → 200, 空树。"""
        token = await _admin_token(db_session)
        resp = await client.get(
            "/api/v1/repo-files/tree", headers=_auth_headers(token)
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == 0
        assert body["data"] == []

    async def test_tree_with_data(
        self, client, db_session, patch_publish_settings, repo_dir
    ):
        """有数据 → 200, 三级嵌套结构正确。"""
        token = await _admin_token(db_session)
        await _create_build(
            db_session, ext_name="postgis", arch="x86_64",
            repo_dir=repo_dir,
        )
        await _create_build(
            db_session, ext_name="pgvector", arch="aarch64",
            repo_dir=repo_dir,
        )

        resp = await client.get(
            "/api/v1/repo-files/tree", headers=_auth_headers(token)
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == 0
        # 单 publisher (com.ongres)，两个 arch
        assert len(body["data"]) == 1
        pub_node = body["data"][0]
        assert pub_node["publisher"] == "com.ongres"
        assert pub_node["count"] == 2
        arch_map = {n["arch"]: n for n in pub_node["children"]}
        assert "x86_64" in arch_map
        assert "aarch64" in arch_map
        assert arch_map["x86_64"]["count"] == 1
        assert arch_map["aarch64"]["count"] == 1
        # 每层都有 os 子节点
        for arch_node in pub_node["children"]:
            assert len(arch_node["children"]) == 1
            assert arch_node["children"][0]["os"] == "linux"
            assert arch_node["children"][0]["count"] == 1


# ─── 删除包 ────────────────────────────────────────────────────

@pytest.mark.integration
class TestDeletePackage:
    async def test_build_not_found(
        self, client, db_session, patch_publish_settings
    ):
        """build_id 不存在 → 404。"""
        token = await _admin_token(db_session)
        resp = await client.delete(
            "/api/v1/repo-files/packages/nonexistent-id",
            headers=_auth_headers(token),
        )
        assert resp.status_code == 404

    async def test_file_not_found(
        self, client, db_session, patch_publish_settings, repo_dir
    ):
        """文件不存在 → 404。"""
        token = await _admin_token(db_session)
        _, _, _, build = await _create_build(db_session, repo_dir=None)
        resp = await client.delete(
            f"/api/v1/repo-files/packages/{build.id}",
            headers=_auth_headers(token),
        )
        assert resp.status_code == 404

    async def test_delete_success(
        self, client, db_session, patch_publish_settings, repo_dir
    ):
        """正常删除 → 200, cached=False, AuditLog 写入。"""
        token = await _admin_token(db_session)
        _, _, _, build = await _create_build(
            db_session, repo_dir=repo_dir, content=b"to be deleted"
        )
        file_path = repo_dir / build.package_path
        assert file_path.exists()

        resp = await client.delete(
            f"/api/v1/repo-files/packages/{build.id}",
            headers=_auth_headers(token),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == 0
        assert body["data"][0]["build_id"] == build.id

        # 文件已删除
        assert not file_path.exists()

        # cached 标记已清除
        await db_session.refresh(build)
        assert build.cached is False

        # AuditLog 已写入
        result = await db_session.execute(
            select(AuditLog).where(
                AuditLog.action == "repo_file_delete",
                AuditLog.resource == build.package_path,
            )
        )
        logs = result.scalars().all()
        assert len(logs) == 1
        assert logs[0].actor == "admin"
        assert logs[0].result == "success"


# ─── 重新下载 ──────────────────────────────────────────────────

@pytest.mark.integration
class TestRedownloadPackage:
    async def test_build_not_found(
        self, client, db_session, patch_publish_settings
    ):
        """build_id 不存在 → 404。"""
        token = await _admin_token(db_session)
        resp = await client.post(
            "/api/v1/repo-files/packages/nonexistent-id/redownload",
            headers=_auth_headers(token),
        )
        assert resp.status_code == 404

    async def test_redownload_success(
        self, client, db_session, patch_publish_settings, repo_dir
    ):
        """mock aiohttp 后重新下载成功。"""
        token = await _admin_token(db_session)
        content = b"redownloaded package content"
        _, _, _, build = await _create_build(
            db_session, repo_dir=None, cached=False
        )

        # 构造 mock response
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.raise_for_status = MagicMock(return_value=None)

        async def _chunked(chunk_size):
            yield content

        mock_resp.content = MagicMock()
        mock_resp.content.iter_chunked = _chunked

        # session.get(url) 返回异步上下文管理器
        mock_get_cm = AsyncMock()
        mock_get_cm.__aenter__.return_value = mock_resp
        mock_get_cm.__aexit__.return_value = None

        # session 是同步对象，get 为同步方法
        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=mock_get_cm)

        # ClientSession(timeout=...) 返回异步上下文管理器
        mock_session_cm = AsyncMock()
        mock_session_cm.__aenter__.return_value = mock_session
        mock_session_cm.__aexit__.return_value = None

        with patch("aiohttp.ClientSession", return_value=mock_session_cm):
            resp = await client.post(
                f"/api/v1/repo-files/packages/{build.id}/redownload",
                headers=_auth_headers(token),
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == 0
        item = body["data"][0]
        assert item["build_id"] == build.id
        assert item["cached"] is True
        assert item["package_size"] == len(content)
        expected_sha = hashlib.sha256(content).hexdigest()
        assert item["sha256"] == expected_sha

        # 文件已写入磁盘
        file_path = repo_dir / build.package_path
        assert file_path.exists()
        assert file_path.read_bytes() == content

        # AuditLog 已写入
        result = await db_session.execute(
            select(AuditLog).where(
                AuditLog.action == "repo_file_redownload",
                AuditLog.resource == build.package_path,
            )
        )
        logs = result.scalars().all()
        assert len(logs) == 1
        assert logs[0].actor == "admin"


# ─── SHA256 验证 ───────────────────────────────────────────────

@pytest.mark.integration
class TestVerifyPackage:
    async def test_build_not_found(
        self, client, db_session, patch_publish_settings
    ):
        """build_id 不存在 → 404。"""
        token = await _admin_token(db_session)
        resp = await client.post(
            "/api/v1/repo-files/packages/nonexistent-id/verify",
            headers=_auth_headers(token),
        )
        assert resp.status_code == 404

    async def test_file_not_found(
        self, client, db_session, patch_publish_settings, repo_dir
    ):
        """文件不存在 → 404。"""
        token = await _admin_token(db_session)
        _, _, _, build = await _create_build(db_session, repo_dir=None)
        resp = await client.post(
            f"/api/v1/repo-files/packages/{build.id}/verify",
            headers=_auth_headers(token),
        )
        assert resp.status_code == 404

    async def test_no_sha256_auto_compute(
        self, client, db_session, patch_publish_settings, repo_dir
    ):
        """无 sha256 记录 → 自动计算并保存, matched=True。"""
        token = await _admin_token(db_session)
        content = b"verify me please"
        _, _, _, build = await _create_build(
            db_session, repo_dir=repo_dir, content=content, sha256=None
        )
        expected = hashlib.sha256(content).hexdigest()

        resp = await client.post(
            f"/api/v1/repo-files/packages/{build.id}/verify",
            headers=_auth_headers(token),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == 0
        item = body["data"][0]
        assert item["matched"] is True
        assert item["computed"] == expected
        assert item["stored"] == expected

        # sha256 已保存到数据库
        await db_session.refresh(build)
        assert build.sha256 == expected

    async def test_sha256_match(
        self, client, db_session, patch_publish_settings, repo_dir
    ):
        """sha256 匹配 → matched=True。"""
        token = await _admin_token(db_session)
        content = b"matching content"
        sha = hashlib.sha256(content).hexdigest()
        _, _, _, build = await _create_build(
            db_session, repo_dir=repo_dir, content=content, sha256=sha
        )

        resp = await client.post(
            f"/api/v1/repo-files/packages/{build.id}/verify",
            headers=_auth_headers(token),
        )
        assert resp.status_code == 200
        body = resp.json()
        item = body["data"][0]
        assert item["matched"] is True
        assert item["computed"] == sha
        assert item["stored"] == sha

    async def test_sha256_mismatch(
        self, client, db_session, patch_publish_settings, repo_dir
    ):
        """sha256 不匹配 → matched=False。"""
        token = await _admin_token(db_session)
        content = b"actual content"
        wrong_sha = hashlib.sha256(b"different content").hexdigest()
        _, _, _, build = await _create_build(
            db_session, repo_dir=repo_dir, content=content, sha256=wrong_sha
        )
        actual_sha = hashlib.sha256(content).hexdigest()

        resp = await client.post(
            f"/api/v1/repo-files/packages/{build.id}/verify",
            headers=_auth_headers(token),
        )
        assert resp.status_code == 200
        body = resp.json()
        item = body["data"][0]
        assert item["matched"] is False
        assert item["computed"] == actual_sha
        assert item["stored"] == wrong_sha


# ─── 一致性检查 ────────────────────────────────────────────────

@pytest.mark.integration
class TestConsistencyCheck:
    async def test_empty_repo(
        self, client, db_session, patch_publish_settings, repo_dir
    ):
        """空仓库 → 200, missing=[], orphans=[]。"""
        token = await _admin_token(db_session)
        resp = await client.post(
            "/api/v1/repo-files/consistency-check",
            headers=_auth_headers(token),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == 0
        data = body["data"][0]
        assert data["missing_files"] == []
        assert data["orphan_files"] == []

    async def test_missing_files(
        self, client, db_session, patch_publish_settings, repo_dir
    ):
        """DB 有 cached 记录但磁盘无文件 → missing 列表。"""
        token = await _admin_token(db_session)
        _, _, _, build = await _create_build(
            db_session, repo_dir=None, cached=True
        )

        resp = await client.post(
            "/api/v1/repo-files/consistency-check",
            headers=_auth_headers(token),
        )
        assert resp.status_code == 200
        body = resp.json()
        data = body["data"][0]
        assert len(data["missing_files"]) == 1
        miss = data["missing_files"][0]
        assert miss["build_id"] == build.id
        assert miss["package_path"] == build.package_path
        assert miss["extension_name"] == "postgis"
        assert data["orphan_files"] == []

    async def test_orphan_files(
        self, client, db_session, patch_publish_settings, repo_dir
    ):
        """磁盘有文件但 DB 无记录 → orphans 列表。"""
        token = await _admin_token(db_session)
        orphan_path = "com.ongres/x86_64/linux/orphan-1.0-pg16.4.tar"
        content = b"orphan content"
        _make_repo_file(repo_dir, orphan_path, content)

        resp = await client.post(
            "/api/v1/repo-files/consistency-check",
            headers=_auth_headers(token),
        )
        assert resp.status_code == 200
        body = resp.json()
        data = body["data"][0]
        assert data["missing_files"] == []
        assert len(data["orphan_files"]) == 1
        orphan = data["orphan_files"][0]
        assert orphan["file_path"] == orphan_path
        assert orphan["file_size"] == len(content)


# ─── 修复一致性 ────────────────────────────────────────────────

@pytest.mark.integration
class TestRepairConsistency:
    async def test_no_orphans(
        self, client, db_session, patch_publish_settings, repo_dir
    ):
        """无孤儿文件 → created=0。"""
        token = await _admin_token(db_session)
        resp = await client.post(
            "/api/v1/repo-files/repair-consistency",
            headers=_auth_headers(token),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == 0
        data = body["data"][0]
        assert data["created"] == 0
        assert data["failed"] == 0

    async def test_repair_creates_records(
        self, client, db_session, patch_publish_settings, repo_dir
    ):
        """有孤儿文件 → 创建完整三层记录, created>0。"""
        token = await _admin_token(db_session)
        orphan_path = "com.ongres/x86_64/linux/postgis-3.4-pg16.4.tar"
        content = b"orphan package content"
        _make_repo_file(repo_dir, orphan_path, content)

        resp = await client.post(
            "/api/v1/repo-files/repair-consistency",
            headers=_auth_headers(token),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == 0
        data = body["data"][0]
        assert data["created"] == 1
        assert data["failed"] == 0

        # 验证 Publisher 已创建
        result = await db_session.execute(
            select(Publisher).where(Publisher.name == "com.ongres")
        )
        assert result.scalar_one_or_none() is not None

        # 验证 Extension 已创建
        result = await db_session.execute(
            select(Extension).where(Extension.name == "postgis")
        )
        assert result.scalar_one_or_none() is not None

        # 验证 ExtensionBuild 已创建且字段正确
        result = await db_session.execute(
            select(ExtensionBuild).where(
                ExtensionBuild.package_path == orphan_path
            )
        )
        build = result.scalar_one_or_none()
        assert build is not None
        assert build.cached is True
        assert build.arch == "x86_64"
        assert build.os == "linux"
        assert build.flavor == "pg"
        assert build.postgres_version == "16.4"
        assert build.package_size == len(content)

        # 验证 AuditLog 已写入
        result = await db_session.execute(
            select(AuditLog).where(AuditLog.action == "repo_file_repair")
        )
        logs = result.scalars().all()
        assert len(logs) == 1
        assert logs[0].actor == "admin"
