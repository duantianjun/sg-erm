# 自定义扩展发布文档

## 概述

自定义扩展发布 API 提供发布者管理、扩展上传和发布功能。

**基础路径**: `/api/v1/publish`

**代码位置**: [app/api/publish.py](file:///e:/stackgres/sg-erm/app/api/publish.py)

---

## 接口列表

| 路径 | 方法 | 描述 | 权限 |
|------|------|------|------|
| `/publishers` | GET | 自定义发布者列表 | 管理员 |
| `/publishers` | POST | 创建自定义发布者 | 管理员 |
| `/publishers/{publisher_id}` | DELETE | 删除自定义发布者 | 管理员 |
| `/upload` | POST | 上传并发布自定义扩展 | 管理员 |
| `/extensions` | GET | 已发布的自定义扩展列表 | 管理员 |

---

## 详细接口说明

### 1. 发布者列表

**路径**: `GET /api/v1/publish/publishers`

**响应格式**:
```json
{
    "code": 0,
    "data": [
        {
            "id": "uuid",
            "name": "my-company",
            "display_name": "My Company",
            "public_key": "-----BEGIN PUBLIC KEY-----\n...",
            "is_custom": true,
            "created_at": "2026-07-23T10:30:00Z"
        }
    ],
    "count": 1
}
```

**相关代码**: [publish.py:40-64](file:///e:/stackgres/sg-erm/app/api/publish.py#L40-L64)

---

### 2. 创建发布者

**路径**: `POST /api/v1/publish/publishers`

**请求体**:
```json
{
    "name": "my-company",
    "display_name": "My Company"
}
```

**响应格式**:
```json
{
    "code": 0,
    "message": "创建成功",
    "data": {
        "id": "uuid",
        "name": "my-company",
        "display_name": "My Company",
        "public_key": "-----BEGIN PUBLIC KEY-----\n..."
    },
    "count": 1
}
```

**流程逻辑**:
1. 检查发布者名称是否已存在
2. 自动生成 RSA 密钥对
3. 存储发布者信息（仅存储公钥，私钥存储在安全位置）
4. 返回公钥

**相关代码**: [publish.py:67-93](file:///e:/stackgres/sg-erm/app/api/publish.py#L67-L93)

---

### 3. 删除发布者

**路径**: `DELETE /api/v1/publish/publishers/{publisher_id}`

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

**限制**:
- 只能删除自定义发布者（`is_custom=True`）
- 删除发布者会级联删除其关联的自定义扩展

**相关代码**: [publish.py:96-116](file:///e:/stackgres/sg-erm/app/api/publish.py#L96-L116)

---

### 4. 上传扩展

**路径**: `POST /api/v1/publish/upload`

**请求参数**（Form Data）:
| 参数 | 类型 | 必填 | 描述 |
|------|------|------|------|
| `publisher_id` | string | 是 | 发布者 ID |
| `ext_name` | string | 是 | 扩展名称 |
| `version` | string | 是 | 版本号 |
| `flavor` | string | 否 | 风味: pg/bf，默认 pg |
| `pg_version` | string | 是 | PostgreSQL 版本，如 16.4 |
| `arch` | string | 否 | 架构，默认 x86_64 |
| `os_name` | string | 否 | 操作系统，默认 linux |
| `build_num` | string | 否 | 构建号 |
| `channel` | string | 否 | 通道: stable/beta/dev，默认 stable |
| `description` | string | 否 | 扩展描述 |
| `license_str` | string | 否 | 许可证 |
| `tags` | string | 否 | 标签，逗号分隔 |
| `tgz_file` | file | 是 | .tgz 扩展包文件 |

**响应格式**:
```json
{
    "code": 0,
    "message": "发布成功",
    "data": {
        "package_path": "my-company/x86_64/linux/myext-1.0-pg-pg16.4.tar",
        "ext_name": "myext",
        "version": "1.0",
        "publisher": "uuid"
    },
    "count": 1
}
```

**流程逻辑**:
1. 验证文件类型（必须是 .tgz）
2. 保存上传文件到临时目录
3. 校验 .tgz（检查 .control 文件）
4. 用发布者私钥签名 → 生成 .sha256 文件
5. 打包为 .tar（.sha256 + .tgz）
6. 写入本地存储
7. 更新 index.json 和数据库

**相关代码**: [publish.py:121-213](file:///e:/stackgres/sg-erm/app/api/publish.py#L121-L213)

---

### 5. 已发布扩展列表

**路径**: `GET /api/v1/publish/extensions?page=1&limit=20`

**查询参数**:
| 参数 | 类型 | 必填 | 描述 |
|------|------|------|------|
| `page` | int | 否 | 页码，默认 1 |
| `limit` | int | 否 | 每页条数，默认 20 |
| `publisher_id` | string | 否 | 发布者过滤 |

**响应格式**:
```json
{
    "code": 0,
    "data": [
        {
            "id": "uuid",
            "name": "myext",
            "description": "My custom extension",
            "publisher": "my-company",
            "publisher_id": "uuid",
            "license": "MIT",
            "version_count": 2,
            "build_count": 5,
            "updated_at": "2026-07-23T10:30:00Z"
        }
    ],
    "count": 10
}
```

**相关代码**: [publish.py:217-287](file:///e:/stackgres/sg-erm/app/api/publish.py#L217-L287)

---

## 发布服务核心流程

**代码位置**: [app/services/publish_service.py](file:///e:/stackgres/sg-erm/app/services/publish_service.py)

```
上传 .tgz 文件
    │
    ▼
1. 验证 .tgz 文件（检查 .control 文件）
    │
    ▼
2. 使用发布者私钥签名（生成 .sha256）
    │
    ▼
3. 打包为 .tar（.sha256 + .tgz）
    │
    ▼
4. 写入本地存储
    │
    ▼
5. 更新数据库（Extension/ExtensionVersion/ExtensionBuild）
    │
    ▼
6. 更新本地 index.json
```

---

## 数据模型

### Publisher
| 字段 | 类型 | 描述 |
|------|------|------|
| `id` | string | UUID 主键 |
| `name` | string | 发布者名称（唯一标识） |
| `display_name` | string | 显示名称 |
| `public_key` | string | RSA 公钥 |
| `is_custom` | bool | 是否自定义发布者 |

**代码位置**: [app/models/publisher.py](file:///e:/stackgres/sg-erm/app/models/publisher.py)

---

## 自定义扩展与官方扩展的区别

| 特性 | 官方扩展 | 自定义扩展 |
|------|---------|-----------|
| `is_custom` | false | true |
| 来源 | 上游仓库同步 | 手动上传 |
| 发布者 | 官方发布者 | 自定义发布者 |
| 签名 | 官方签名 | 自定义签名 |
| 更新方式 | 同步任务 | 重新上传 |
