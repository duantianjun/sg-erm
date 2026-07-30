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
