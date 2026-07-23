# -*- coding: utf-8 -*-
"""认证服务。

提供 JWT 生成/验证、密码哈希、当前用户获取。

Phase 1 采用简化方案：
- 单管理员账号（用户名/密码）
- JWT 访问令牌（Bearer Token）
- API Token 用于程序访问（集群认证）

安全特性：
- JWT 令牌包含 token_version，支持令牌撤销（修改密码时递增）
- API Token 使用前缀索引优化，避免全表遍历验证
"""
from datetime import datetime, timedelta, timezone
import logging
from typing import Optional

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models import ApiToken, User

logger = logging.getLogger(__name__)

pwd_context = CryptContext(
    schemes=["pbkdf2_sha256"],
    deprecated="auto",
)

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login", auto_error=False)

API_TOKEN_PREFIX = "sgerm_"
TOKEN_PREFIX_LEN = 8


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)


def create_access_token(
    data: dict,
    expires_delta: Optional[timedelta] = None,
) -> str:
    to_encode = data.copy()
    now = datetime.now(timezone.utc)
    if expires_delta:
        expire = now + expires_delta
    else:
        expire = now + timedelta(minutes=settings.access_token_expire_minutes)
    to_encode.update({"exp": expire, "type": "access", "iat": now})
    encoded_jwt = jwt.encode(
        to_encode,
        settings.secret_key,
        algorithm=settings.jwt_algorithm,
    )
    return encoded_jwt


def create_refresh_token(data: dict) -> str:
    to_encode = data.copy()
    now = datetime.now(timezone.utc)
    expire = now + timedelta(days=7)
    to_encode.update({"exp": expire, "type": "refresh", "iat": now})
    encoded_jwt = jwt.encode(
        to_encode,
        settings.secret_key,
        algorithm=settings.jwt_algorithm,
    )
    return encoded_jwt


async def get_current_user(
    request: Request,
    token: Optional[str] = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> Optional[User]:
    if not token:
        request.state.principal = None
        return None

    try:
        payload = jwt.decode(
            token,
            settings.secret_key,
            algorithms=[settings.jwt_algorithm],
        )
        user_id: str = payload.get("sub")
        token_version: int = payload.get("token_version", 0)
        if user_id is None:
            logger.debug("[认证] JWT 缺少 sub 字段")
            request.state.principal = None
            return None
    except JWTError as e:
        logger.debug(f"[认证] JWT 解码失败: {e}")
        request.state.principal = None
        return None

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        logger.debug(f"[认证] 用户不存在 user_id={user_id}")
        request.state.principal = None
        return None
    if not user.is_active:
        logger.warning(f"[认证] 用户已禁用 user_id={user_id} username={user.username}")
        request.state.principal = None
        return None

    if token_version != user.token_version:
        logger.warning(
            f"[认证] JWT token_version 不匹配 user_id={user_id} username={user.username} "
            f"jwt_ver={token_version} db_ver={user.token_version}"
        )
        request.state.principal = None
        return None

    request.state.principal = {
        "type": "user",
        "id": user.id,
        "name": user.username,
        "is_admin": user.is_admin,
    }
    return user


async def require_auth(
    request: Request,
    token: Optional[str] = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    user = await get_current_user(request, token, db)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="未认证，请先登录",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


async def require_admin(
    request: Request,
    token: Optional[str] = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    user = await require_auth(request, token, db)
    if not user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="需要管理员权限",
        )
    return user


async def authenticate_user(
    db: AsyncSession,
    username: str,
    password: str,
) -> Optional[User]:
    result = await db.execute(select(User).where(User.username == username))
    user = result.scalar_one_or_none()
    if not user:
        logger.warning(f"[认证] 登录失败：用户不存在 username={username}")
        return None
    if not verify_password(password, user.password_hash):
        logger.warning(f"[认证] 登录失败：密码错误 username={username}")
        return None
    logger.info(f"[认证] 登录成功 username={username} user_id={user.id}")
    return user


async def increment_token_version(
    db: AsyncSession,
    user_id: str,
) -> None:
    """递增用户的 token_version，使所有旧令牌失效。"""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user:
        user.token_version += 1
        await db.commit()
        logger.info(
            f"[认证] 递增 token_version user_id={user_id} username={user.username} "
            f"new_version={user.token_version}"
        )


def generate_api_token() -> str:
    import secrets

    return API_TOKEN_PREFIX + secrets.token_urlsafe(32)


def get_token_prefix(token: str) -> str:
    """提取令牌前缀（用于索引查询）。"""
    return token[len(API_TOKEN_PREFIX) : len(API_TOKEN_PREFIX) + TOKEN_PREFIX_LEN]


def hash_api_token(token: str) -> str:
    return pwd_context.hash(token)


def verify_api_token(token: str, token_hash: str) -> bool:
    return pwd_context.verify(token, token_hash)


async def get_api_token_auth(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> Optional[ApiToken]:
    """从请求头中获取并验证 API Token。

    支持 X-API-Token 头或 Authorization: Bearer <token>。
    使用 token_prefix 索引优化查询，避免全表遍历。
    """
    token = None

    token = request.headers.get("X-API-Token")
    if not token:
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            token = auth[7:]

    if not token or not token.startswith(API_TOKEN_PREFIX):
        return None

    token_prefix = get_token_prefix(token)

    result = await db.execute(
        select(ApiToken).where(ApiToken.token_prefix == token_prefix)
    )
    tokens = result.scalars().all()

    if not tokens:
        logger.debug(f"[认证] 未找到匹配 prefix 的 API Token prefix={token_prefix}")
        return None

    for api_token in tokens:
        if verify_api_token(token, api_token.token_hash):
            if api_token.expires_at and api_token.expires_at < datetime.now(timezone.utc):
                logger.warning(
                    f"[认证] API Token 已过期 token_id={api_token.id} name={api_token.name}"
                )
                return None
            api_token.last_used_at = datetime.now(timezone.utc)
            await db.commit()
            logger.debug(
                f"[认证] API Token 验证通过 token_id={api_token.id} name={api_token.name}"
            )
            return api_token

    logger.warning(f"[认证] API Token 验证失败 prefix={token_prefix} candidates={len(tokens)}")
    return None


async def get_current_principal(
    request: Request,
    token: Optional[str] = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> Optional[dict]:
    user = await get_current_user(token, db)
    if user:
        principal = {
            "type": "user",
            "id": user.id,
            "name": user.username,
            "is_admin": user.is_admin,
        }
        request.state.principal = principal
        return principal

    api_token = await get_api_token_auth(request, db)
    if api_token:
        principal = {
            "type": "token",
            "id": api_token.id,
            "name": api_token.name,
            "is_admin": api_token.type == "admin",
            "permissions": api_token.permissions or [],
        }
        request.state.principal = principal
        return principal

    request.state.principal = None
    return None


async def attach_principal_to_request(
    request: Request,
    token: Optional[str] = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
):
    await get_current_principal(request, token, db)
