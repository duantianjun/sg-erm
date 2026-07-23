# 认证 API 文档

## 概述

认证 API 提供用户登录、登出、信息查询、密码修改以及用户管理功能。

**基础路径**: `/api/v1/auth`

**代码位置**: [app/api/auth.py](file:///e:/stackgres/sg-erm/app/api/auth.py)

---

## 接口列表

| 路径 | 方法 | 描述 | 权限 |
|------|------|------|------|
| `/login` | POST | 用户登录，获取 JWT 令牌 | 公开 |
| `/me` | GET | 获取当前登录用户信息 | 需认证 |
| `/refresh` | POST | 使用刷新令牌换取新的访问令牌 | 需认证 |
| `/change-password` | POST | 修改当前用户密码 | 需认证 |
| `/users` | GET | 用户列表 | 管理员 |
| `/users` | POST | 创建用户 | 管理员 |

---

## 详细接口说明

### 1. 用户登录

**路径**: `POST /api/v1/auth/login`

**请求参数**:
| 参数 | 类型 | 必填 | 描述 |
|------|------|------|------|
| `username` | string | 是 | 用户名 |
| `password` | string | 是 | 密码 |

**响应格式**:
```json
{
    "code": 0,
    "message": "登录成功",
    "data": {
        "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
        "refresh_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
        "token_type": "bearer",
        "expires_in": 86400,
        "user": {
            "id": "uuid",
            "username": "admin",
            "email": "admin@sg-erm.local",
            "is_admin": true
        }
    },
    "count": 1
}
```

**流程逻辑**:
1. 验证用户名和密码
2. 更新用户最后登录时间
3. 创建访问令牌（access_token），有效期 24 小时
4. 创建刷新令牌（refresh_token）
5. 返回令牌和用户信息

**相关代码**: [auth.py:86-127](file:///e:/stackgres/sg-erm/app/api/auth.py#L86-L127)

---

### 2. 获取当前用户信息

**路径**: `GET /api/v1/auth/me`

**权限**: 需认证（Authorization: Bearer <access_token>）

**响应格式**:
```json
{
    "code": 0,
    "data": {
        "id": "uuid",
        "username": "admin",
        "email": "admin@sg-erm.local",
        "is_admin": true,
        "last_login": "2026-07-23T10:30:00Z"
    },
    "count": 1
}
```

**相关代码**: [auth.py:130-143](file:///e:/stackgres/sg-erm/app/api/auth.py#L130-L143)

---

### 3. 刷新令牌

**路径**: `POST /api/v1/auth/refresh`

**请求方式**:
- 请求头: `Authorization: Bearer <refresh_token>`
- 或请求体: `{"refresh_token": "..."}`

**响应格式**:
```json
{
    "code": 0,
    "message": "令牌刷新成功",
    "data": {
        "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
        "refresh_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
        "token_type": "bearer",
        "expires_in": 86400
    },
    "count": 1
}
```

**流程逻辑**:
1. 解析刷新令牌
2. 验证令牌类型为 "refresh"
3. 查询用户并验证 token_version（防止令牌被撤销后继续使用）
4. 生成新的访问令牌和刷新令牌

**安全机制**:
- JWT 包含 `token_version` 字段
- 修改密码时 `token_version` 递增
- 刷新令牌时验证 `token_version` 是否匹配数据库值

**相关代码**: [auth.py:146-228](file:///e:/stackgres/sg-erm/app/api/auth.py#L146-L228)

---

### 4. 修改密码

**路径**: `POST /api/v1/auth/change-password`

**请求体**:
```json
{
    "old_password": "old_password",
    "new_password": "NewPassword123!"
}
```

**密码强度要求**:
- 至少 8 位
- 包含至少一个大写字母
- 包含至少一个小写字母
- 包含至少一个数字或特殊字符

**响应格式**:
```json
{
    "code": 0,
    "message": "密码修改成功，已退出所有会话",
    "data": {},
    "count": 1
}
```

**流程逻辑**:
1. 验证原密码正确性
2. 验证新密码强度
3. 更新密码哈希值
4. **递增 token_version**（使所有旧令牌失效）

**相关代码**: [auth.py:231-253](file:///e:/stackgres/sg-erm/app/api/auth.py#L231-L253)

---

### 5. 用户列表

**路径**: `GET /api/v1/auth/users`

**权限**: 仅管理员

**响应格式**:
```json
{
    "code": 0,
    "data": [
        {
            "id": "uuid",
            "username": "admin",
            "email": "admin@sg-erm.local",
            "is_admin": true,
            "is_active": true,
            "last_login": "2026-07-23T10:30:00Z",
            "created_at": "2026-07-21T00:00:00Z"
        }
    ],
    "count": 1
}
```

**相关代码**: [auth.py:258-280](file:///e:/stackgres/sg-erm/app/api/auth.py#L258-L280)

---

### 6. 创建用户

**路径**: `POST /api/v1/auth/users`

**权限**: 仅管理员

**请求体**:
```json
{
    "username": "newuser",
    "password": "NewPassword123!",
    "email": "newuser@sg-erm.local",
    "is_admin": false
}
```

**响应格式**:
```json
{
    "code": 0,
    "message": "创建成功",
    "data": {
        "id": "uuid",
        "username": "newuser"
    },
    "count": 1
}
```

**相关代码**: [auth.py:283-312](file:///e:/stackgres/sg-erm/app/api/auth.py#L283-L312)

---

## 认证流程

```
客户端 ──POST /login──→ API
                          │
                          ▼
                  authenticate_user()
                      │
                      ├─ 验证用户名存在
                      ├─ 验证密码哈希
                      └─ 返回用户对象
                          │
                          ▼
                  create_access_token()
                  create_refresh_token()
                          │
                          ▼
                    返回令牌
                          │
                          ▼
客户端 ──Bearer Token──→ 其他 API
                          │
                          ▼
                  require_auth()
                      │
                      ├─ 解析 JWT
                      ├─ 查询用户
                      ├─ 验证 token_version
                      └─ 返回用户对象
```

---

## 关键安全特性

1. **JWT 令牌**: 使用 HS256 算法签名
2. **令牌撤销**: 通过 token_version 机制实现
3. **密码哈希**: 使用 bcrypt 算法
4. **密码强度**: 强制复杂密码策略
5. **刷新令牌**: 独立的 refresh token 用于获取新的 access token
