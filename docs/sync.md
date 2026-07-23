# 同步任务文档

## 概述

同步任务 API 提供同步任务的触发、取消、列表查询以及同步策略的管理功能。

**基础路径**: `/api/v1/sync`

**代码位置**: [app/api/sync.py](file:///e:/stackgres/sg-erm/app/api/sync.py)

---

## 接口列表

| 路径 | 方法 | 描述 | 权限 |
|------|------|------|------|
| `/tasks` | GET | 同步任务列表 | 管理员 |
| `/tasks/{task_id}` | GET | 同步任务详情 | 管理员 |
| `/trigger` | POST | 触发同步任务 | 管理员 |
| `/cancel/{task_id}` | POST | 取消同步任务 | 管理员 |
| `/policies` | GET | 同步策略列表 | 管理员 |
| `/policies` | POST | 创建同步策略 | 管理员 |
| `/policies/{policy_id}` | PUT | 更新同步策略 | 管理员 |
| `/policies/{policy_id}` | DELETE | 删除同步策略 | 管理员 |

---

## 详细接口说明

### 1. 同步任务列表

**路径**: `GET /api/v1/sync/tasks?page=1&limit=20&status=completed`

**查询参数**:
| 参数 | 类型 | 必填 | 描述 |
|------|------|------|------|
| `page` | int | 否 | 页码，默认 1 |
| `limit` | int | 否 | 每页条数，默认 20 |
| `status` | string | 否 | 状态过滤: pending/running/completed/failed/cancelled |
| `source_id` | string | 否 | 仓库源过滤 |

**响应格式**:
```json
{
    "code": 0,
    "data": [
        {
            "id": "uuid",
            "source_id": "uuid",
            "source_name": "test-sync-source",
            "policy_id": null,
            "status": "completed",
            "total": 100,
            "downloaded": 50,
            "failed": 0,
            "skipped": 50,
            "error_message": null,
            "started_at": "2026-07-23T10:30:00Z",
            "finished_at": "2026-07-23T10:45:00Z"
        }
    ],
    "count": 10
}
```

**相关代码**: [sync.py:35-86](file:///e:/stackgres/sg-erm/app/api/sync.py#L35-L86)

---

### 2. 同步任务详情

**路径**: `GET /api/v1/sync/tasks/{task_id}`

**响应格式**:
```json
{
    "code": 0,
    "data": {
        "id": "uuid",
        "source_id": "uuid",
        "source_name": "test-sync-source",
        "policy_id": "uuid",
        "policy_name": "my-policy",
        "status": "completed",
        "total": 100,
        "downloaded": 50,
        "failed": 0,
        "skipped": 50,
        "error_message": null,
        "started_at": "2026-07-23T10:30:00Z",
        "finished_at": "2026-07-23T10:45:00Z",
        "diff_summary": {
            "total": 100,
            "downloaded": 50,
            "failed": 0,
            "skipped": 50,
            "removed": 0
        }
    },
    "count": 1
}
```

**相关代码**: [sync.py:89-118](file:///e:/stackgres/sg-erm/app/api/sync.py#L89-L118)

---

### 3. 触发同步任务

**路径**: `POST /api/v1/sync/trigger`

**请求体**:
```json
{
    "source_id": "uuid",
    "policy_id": null,
    "dry_run": false
}
```

| 参数 | 类型 | 必填 | 描述 |
|------|------|------|------|
| `source_id` | string | 是 | 仓库源 ID |
| `policy_id` | string | 否 | 同步策略 ID |
| `dry_run` | bool | 否 | 模拟模式，不实际下载，默认 false |

**响应格式**:
```json
{
    "code": 0,
    "message": "同步任务已启动",
    "data": {
        "task_id": "uuid",
        "status": "running",
        "source_name": "test-sync-source",
        "dry_run": false
    },
    "count": 1
}
```

**流程逻辑**:
1. 验证仓库源存在且已启用
2. 创建 SyncTask 记录（状态: running）
3. 在后台异步执行同步流程
4. 返回任务 ID

**相关代码**: [sync.py:121-152](file:///e:/stackgres/sg-erm/app/api/sync.py#L121-L152)

---

### 4. 取消同步任务

**路径**: `POST /api/v1/sync/cancel/{task_id}`

**响应格式**:
```json
{
    "code": 0,
    "message": "取消请求已发送",
    "data": {
        "task_id": "uuid"
    },
    "count": 1
}
```

**限制**:
- 只有状态为 `pending` 或 `running` 的任务可以取消
- 已完成、失败或已取消的任务无法取消

**相关代码**: [sync.py:155-177](file:///e:/stackgres/sg-erm/app/api/sync.py#L155-L177)

---

### 5. 同步策略列表

**路径**: `GET /api/v1/sync/policies`

**响应格式**:
```json
{
    "code": 0,
    "data": [
        {
            "id": "uuid",
            "name": "my-policy",
            "source_id": "uuid",
            "source_name": "test-sync-source",
            "filters": {
                "extensions": {
                    "include": ["postgis"]
                }
            },
            "schedule": "0 2 * * *",
            "enabled": true,
            "bandwidth_limit": "50M",
            "keep_old_versions": 3
        }
    ],
    "count": 1
}
```

**相关代码**: [sync.py:202-240](file:///e:/stackgres/sg-erm/app/api/sync.py#L202-L240)

---

### 6. 创建同步策略

**路径**: `POST /api/v1/sync/policies`

**请求体**:
```json
{
    "name": "my-policy",
    "source_id": "uuid",
    "filters": {
        "extensions": {
            "include": ["postgis"]
        },
        "arch": ["x86_64"],
        "os": ["linux"]
    },
    "schedule": "0 2 * * *",
    "enabled": true,
    "bandwidth_limit": "50M",
    "keep_old_versions": 3
}
```

| 参数 | 类型 | 必填 | 描述 |
|------|------|------|------|
| `name` | string | 是 | 策略名称 |
| `source_id` | string | 是 | 关联的仓库源 ID |
| `filters` | dict | 否 | 过滤配置 |
| `schedule` | string | 否 | Cron 表达式 |
| `enabled` | bool | 否 | 是否启用，默认 true |
| `bandwidth_limit` | string | 否 | 带宽限制 |
| `keep_old_versions` | int | 否 | 保留旧版本数，默认 3 |

**响应格式**:
```json
{
    "code": 0,
    "message": "创建成功",
    "data": {
        "id": "uuid"
    },
    "count": 1
}
```

**流程逻辑**:
1. 创建 SyncPolicy 记录
2. 重新加载定时任务调度器

**相关代码**: [sync.py:243-275](file:///e:/stackgres/sg-erm/app/api/sync.py#L243-L275)

---

### 7. 更新同步策略

**路径**: `PUT /api/v1/sync/policies/{policy_id}`

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

**相关代码**: [sync.py:278-303](file:///e:/stackgres/sg-erm/app/api/sync.py#L278-L303)

---

### 8. 删除同步策略

**路径**: `DELETE /api/v1/sync/policies/{policy_id}`

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

**相关代码**: [sync.py:306-326](file:///e:/stackgres/sg-erm/app/api/sync.py#L306-L326)

---

## 同步引擎核心流程

**代码位置**: [app/services/sync_engine.py](file:///e:/stackgres/sg-erm/app/services/sync_engine.py)

```
触发同步
    │
    ▼
1. 获取上游 index.json
    │
    ▼
2. 获取过滤配置（全局白名单 + 策略级过滤器）
    │
    ▼
3. 收集匹配的包列表
    │
    ▼
4. dry_run 模式检查（是则跳过下载）
    │
    ▼
5. 预写数据库元数据（cached=False）
    │
    ▼
6. 并发下载包（支持断点续传）
    │
    ▼
7. 更新已下载包的缓存状态（cached=True）
    │
    ▼
8. 清理已从上游移除的本地包
    │
    ▼
9. 更新本地 index.json
    │
    ▼
10. 更新任务状态为 completed
```

---

## 数据模型

### SyncPolicy
| 字段 | 类型 | 描述 |
|------|------|------|
| `id` | string | UUID 主键 |
| `name` | string | 策略名称 |
| `source_id` | string | 关联仓库源 ID |
| `filters` | dict | 过滤配置 |
| `schedule` | string | Cron 表达式 |
| `enabled` | bool | 是否启用 |
| `bandwidth_limit` | string | 带宽限制 |
| `keep_old_versions` | int | 保留旧版本数 |

### SyncTask
| 字段 | 类型 | 描述 |
|------|------|------|
| `id` | string | UUID 主键 |
| `source_id` | string | 关联仓库源 ID |
| `policy_id` | string | 关联策略 ID |
| `status` | string | 状态: pending/running/completed/failed/cancelled |
| `total` | int | 总包数 |
| `downloaded` | int | 已下载数 |
| `failed` | int | 失败数 |
| `skipped` | int | 跳过数 |
| `error_message` | string | 错误信息 |
| `started_at` | datetime | 开始时间 |
| `finished_at` | datetime | 完成时间 |
| `diff_summary` | dict | 变更摘要 |

**代码位置**: [app/models/sync.py](file:///e:/stackgres/sg-erm/app/models/sync.py)

---

## 定时同步调度器

**代码位置**: [app/services/scheduler.py](file:///e:/stackgres/sg-erm/app/services/scheduler.py)

**流程**:
1. 服务启动时启动调度器
2. 加载所有启用的 SyncPolicy
3. 根据 schedule（Cron 表达式）注册定时任务
4. 定时任务触发时调用 SyncEngine.run()
5. 策略变更后调用 reload_jobs() 重新加载

---

## 过滤器配置

```json
{
    "arch": ["x86_64", "aarch64"],
    "os": ["linux"],
    "publisher": ["com.ongres"],
    "extensions": {
        "include": ["postgis"],
        "exclude": ["deprecated_ext"]
    }
}
```

| 过滤器 | 类型 | 作用 |
|--------|------|------|
| `arch` | list | 过滤架构 |
| `os` | list | 过滤操作系统 |
| `publisher` | list | 过滤发布者 |
| `extensions.include` | list | 包含的扩展（与白名单取交集） |
| `extensions.exclude` | list | 排除的扩展 |
