# API Token 管理文档

## 概述

API Token 管理 API 提供用于服务间调用的 API Token 创建、列表和删除功能。

**基础路径**: `/api/v1/tokens`

**代码位置**: [app/api/tokens.py](file:///e:/stackgres/sg-erm/app/api/tokens.py)

---

## 接口列表

| 路径 | 方法 | 描述 | 权限 |
|------|------|------|------|
| `/` | GET | API Token 列表 | 管理员 |
| `/` | POST | 创建 API Token | 管理员 |
| `/{token_id}` | DELETE | 删除 API Token | 管理员 |

---

## 详细接口说明

### 1. Token 列表

**路径**: `GET /api/v1/tokens`

**权限**: 仅管理员

**响应格式**:
```json
{
    "code": 0,
    "data": [
        {
            "id": "uuid",
            "name": "my-service-token",
            "type": "read",
            "permissions": ["read", "list"],
            "expires_at": "2026-08-23T10:30:00Z",
            "last_used_at": "2026-07-23T08:00:00Z",
            "created_at": "2026-07-23T10:30:00Z"
        }
    ],
    "count": 1
}
```

**相关代码**: [tokens.py:38-63](file:///e:/stackgres/sg-erm/app/api/tokens.py#L38-L63)

---

### 2. 创建 Token

**路径**: `POST /api/v1/tokens`

**权限**: 仅管理员

**请求体**:
```json
{
    "name": "my-service-token",
    "type": "read",
    "expires_days": 30,
    "permissions": ["read", "list"]
}
```

| 参数 | 类型 | 必填 | 描述 |
|------|------|------|------|
| `name` | string | 是 | Token 名称，用于标识用途 |
| `type` | string | 否 | Token 类型，默认 "read" |
| `expires_days` | int | 否 | 过期天数，不填则永不过期 |
| `permissions` | list | 否 | 权限列表 |

**响应格式**:
```json
{
    "code": 0,
    "message": "创建成功。请妥善保存 Token，此页面关闭后无法再次查看。",
    "data": {
        "id": "uuid",
        "name": "my-service-token",
        "type": "read",
        "token": "sg_erm_abc123def456...",
        "expires_at": "2026-08-23T10:30:00Z"
    },
    "count": 1
}
```

**流程逻辑**:
1. 生成随机明文 Token（格式: `sg_erm_<随机字符串>`）
2. 提取 Token 前缀（前 8 个字符）用于快速查找
3. 使用 bcrypt 哈希 Token
4. 存储到数据库（仅存储哈希和前缀，不存储明文）
5. 返回明文 Token（**只返回一次**）

**安全机制**:
- 明文 Token 仅在创建时返回一次
- 数据库只存储 Token 哈希值
- 使用 token_prefix 字段建立索引，避免验证时全表扫描
- 支持过期时间设置

**相关代码**: [tokens.py:66-114](file:///e:/stackgres/sg-erm/app/api/tokens.py#L66-L114)

---

### 3. 删除 Token

**路径**: `DELETE /api/v1/tokens/{token_id}`

**权限**: 仅管理员

**响应格式**:
```json
{
    "code": 0,
    "message": "删除成功",
    "data": {
        "id": "uuid"
    },
    "count": 1
}
```

**相关代码**: [tokens.py:117-135](file:///e:/stackgres/sg-erm/app/api/tokens.py#L117-L135)

---

## Token 验证流程

```
客户端 ──Authorization: Bearer sg_erm_xxx──→ API
                                              │
                                              ▼
                              get_api_token_auth()
                                    │
                              ┌─────┴─────┐
                              │           │
                              ▼           ▼
                       提取前缀      查询数据库
                       (sg_erm_xxx)  (按 token_prefix 索引)
                              │           │
                              │           ▼
                              │    获取 token_hash
                              │           │
                              └─────┬─────┘
                                    ▼
                              bcrypt 验证
                              明文 Token
                                    │
                              ┌─────┴─────┐
                              │           │
                              ▼           ▼
                          验证成功      验证失败
                              │           │
                              ▼           ▼
                         返回用户      返回 401
```

---

## 数据模型

**ApiToken 模型字段**:
| 字段 | 类型 | 描述 |
|------|------|------|
| `id` | string | UUID 主键 |
| `name` | string | Token 名称 |
| `token_hash` | string | Token 哈希值（bcrypt） |
| `token_prefix` | string | Token 前缀（前 8 字符，有索引） |
| `type` | string | Token 类型 |
| `permissions` | list | 权限列表 |
| `expires_at` | datetime | 过期时间 |
| `last_used_at` | datetime | 最后使用时间 |
| `created_at` | datetime | 创建时间 |

**代码位置**: [app/models/security.py](file:///e:/stackgres/sg-erm/app/models/security.py)

---

## 关键安全特性

1. **前缀索引**: Token 验证时先按前缀查找，避免全表扫描
2. **哈希存储**: 明文 Token 不存储在数据库中
3. **单次返回**: 创建时只返回一次明文 Token
4. **过期机制**: 支持设置过期时间
5. **使用追踪**: 记录最后使用时间
