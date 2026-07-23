# 仓库源管理文档

## 概述

仓库源管理 API 提供仓库源的增删改查、健康检查和多源索引聚合功能。

**基础路径**: `/api/v1/sources`

**代码位置**: [app/api/sources.py](file:///e:/stackgres/sg-erm/app/api/sources.py)

---

## 接口列表

| 路径 | 方法 | 描述 | 权限 |
|------|------|------|------|
| `/` | GET | 仓库源列表 | 管理员 |
| `/{source_id}` | GET | 仓库源详情 | 管理员 |
| `/` | POST | 创建仓库源 | 管理员 |
| `/{source_id}` | PUT | 更新仓库源 | 管理员 |
| `/{source_id}` | DELETE | 删除仓库源 | 管理员 |
| `/health-check` | POST | 手动触发健康检查 | 管理员 |
| `/aggregate-index` | POST | 手动触发多源 index.json 聚合 | 管理员 |

---

## 详细接口说明

### 1. 仓库源列表

**路径**: `GET /api/v1/sources`

**权限**: 仅管理员

**响应格式**:
```json
{
    "code": 0,
    "data": [
        {
            "id": "uuid",
            "name": "test-sync-source",
            "url": "https://extensions.stackgres.io/postgres/repository",
            "enabled": true,
            "priority": 100,
            "sync_interval": 3600,
            "last_sync": "2026-07-23T10:30:00Z",
            "last_sync_status": "success",
            "health_status": "healthy",
            "auth_type": "none",
            "created_at": "2026-07-21T00:00:00Z"
        }
    ],
    "count": 1
}
```

**相关代码**: [sources.py:51-80](file:///e:/stackgres/sg-erm/app/api/sources.py#L51-L80)

---

### 2. 仓库源详情

**路径**: `GET /api/v1/sources/{source_id}`

**权限**: 仅管理员

**响应格式**:
```json
{
    "code": 0,
    "data": {
        "id": "uuid",
        "name": "test-sync-source",
        "url": "https://extensions.stackgres.io/postgres/repository",
        "enabled": true,
        "priority": 100,
        "sync_interval": 3600,
        "last_sync": "2026-07-23T10:30:00Z",
        "last_sync_status": "success",
        "health_status": "healthy",
        "auth_type": "none",
        "auth_config": null,
        "proxy_url": null,
        "created_at": "2026-07-21T00:00:00Z",
        "updated_at": "2026-07-23T08:00:00Z"
    },
    "count": 1
}
```

**相关代码**: [sources.py:83-107](file:///e:/stackgres/sg-erm/app/api/sources.py#L83-L107)

---

### 3. 创建仓库源

**路径**: `POST /api/v1/sources`

**权限**: 仅管理员

**请求体**:
```json
{
    "name": "my-source",
    "url": "https://extensions.stackgres.io/postgres/repository",
    "enabled": true,
    "priority": 100,
    "sync_interval": 3600,
    "auth_type": "none",
    "auth_config": null,
    "proxy_url": null
}
```

| 参数 | 类型 | 必填 | 描述 |
|------|------|------|------|
| `name` | string | 是 | 仓库源名称 |
| `url` | string | 是 | 上游仓库 URL |
| `enabled` | bool | 否 | 是否启用，默认 true |
| `priority` | int | 否 | 优先级，数值越小优先级越高，默认 100 |
| `sync_interval` | int | 否 | 同步间隔（秒），默认 3600 |
| `auth_type` | string | 否 | 认证类型: none/basic/oauth，默认 none |
| `auth_config` | dict | 否 | 认证配置 |
| `proxy_url` | string | 否 | 代理 URL |

**响应格式**:
```json
{
    "code": 0,
    "message": "创建成功",
    "data": {
        "id": "uuid",
        "name": "my-source",
        "url": "https://extensions.stackgres.io/postgres/repository",
        "enabled": true
    },
    "count": 1
}
```

**相关代码**: [sources.py:110-141](file:///e:/stackgres/sg-erm/app/api/sources.py#L110-L141)

---

### 4. 更新仓库源

**路径**: `PUT /api/v1/sources/{source_id}`

**权限**: 仅管理员

**请求体**:
```json
{
    "name": "new-name",
    "enabled": false
}
```

**响应格式**:
```json
{
    "code": 0,
    "message": "更新成功",
    "data": {
        "id": "uuid"
    },
    "count": 1
}
```

**相关代码**: [sources.py:144-166](file:///e:/stackgres/sg-erm/app/api/sources.py#L144-L166)

---

### 5. 删除仓库源

**路径**: `DELETE /api/v1/sources/{source_id}`

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

**流程逻辑**:
1. 查询仓库源是否存在
2. 检查是否有运行中的同步任务（有则拒绝删除）
3. 级联删除关联的同步任务记录
4. 删除仓库源

**相关代码**: [sources.py:169-205](file:///e:/stackgres/sg-erm/app/api/sources.py#L169-L205)

---

### 6. 健康检查

**路径**: `POST /api/v1/sources/health-check`

**权限**: 仅管理员

**响应格式**:
```json
{
    "code": 0,
    "message": "已检查 2 个源",
    "data": {
        "checked": 2,
        "healthy": 1,
        "unhealthy": 1
    },
    "count": 1
}
```

**相关代码**: [sources.py:208-215](file:///e:/stackgres/sg-erm/app/api/sources.py#L208-L215)

---

### 7. 多源索引聚合

**路径**: `POST /api/v1/sources/aggregate-index`

**权限**: 仅管理员

**响应格式**:
```json
{
    "code": 0,
    "message": "索引聚合成功",
    "data": {
        "path": "/data/repo/v2/index.json"
    },
    "count": 1
}
```

**相关代码**: [sources.py:218-228](file:///e:/stackgres/sg-erm/app/api/sources.py#L218-L228)

---

## 数据模型

**RepositorySource 模型字段**:
| 字段 | 类型 | 描述 |
|------|------|------|
| `id` | string | UUID 主键 |
| `name` | string | 仓库源名称 |
| `url` | string | 上游仓库 URL |
| `enabled` | bool | 是否启用 |
| `priority` | int | 优先级（数值越小越高） |
| `sync_interval` | int | 同步间隔（秒） |
| `last_sync` | datetime | 最后同步时间 |
| `last_sync_status` | string | 最后同步状态 |
| `health_status` | string | 健康状态 |
| `auth_type` | string | 认证类型 |
| `auth_config` | dict | 认证配置 |
| `proxy_url` | string | 代理 URL |

**代码位置**: [app/models/source.py](file:///e:/stackgres/sg-erm/app/models/source.py)

---

## 健康检查机制

**自动健康检查**:
- 服务启动时自动启动健康检查器
- 默认每 60 秒检查一次所有启用的仓库源
- 检查方式: 请求上游 index.json

**代码位置**: [app/services/health_checker.py](file:///e:/stackgres/sg-erm/app/services/health_checker.py)

---

## 多源聚合机制

当存在多个启用的仓库源时：
1. 从每个源获取 index.json
2. 合并扩展元数据（去重）
3. 生成统一的 index.json

**代码位置**: [app/services/index_aggregator.py](file:///e:/stackgres/sg-erm/app/services/index_aggregator.py)
