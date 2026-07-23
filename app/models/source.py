# -*- coding: utf-8 -*-
"""仓库源模型。

管理上游扩展仓库源（官方/第三方/自建）。
每个源独立配置 URL、认证、优先级、同步间隔。
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, Integer, String, Text
from sqlalchemy.dialects.sqlite import JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.base import TimestampMixin, generate_uuid


class RepositorySource(TimestampMixin, Base):
    """上游仓库源。"""
    __tablename__ = "repository_source"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    priority: Mapped[int] = mapped_column(Integer, default=100)
    sync_interval: Mapped[int] = mapped_column(Integer, default=3600)  # 秒
    last_sync: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_sync_status: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    health_status: Mapped[str] = mapped_column(String(50), default="unknown")
    auth_type: Mapped[str] = mapped_column(String(50), default="none")
    auth_config: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    proxy_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # 关系
    extensions = relationship("Extension", back_populates="source")
    sync_tasks = relationship("SyncTask", back_populates="source")
    sync_policies = relationship("SyncPolicy", back_populates="source")
