# -*- coding: utf-8 -*-
"""认证 API。

提供登录、登出、当前用户信息、密码修改。
"""
import logging
import re
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.response import error_response, success
from app.config import settings
from app.database import get_db
from app.models import User
from app.services.auth_service import (
    authenticate_user,
    create_access_token,
    create_refresh_token,
    get_current_user,
    get_password_hash,
    increment_token_version,
    require_auth,
    require_admin,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


def validate_password_strength(password: str) -> tuple[bool, str]:
    """校验密码强度。

    要求:
    - 至少 8 位
    - 包含至少一个大写字母
    - 包含至少一个小写字母
    - 包含至少一个数字或特殊字符
    """
    if len(password) < 8:
        return False, "密码长度至少 8 位"
    if not re.search(r"[A-Z]", password):
        return False, "密码需包含至少一个大写字母"
    if not re.search(r"[a-z]", password):
        return False, "密码需包含至少一个小写字母"
    if not re.search(r"[0-9!@#$%^&*(),.?\":{}|<>]", password):
        return False, "密码需包含至少一个数字或特殊字符"
    return True, ""


class PasswordChange(BaseModel):
    """修改密码请求。"""
    old_password: str
    new_password: str

    @field_validator("new_password")
    @classmethod
    def validate_new_password(cls, v: str) -> str:
        valid, reason = validate_password_strength(v)
        if not valid:
            raise ValueError(reason)
        return v


class UserCreate(BaseModel):
    """创建用户请求（仅管理员）。"""
    username: str
    password: str
    email: str | None = None
    is_admin: bool = False

    @field_validator("password")
    @classmethod
    def validate_password(cls, v: str) -> str:
        valid, reason = validate_password_strength(v)
        if not valid:
            raise ValueError(reason)
        return v


@router.post("/login")
async def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: AsyncSession = Depends(get_db),
):
    """用户登录（JWT 令牌）。

    返回: {access_token, token_type, user: {...}}
    """
    user = await authenticate_user(db, form_data.username, form_data.password)
    if not user:
        logger.warning(f"[认证API] 登录失败 username={form_data.username}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码错误",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user.last_login = datetime.now(timezone.utc)
    await db.commit()

    access_token = create_access_token(data={"sub": user.id, "token_version": user.token_version})
    refresh_token = create_refresh_token(data={"sub": user.id, "token_version": user.token_version})

    logger.info(f"[认证API] 用户登录成功 user_id={user.id} username={user.username} is_admin={user.is_admin}")

    return success(
        {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": "bearer",
            "expires_in": 24 * 60 * 60,
            "user": {
                "id": user.id,
                "username": user.username,
                "email": user.email,
                "is_admin": user.is_admin,
            },
        },
        1,
        "登录成功",
    )


@router.get("/me")
async def get_me(user: User = Depends(require_auth)):
    """获取当前登录用户信息。"""
    logger.debug(f"[认证API] 获取用户信息 user_id={user.id} username={user.username}")
    return success(
        {
            "id": user.id,
            "username": user.username,
            "email": user.email,
            "is_admin": user.is_admin,
            "last_login": user.last_login.isoformat() if user.last_login else None,
        },
        1,
    )


@router.post("/refresh")
async def refresh_token(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """使用刷新令牌换取新的访问令牌。

    请求头: Authorization: Bearer <refresh_token>
    或请求体: {"refresh_token": "..."}
    """
    from jose import JWTError, jwt
    from pydantic import BaseModel

    class RefreshBody(BaseModel):
        refresh_token: str

    try:
        body = await request.json()
        token_str = body.get("refresh_token", "")
    except Exception:
        token_str = ""

    if not token_str:
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            token_str = auth[7:]

    if not token_str:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="缺少刷新令牌",
        )

    try:
        payload = jwt.decode(
            token_str,
            settings.secret_key,
            algorithms=[settings.jwt_algorithm],
        )
        token_type = payload.get("type")
        user_id = payload.get("sub")
        token_version = payload.get("token_version", 0)

        if token_type != "refresh" or not user_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="无效的刷新令牌",
            )
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="刷新令牌已过期或无效",
        )

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户不存在或已禁用",
        )

    if token_version != user.token_version:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="令牌已失效，请重新登录",
        )

    new_access = create_access_token(data={"sub": user.id, "token_version": user.token_version})
    new_refresh = create_refresh_token(data={"sub": user.id, "token_version": user.token_version})

    logger.info(f"[认证API] 刷新令牌成功 user_id={user.id}")

    return success(
        {
            "access_token": new_access,
            "refresh_token": new_refresh,
            "token_type": "bearer",
            "expires_in": 24 * 60 * 60,
        },
        1,
        "令牌刷新成功",
    )


@router.post("/change-password")
async def change_password(
    body: PasswordChange,
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """修改当前用户密码。

    修改密码后自动递增 token_version，使所有旧令牌失效。
    """
    from app.services.auth_service import verify_password

    if not verify_password(body.old_password, user.password_hash):
        logger.warning(f"[认证API] 修改密码失败：原密码错误 user_id={user.id}")
        return error_response("原密码错误")

    user.password_hash = get_password_hash(body.new_password)
    await db.flush()

    await increment_token_version(db, user.id)

    logger.info(f"[认证API] 用户修改密码成功 user_id={user.id} token_version+1")
    return success({}, 1, "密码修改成功，已退出所有会话")


# ─── 用户管理（仅管理员）───────────────────────────────

@router.get("/users")
async def list_users(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    """用户列表（仅管理员）。"""
    logger.info("[认证API] 管理员查询用户列表")
    result = await db.execute(select(User).order_by(User.username))
    users = result.scalars().all()

    data = [
        {
            "id": u.id,
            "username": u.username,
            "email": u.email,
            "is_admin": u.is_admin,
            "is_active": u.is_active,
            "last_login": u.last_login.isoformat() if u.last_login else None,
            "created_at": u.created_at.isoformat() if u.created_at else None,
        }
        for u in users
    ]
    return success(data, len(data))


@router.post("/users")
async def create_user(
    body: UserCreate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    """创建用户（仅管理员）。"""
    existing = await db.scalar(
        select(User).where(User.username == body.username)
    )
    if existing:
        logger.warning(f"[认证API] 创建用户失败：用户名已存在 username={body.username}")
        return error_response(f"用户名 '{body.username}' 已存在")

    user = User(
        username=body.username,
        password_hash=get_password_hash(body.password),
        email=body.email,
        is_admin=body.is_admin,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)

    logger.info(f"[认证API] 创建用户成功 username={user.username} is_admin={user.is_admin} user_id={user.id}")
    return success(
        {"id": user.id, "username": user.username},
        1,
        "创建成功",
    )
