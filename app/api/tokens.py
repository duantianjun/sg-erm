"""API Token 管理 API。

提供 API Token 的创建、列出、删除。
"""
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.response import error_response, success
from app.database import get_db
from app.models import ApiToken, User
from app.services.auth_service import (
    generate_api_token,
    get_password_hash,
    hash_api_token,
    require_admin,
)

router = APIRouter(prefix="/api/v1/tokens", tags=["tokens"])


class TokenCreate(BaseModel):
    """创建 API Token 请求。"""
    name: str
    type: str = "read"  # read/write/admin
    expires_days: int | None = None  # None = 永不过期
    permissions: list | None = None


@router.get("")
async def list_tokens(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    """API Token 列表（仅管理员）。"""
    result = await db.execute(
        select(ApiToken).order_by(ApiToken.created_at.desc())
    )
    tokens = result.scalars().all()

    data = [
        {
            "id": t.id,
            "name": t.name,
            "type": t.type,
            "permissions": t.permissions,
            "expires_at": t.expires_at.isoformat() if t.expires_at else None,
            "last_used_at": t.last_used_at.isoformat() if t.last_used_at else None,
            "created_at": t.created_at.isoformat() if t.created_at else None,
        }
        for t in tokens
    ]
    return success(data, len(data))


@router.post("")
async def create_token(
    body: TokenCreate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    """创建 API Token（仅管理员）。

    注意：明文 Token 只在创建时返回一次，之后无法查看。
    """
    plain_token = generate_api_token()
    token_hash = hash_api_token(plain_token)

    expires_at = None
    if body.expires_days:
        expires_at = datetime.utcnow() + timedelta(days=body.expires_days)

    token = ApiToken(
        name=body.name,
        token_hash=token_hash,
        type=body.type,
        permissions=body.permissions or [],
        expires_at=expires_at,
    )
    db.add(token)
    await db.commit()
    await db.refresh(token)

    return success(
        {
            "id": token.id,
            "name": token.name,
            "type": token.type,
            "token": plain_token,  # 只返回一次！
            "expires_at": token.expires_at.isoformat() if token.expires_at else None,
        },
        1,
        "创建成功。请妥善保存 Token，此页面关闭后无法再次查看。",
    )


@router.delete("/{token_id}")
async def delete_token(
    token_id: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    """删除 API Token（仅管理员）。"""
    token = await db.get(ApiToken, token_id)
    if not token:
        return error_response("Token 不存在", status_code=404)

    await db.delete(token)
    await db.commit()

    return success({"id": token_id}, 1, "删除成功")
