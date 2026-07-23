# -*- coding: utf-8 -*-
"""安全模型：用户与 API 令牌。

User: 管理员账号（用户名+密码 → JWT）
ApiToken: API 访问令牌（集群/只读用户使用，仅存哈希）
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Index, String, Text
from sqlalchemy.dialects.sqlite import JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.base import TimestampMixin, generate_uuid


class User(TimestampMixin, Base):
    """系统用户（管理员）。"""
    __tablename__ = "user"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    username: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(default=True)
    is_admin: Mapped[bool] = mapped_column(default=False)
    last_login: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    token_version: Mapped[int] = mapped_column(default=1)

    # 关系
    audit_logs = relationship("AuditLog", back_populates="user")


class ApiToken(TimestampMixin, Base):
    """API 访问令牌。

    格式: sgerm_{random_32_chars}
    存储: 仅存哈希，不存明文
    权限: read / write / admin

    token_prefix: 令牌前 8 位明文前缀，用于索引查询优化，避免全表遍历验证
    """
    __tablename__ = "api_token"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    token_prefix: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    type: Mapped[str] = mapped_column(String(50), default="read")
    permissions: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_used_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    __table_args__ = (
        Index("ix_api_token_token_prefix", "token_prefix"),
    )

    # 关系
    audit_logs = relationship("AuditLog", back_populates="api_token")
