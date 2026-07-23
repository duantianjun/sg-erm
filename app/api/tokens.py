# -*- coding: utf-8 -*-
"""API Token 管理 API。

提供 API Token 的创建、列出、删除。
"""
import logging
from datetime import datetime, timedelta, timezone

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
    get_token_prefix,
    hash_api_token,
    require_admin,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/tokens", tags=["tokens"])


class TokenCreate(BaseModel):
    """创建 API Token 请求。"""
    name: str
    type: str = "read"
    expires_days: int | None = None
    permissions: list | None = None


@router.get("")
async def list_tokens(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    """API Token 列表（仅管理员）。"""
    logger.info("[TokenAPI] 查询 API Token 列表")
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
    logger.info(f"[TokenAPI] 返回 {len(data)} 个 Token")
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
    logger.info(
        f"[TokenAPI] 创建 Token name={body.name} type={body.type} "
        f"expires_days={body.expires_days or 'never'}"
    )
    plain_token = generate_api_token()
    token_hash = hash_api_token(plain_token)
    token_prefix = get_token_prefix(plain_token)

    expires_at = None
    if body.expires_days:
        expires_at = datetime.now(timezone.utc) + timedelta(days=body.expires_days)

    token = ApiToken(
        name=body.name,
        token_hash=token_hash,
        token_prefix=token_prefix,
        type=body.type,
        permissions=body.permissions or [],
        expires_at=expires_at,
    )
    db.add(token)
    await db.commit()
    await db.refresh(token)

    logger.info(
        f"[TokenAPI] Token 创建成功 id={token.id} name={token.name} "
        f"prefix={token_prefix}"
    )
    return success(
        {
            "id": token.id,
            "name": token.name,
            "type": token.type,
            "token": plain_token,
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
    logger.info(f"[TokenAPI] 删除 Token token_id={token_id}")
    token = await db.get(ApiToken, token_id)
    if not token:
        logger.warning(f"[TokenAPI] 删除失败：Token 不存在 token_id={token_id}")
        return error_response("Token 不存在", status_code=404)

    token_name = token.name
    await db.delete(token)
    await db.commit()

    logger.info(f"[TokenAPI] Token 删除成功 token_id={token_id} name={token_name}")
    return success({"id": token_id}, 1, "删除成功")
