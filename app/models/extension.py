"""扩展模型。

三层结构：Extension -> ExtensionVersion -> ExtensionBuild
- Extension: 扩展基本信息（如 postgis）
- ExtensionVersion: 版本（如 3.4），关联通道
- ExtensionBuild: 具体构建（PG 版本 + 架构 + OS），关联实际包文件
"""
from typing import Optional

from sqlalchemy import Boolean, BigInteger, DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.sqlite import JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.base import TimestampMixin, generate_uuid


class Extension(TimestampMixin, Base):
    """扩展（如 postgis、pgaudit）。"""
    __tablename__ = "extension"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    publisher_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("publisher.id"), nullable=False
    )
    source_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("repository_source.id"), nullable=True
    )
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    abstract: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    tags: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)  # 标签数组
    url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # 文档 URL
    source_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # 源码 URL
    license: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    channels: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)  # {stable: "3.4", beta: "3.5"}
    is_custom: Mapped[bool] = mapped_column(Boolean, default=False)

    # 关系
    publisher = relationship("Publisher", back_populates="extensions")
    source = relationship("RepositorySource", back_populates="extensions")
    versions = relationship("ExtensionVersion", back_populates="extension", cascade="all, delete-orphan")


class ExtensionVersion(TimestampMixin, Base):
    """扩展版本（如 3.4）。"""
    __tablename__ = "extension_version"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    extension_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("extension.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[str] = mapped_column(String(100), nullable=False)
    channel: Mapped[str] = mapped_column(String(50), default="stable")  # stable/beta/dev
    extra_mounts: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    # 关系
    extension = relationship("Extension", back_populates="versions")
    builds = relationship("ExtensionBuild", back_populates="version", cascade="all, delete-orphan")


class ExtensionBuild(Base):
    """扩展构建（具体 PG 版本 + 架构 + OS 的包）。"""
    __tablename__ = "extension_build"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    version_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("extension_version.id", ondelete="CASCADE"), nullable=False
    )
    postgres_version: Mapped[str] = mapped_column(String(50), nullable=False)  # 如 "16.4"
    arch: Mapped[str] = mapped_column(String(50), nullable=False)  # x86_64/aarch64
    os: Mapped[str] = mapped_column(String(50), nullable=False)  # linux
    flavor: Mapped[str] = mapped_column(String(20), nullable=False)  # pg/bf
    build: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)  # 构建号
    package_path: Mapped[str] = mapped_column(Text, nullable=False)  # 文件相对路径
    package_size: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)  # 字节
    sha256: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    verified: Mapped[bool] = mapped_column(Boolean, default=False)  # 签名是否验证通过
    cached: Mapped[bool] = mapped_column(Boolean, default=False)  # 是否已缓存到本地
    last_accessed: Mapped[Optional[str]] = mapped_column(DateTime, nullable=True)  # 用于 LRU

    # 关系
    version = relationship("ExtensionVersion", back_populates="builds")
