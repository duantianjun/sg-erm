"""同步策略与任务模型。

SyncPolicy: 定义同步规则（过滤器、调度、带宽限制）
SyncTask: 记录每次同步执行的状态与结果
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.sqlite import JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.base import TimestampMixin, generate_uuid


class SyncPolicy(TimestampMixin, Base):
    """同步策略。"""
    __tablename__ = "sync_policy"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    source_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("repository_source.id"), nullable=False
    )
    filters: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)  # 过滤配置
    schedule: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)  # Cron 表达式
    enabled: Mapped[bool] = mapped_column(default=True)
    bandwidth_limit: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)  # 如 "50M"
    time_window: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)  # 允许同步的时间窗口
    keep_old_versions: Mapped[int] = mapped_column(Integer, default=3)

    # 关系
    source = relationship("RepositorySource", back_populates="sync_policies")
    tasks = relationship("SyncTask", back_populates="policy")


class SyncTask(Base):
    """同步任务执行记录。"""
    __tablename__ = "sync_task"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    source_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("repository_source.id"), nullable=False
    )
    policy_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("sync_policy.id"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(50), default="pending")
    # pending/running/completed/failed/cancelled
    total: Mapped[int] = mapped_column(Integer, default=0)
    downloaded: Mapped[int] = mapped_column(Integer, default=0)
    failed: Mapped[int] = mapped_column(Integer, default=0)
    skipped: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    diff_summary: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)  # 变更摘要

    # 关系
    source = relationship("RepositorySource", back_populates="sync_tasks")
    policy = relationship("SyncPolicy", back_populates="tasks")
