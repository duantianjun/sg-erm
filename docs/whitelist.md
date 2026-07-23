# 全局白名单文档

## 概述

全局白名单 API 提供扩展名称的白名单管理功能。白名单作为基线，所有同步策略和代理模式都受其约束。

**基础路径**: `/api/v1/whitelist`

**代码位置**: [app/api/whitelist.py](file:///e:/stackgres/sg-erm/app/api/whitelist.py)

---

## 接口列表

| 路径 | 方法 | 描述 | 权限 |
|------|------|------|------|
| `/` | GET | 白名单列表 | 管理员 |
| `/` | POST | 添加白名单条目 | 管理员 |
| `/{entry_id}` | DELETE | 删除白名单条目 | 管理员 |

---

## 详细接口说明

### 1. 白名单列表

**路径**: `GET /api/v1/whitelist?keyword=xxx`

**权限**: 仅管理员

**查询参数**:
| 参数 | 类型 | 必填 | 描述 |
|------|------|------|------|
| `keyword` | string | 否 | 搜索关键词 |

**响应格式**:
```json
{
    "code": 0,
    "data": [
        {
            "id": "uuid",
            "extension_name": "postgis",
            "postgres_versions": [">=16.0"],
            "arch": ["x86_64", "aarch64"],
            "created_at": "2026-07-23T10:30:00Z"
        }
    ],
    "count": 1
}
```

**相关代码**: [whitelist.py:35-62](file:///e:/stackgres/sg-erm/app/api/whitelist.py#L35-L62)

---

### 2. 添加白名单条目

**路径**: `POST /api/v1/whitelist`

**权限**: 仅管理员

**请求体**:
```json
{
    "extension_name": "postgis",
    "postgres_versions": [">=16.0"],
    "arch": ["x86_64", "aarch64"]
}
```

| 参数 | 类型 | 必填 | 描述 |
|------|------|------|------|
| `extension_name` | string | 是 | 扩展名称 |
| `postgres_versions` | list | 否 | PG 版本范围限制 |
| `arch` | list | 否 | 架构限制 |

**响应格式**:
```json
{
    "code": 0,
    "message": "添加成功",
    "data": {
        "id": "uuid",
        "extension_name": "postgis"
    },
    "count": 1
}
```

**相关代码**: [whitelist.py:65-98](file:///e:/stackgres/sg-erm/app/api/whitelist.py#L65-L98)

---

### 3. 删除白名单条目

**路径**: `DELETE /api/v1/whitelist/{entry_id}`

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

**相关代码**: [whitelist.py:101-118](file:///e:/stackgres/sg-erm/app/api/whitelist.py#L101-L118)

---

## 数据模型

**GlobalWhitelist 模型字段**:
| 字段 | 类型 | 描述 |
|------|------|------|
| `id` | string | UUID 主键 |
| `extension_name` | string | 扩展名称 |
| `postgres_versions` | list | PG 版本范围限制 |
| `arch` | list | 架构限制 |
| `created_at` | datetime | 创建时间 |

**代码位置**: [app/models/whitelist.py](file:///e:/stackgres/sg-erm/app/models/whitelist.py)

---

## 白名单作用机制

### 1. 同步任务中的白名单过滤

**代码位置**: [app/services/sync_engine.py:299-355](file:///e:/stackgres/sg-erm/app/services/sync_engine.py#L299-L355)

**流程**:
1. 获取全局白名单
2. 如果白名单为空，**拒绝同步**（安全默认）
3. 如果白名单查询失败，**拒绝同步**（安全默认）
4. 合并策略级过滤器与白名单（取交集）
5. 只有在白名单中的扩展才会被同步

**安全规则**:
- 白名单为空 → 拒绝所有同步任务
- 白名单查询失败 → 拒绝所有同步任务
- 策略级 include 与白名单取交集

---

### 2. 代理模式中的白名单过滤

**代码位置**: [app/services/proxy_engine.py:292-329](file:///e:/stackgres/sg-erm/app/services/proxy_engine.py#L292-L329)

**流程**:
1. 查询全局白名单
2. 如果白名单为空，**拒绝代理请求**（安全默认）
3. 从包名提取扩展名（如 `postgis-3.4-pg16.4` → `postgis`）
4. 检查扩展名是否在白名单中
5. 不在白名单中的包，直接返回 404

**安全规则**:
- 白名单为空 → 拒绝所有代理请求
- 白名单查询失败 → 拒绝所有代理请求
- 只允许请求白名单中的扩展包

---

## 三种代理模式下的白名单行为

| 模式 | 本地缓存命中 | 本地未命中 | 白名单检查 |
|------|-------------|-----------|-----------|
| **strict** | 返回文件 | 直接 404 | 不检查 |
| **hybrid** | 返回文件 | 检查白名单 → 代理拉取 | 必须通过 |
| **proxy_only** | 返回文件 | 检查白名单 → 代理拉取 | 必须通过 |

---

## 白名单与同步策略的关系

```
全局白名单（基线）
    │
    ├── 同步策略 A（无 include）
    │       └── 使用全局白名单作为过滤条件
    │
    ├── 同步策略 B（有 include=["postgis"]）
    │       └── 策略级 include 与全局白名单取交集
    │           结果: ["postgis"]
    │
    └── 同步策略 C（有 include=["other_ext"]）
            └── 策略级 include 与全局白名单取交集
                结果: []（无交集，不同步任何包）
```

---

## 安全设计

1. **安全默认**: 白名单为空时拒绝所有同步和代理操作
2. **失败安全**: 白名单查询失败时拒绝所有操作
3. **最小权限**: 只允许白名单中的扩展被下载
4. **无绕过**: 代理模式不能绕过白名单检查
