# -*- coding: utf-8 -*-
"""审计日志模型。

记录所有关键操作：sync.start, sync.complete, publish, delete, login, config.change 等。
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.sqlite import JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.base import generate_uuid


class AuditLog(Base):
    """审计日志（精确到毫秒）。"""
    __tablename__ = "audit_log"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    actor: Mapped[str] = mapped_column(String(255), nullable=False)  # 操作者
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    # sync.start/sync.complete/publish/delete/login/config.change/token.create/token.revoke
    resource: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # 操作对象
    detail: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)  # 详情
    result: Mapped[str] = mapped_column(String(50), default="success")  # success/failure
    client_ip: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)

    # 关联（可选：关联用户或令牌）
    user_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("user.id"), nullable=True
    )
    api_token_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("api_token.id"), nullable=True
    )

    # 关系
    user = relationship("User", back_populates="audit_logs")
    api_token = relationship("ApiToken", back_populates="audit_logs")
