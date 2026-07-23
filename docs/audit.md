# 审计日志文档

## 概述

审计日志 API 提供审计日志的查询和统计功能。

**基础路径**: `/api/v1/audit`

**代码位置**: [app/api/audit.py](file:///e:/stackgres/sg-erm/app/api/audit.py)

---

## 接口列表

| 路径 | 方法 | 描述 | 权限 |
|------|------|------|------|
| `/logs` | GET | 审计日志列表 | 管理员 |
| `/stats` | GET | 审计统计 | 管理员 |

---

## 详细接口说明

### 1. 审计日志列表

**路径**: `GET /api/v1/audit/logs?page=1&limit=20&action=repo_file_delete`

**查询参数**:
| 参数 | 类型 | 必填 | 描述 |
|------|------|------|------|
| `page` | int | 否 | 页码，默认 1 |
| `limit` | int | 否 | 每页条数，默认 20，最大 100 |
| `action` | string | 否 | 动作过滤 |
| `result` | string | 否 | 结果过滤: success/failure |
| `start_date` | string | 否 | 开始日期 YYYY-MM-DD |
| `end_date` | string | 否 | 结束日期 YYYY-MM-DD |

**响应格式**:
```json
{
    "code": 0,
    "data": [
        {
            "id": "uuid",
            "timestamp": "2026-07-23T10:30:00Z",
            "actor": "admin",
            "action": "repo_file_delete",
            "resource": "com.ongres/x86_64/linux/postgis-3.4-pg16.4.tar",
            "detail": null,
            "result": "success",
            "client_ip": "192.168.1.100"
        }
    ],
    "count": 100
}
```

**相关代码**: [audit.py:22-72](file:///e:/stackgres/sg-erm/app/api/audit.py#L22-L72)

---

### 2. 审计统计

**路径**: `GET /api/v1/audit/stats`

**响应格式**:
```json
{
    "code": 0,
    "data": {
        "total": 1000,
        "success": 980,
        "failure": 20,
        "recent_24h": 50
    },
    "count": 1
}
```

**相关代码**: [audit.py:75-109](file:///e:/stackgres/sg-erm/app/api/audit.py#L75-L109)

---

## 数据模型

**AuditLog 模型字段**:
| 字段 | 类型 | 描述 |
|------|------|------|
| `id` | string | UUID 主键 |
| `timestamp` | datetime | 时间戳 |
| `actor` | string | 操作人（用户名） |
| `action` | string | 动作类型 |
| `resource` | string | 操作资源 |
| `detail` | string | 详细信息 |
| `result` | string | 结果: success/failure |
| `client_ip` | string | 客户端 IP |

**代码位置**: [app/models/audit.py](file:///e:/stackgres/sg-erm/app/models/audit.py)

---

## 审计日志自动记录

**代码位置**: [app/middleware/audit.py](file:///e:/stackgres/sg-erm/app/middleware/audit.py)

审计中间件自动记录所有 API 请求：

```
请求进入
    │
    ▼
记录请求信息（actor, action, resource, client_ip）
    │
    ▼
执行请求处理
    │
    ▼
记录响应结果（success/failure）
```

**自动记录的信息**:
- 请求方法和路径
- 认证用户（如果有）
- 客户端 IP
- 请求结果（成功/失败）

---

## 手动记录审计日志

在业务逻辑中手动记录审计日志：

```python
audit = AuditLog(
    actor=current_user.username,
    action="repo_file_delete",
    resource=build.package_path,
    result="success",
)
db.add(audit)
await db.commit()
```

**常见动作类型**:
| 动作 | 描述 |
|------|------|
| `repo_file_delete` | 删除仓库文件 |
| `repo_file_redownload` | 重新下载包 |
| `repo_file_verify` | SHA256 验证 |
| `user_create` | 创建用户 |
| `user_delete` | 删除用户 |
| `password_change` | 修改密码 |

---

## 审计日志存储

审计日志存储在 SQLite 数据库中，与其他业务数据分开管理。

**保留策略**:
- 目前没有自动清理机制
- 需要定期手动清理或实现归档策略

---

## 安全审计覆盖范围

| 模块 | 审计内容 |
|------|---------|
| 认证 | 用户登录、登出、密码修改、令牌刷新 |
| 用户管理 | 创建、删除用户 |
| 仓库源 | 创建、更新、删除仓库源 |
| 白名单 | 添加、删除白名单条目 |
| 同步任务 | 触发、取消同步任务 |
| 仓库文件 | 删除、重新下载、验证包 |
| 自定义发布 | 上传扩展、管理发布者 |
