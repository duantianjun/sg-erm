"""认证服务。

提供 JWT 生成/验证、密码哈希、当前用户获取。

Phase 1 采用简化方案：
- 单管理员账号（用户名/密码）
- JWT 访问令牌（Bearer Token）
- API Token 用于程序访问（集群认证）
"""
from datetime import datetime, timedelta
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

# 密码哈希
# 使用 pbkdf2_sha256 替代 bcrypt，避免 Windows 兼容性问题
pwd_context = CryptContext(
    schemes=["pbkdf2_sha256"],
    deprecated="auto",
)

# OAuth2 scheme
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login", auto_error=False)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """验证密码。"""
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    """生成密码哈希。"""
    return pwd_context.hash(password)


def create_access_token(
    data: dict,
    expires_delta: Optional[timedelta] = None,
) -> str:
    """创建 JWT 访问令牌。"""
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(
            minutes=settings.access_token_expire_minutes
        )
    to_encode.update({"exp": expire, "type": "access"})
    encoded_jwt = jwt.encode(
        to_encode,
        settings.secret_key,
        algorithm=settings.jwt_algorithm,
    )
    return encoded_jwt


def create_refresh_token(data: dict) -> str:
    """创建 JWT 刷新令牌。

    刷新令牌有效期 7 天，type 字段为 "refresh"。
    访问令牌可使用刷新令牌换取新的访问令牌。
    """
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(days=7)
    to_encode.update({"exp": expire, "type": "refresh"})
    encoded_jwt = jwt.encode(
        to_encode,
        settings.secret_key,
        algorithm=settings.jwt_algorithm,
    )
    return encoded_jwt


async def get_current_user(
    token: Optional[str] = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> Optional[User]:
    """从 JWT 令牌获取当前用户。

    返回 None 表示未认证（用于可选认证场景）。
    """
    if not token:
        return None

    try:
        payload = jwt.decode(
            token,
            settings.secret_key,
            algorithms=[settings.jwt_algorithm],
        )
        user_id: str = payload.get("sub")
        if user_id is None:
            return None
    except JWTError:
        return None

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user and not user.is_active:
        return None
    return user


async def require_auth(
    token: Optional[str] = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    """强制认证：未登录返回 401。

    用于需要登录的 API 端点。
    """
    user = await get_current_user(token, db)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="未认证，请先登录",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


async def require_admin(
    token: Optional[str] = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    """强制管理员权限。

    用于管理操作（删除、配置变更等）。
    """
    user = await require_auth(token, db)
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
    """验证用户名密码。"""
    result = await db.execute(select(User).where(User.username == username))
    user = result.scalar_one_or_none()
    if not user:
        return None
    if not verify_password(password, user.password_hash):
        return None
    return user


# ─── API Token 认证 ──────────────────────────────────

API_TOKEN_PREFIX = "sgerm_"


def generate_api_token() -> str:
    """生成随机 API Token 明文。"""
    import secrets

    return API_TOKEN_PREFIX + secrets.token_urlsafe(32)


def hash_api_token(token: str) -> str:
    """哈希 API Token。"""
    return pwd_context.hash(token)


def verify_api_token(token: str, token_hash: str) -> bool:
    """验证 API Token。"""
    return pwd_context.verify(token, token_hash)


async def get_api_token_auth(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> Optional[ApiToken]:
    """从请求头中获取并验证 API Token。

    支持 X-API-Token 头或 Authorization: Bearer <token>。
    """
    token = None

    # 优先检查 X-API-Token
    token = request.headers.get("X-API-Token")
    if not token:
        # 检查 Authorization: Bearer <token>
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            token = auth[7:]

    if not token or not token.startswith(API_TOKEN_PREFIX):
        return None

    # 查找匹配的 token hash
    result = await db.execute(select(ApiToken))
    tokens = result.scalars().all()

    for api_token in tokens:
        if verify_api_token(token, api_token.token_hash):
            # 检查是否过期
            if api_token.expires_at and api_token.expires_at < datetime.utcnow():
                return None
            # 更新最后使用时间
            api_token.last_used_at = datetime.utcnow()
            await db.commit()
            return api_token

    return None


async def get_current_principal(
    request: Request,
    token: Optional[str] = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> Optional[dict]:
    """获取当前认证主体（用户或 API Token）。

    返回 dict: {"type": "user"|"token", "id": ..., "name": ..., "is_admin": bool}
    或 None（未认证）。
    """
    # 先尝试 JWT 用户认证
    user = await get_current_user(token, db)
    if user:
        return {
            "type": "user",
            "id": user.id,
            "name": user.username,
            "is_admin": user.is_admin,
        }

    # 再尝试 API Token
    api_token = await get_api_token_auth(request, db)
    if api_token:
        return {
            "type": "token",
            "id": api_token.id,
            "name": api_token.name,
            "is_admin": api_token.type == "admin",
            "permissions": api_token.permissions or [],
        }

    return None
