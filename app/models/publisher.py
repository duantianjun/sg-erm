"""发布者模型。

管理扩展发布者（如 com.ongres）。
自定义发布者由系统管理 RSA 密钥对，用于签名扩展包。
"""
from typing import Optional

from sqlalchemy import Boolean, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.base import TimestampMixin, generate_uuid


class Publisher(TimestampMixin, Base):
    """扩展发布者。"""
    __tablename__ = "publisher"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    display_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    public_key: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # PEM 格式公钥
    private_key: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # AES-256 加密的私钥
    is_custom: Mapped[bool] = mapped_column(Boolean, default=False)

    # 关系
    extensions = relationship(
        "Extension",
        back_populates="publisher",
        cascade="all, delete-orphan",
    )
