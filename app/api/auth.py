"""认证 API。

提供登录、登出、当前用户信息、密码修改。
"""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel
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
    require_auth,
    require_admin,
)

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


class PasswordChange(BaseModel):
    """修改密码请求。"""
    old_password: str
    new_password: str


class UserCreate(BaseModel):
    """创建用户请求（仅管理员）。"""
    username: str
    password: str
    email: str | None = None
    is_admin: bool = False


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
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码错误",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # 更新最后登录时间
    user.last_login = datetime.utcnow()
    await db.commit()

    access_token = create_access_token(data={"sub": user.id})
    refresh_token = create_refresh_token(data={"sub": user.id})

    return success(
        {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": "bearer",
            "expires_in": 24 * 60 * 60,  # 秒
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

    # 尝试从请求体获取
    try:
        body = await request.json()
        token_str = body.get("refresh_token", "")
    except Exception:
        token_str = ""

    # 回退到 Authorization 头
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

    # 验证用户仍有效
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户不存在或已禁用",
        )

    # 签发新的访问令牌
    new_access = create_access_token(data={"sub": user.id})
    new_refresh = create_refresh_token(data={"sub": user.id})

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
    """修改当前用户密码。"""
    from app.services.auth_service import verify_password

    if not verify_password(body.old_password, user.password_hash):
        return error_response("原密码错误")

    user.password_hash = get_password_hash(body.new_password)
    await db.commit()

    return success({}, 1, "密码修改成功")


# ─── 用户管理（仅管理员）───────────────────────────────

@router.get("/users")
async def list_users(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    """用户列表（仅管理员）。"""
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
    # 检查用户名是否已存在
    existing = await db.scalar(
        select(User).where(User.username == body.username)
    )
    if existing:
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

    return success(
        {"id": user.id, "username": user.username},
        1,
        "创建成功",
    )
