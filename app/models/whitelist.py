"""全局白名单模型。

定义基线白名单，所有同步策略默认包含。
策略级白名单在 sync_policy.filters 中配置，可追加或覆盖。
"""
from typing import Optional

from sqlalchemy import String
from sqlalchemy.dialects.sqlite import JSON
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.base import TimestampMixin, generate_uuid


class GlobalWhitelist(TimestampMixin, Base):
    """全局白名单（基线）。"""
    __tablename__ = "global_whitelist"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    extension_name: Mapped[str] = mapped_column(String(255), nullable=False)
    # PG 版本范围（如 [">=16.0"]）
    postgres_versions: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    # 架构列表（如 ["x86_64", "aarch64"]）
    arch: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
