# -*- coding: utf-8 -*-
"""SG-ERM ORM 模型包。

导入所有模型类，确保它们在 Base.metadata 中注册。
Alembic 迁移和 ORM 查询都依赖此包的导入。
"""
from app.models.audit import AuditLog
from app.models.extension import Extension, ExtensionBuild, ExtensionVersion
from app.models.publisher import Publisher
from app.models.security import ApiToken, User
from app.models.source import RepositorySource
from app.models.sync import SyncPolicy, SyncTask
from app.models.whitelist import GlobalWhitelist

__all__ = [
    "AuditLog",
    "Extension",
    "ExtensionBuild",
    "ExtensionVersion",
    "Publisher",
    "ApiToken",
    "User",
    "RepositorySource",
    "SyncPolicy",
    "SyncTask",
    "GlobalWhitelist",
]
