"""模型公共基类与 Mixin。"""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, func
from sqlalchemy.orm import Mapped, mapped_column


class TimestampMixin:
    """创建/更新时间戳 Mixin，所有业务表共享。"""
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        server_default=func.current_timestamp(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        server_default=func.current_timestamp(),
    )


def generate_uuid() -> str:
    """生成 UUID 字符串（作为主键默认值）。"""
    return str(uuid.uuid4())
