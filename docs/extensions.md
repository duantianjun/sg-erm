# 扩展目录文档

## 概述

扩展目录 API 提供扩展列表的分页查询、搜索过滤以及扩展详情查询功能。

**基础路径**: `/api/v1/extensions`

**代码位置**: [app/api/extensions.py](file:///e:/stackgres/sg-erm/app/api/extensions.py)

---

## 接口列表

| 路径 | 方法 | 描述 | 权限 |
|------|------|------|------|
| `/` | GET | 扩展列表（分页） | 需认证 |
| `/{name}` | GET | 扩展详情（含版本和构建信息） | 需认证 |

---

## 详细接口说明

### 1. 扩展列表

**路径**: `GET /api/v1/extensions?page=1&limit=20&keyword=postgis`

**查询参数**:
| 参数 | 类型 | 必填 | 描述 |
|------|------|------|------|
| `page` | int | 否 | 页码，默认 1 |
| `limit` | int | 否 | 每页条数，默认 20，最大 100 |
| `keyword` | string | 否 | 搜索关键词（匹配名称或描述） |
| `publisher` | string | 否 | 发布者过滤 |

**响应格式**:
```json
{
    "code": 0,
    "data": [
        {
            "id": "uuid",
            "name": "postgis",
            "description": "PostGIS spatial database extension",
            "publisher": "com.ongres",
            "license": "GPL-2.0",
            "is_custom": false,
            "version_count": 5,
            "build_count": 20,
            "updated_at": "2026-07-23T10:30:00Z"
        }
    ],
    "count": 50
}
```

**相关代码**: [extensions.py:23-96](file:///e:/stackgres/sg-erm/app/api/extensions.py#L23-L96)

---

### 2. 扩展详情

**路径**: `GET /api/v1/extensions/{name}`

**响应格式**:
```json
{
    "code": 0,
    "data": {
        "id": "uuid",
        "name": "postgis",
        "description": "PostGIS spatial database extension",
        "abstract": "PostGIS brings spatial data support to PostgreSQL",
        "publisher": "com.ongres",
        "license": "GPL-2.0",
        "url": "https://postgis.net",
        "source_url": "https://github.com/postgis/postgis",
        "tags": ["spatial", "gis"],
        "channels": {"stable": "3.4"},
        "is_custom": false,
        "versions": [
            {
                "version": "3.4",
                "channel": "stable",
                "builds": [
                    {
                        "postgres_version": "16.4",
                        "arch": "x86_64",
                        "os": "linux",
                        "flavor": "pg",
                        "build": "1",
                        "package_path": "com.ongres/x86_64/linux/postgis-3.4-pg16.4.tar",
                        "package_size": 12345678,
                        "cached": true,
                        "verified": true
                    }
                ]
            }
        ],
        "created_at": "2026-07-21T00:00:00Z",
        "updated_at": "2026-07-23T10:30:00Z"
    },
    "count": 1
}
```

**相关代码**: [extensions.py:99-173](file:///e:/stackgres/sg-erm/app/api/extensions.py#L99-L173)

---

## 数据模型

### Extension
| 字段 | 类型 | 描述 |
|------|------|------|
| `id` | string | UUID 主键 |
| `name` | string | 扩展名称 |
| `publisher_id` | string | 关联发布者 ID |
| `source_id` | string | 关联仓库源 ID |
| `description` | string | 描述 |
| `abstract` | string | 摘要 |
| `license` | string | 许可证 |
| `url` | string | 扩展官网 |
| `source_url` | string | 源码地址 |
| `tags` | list | 标签列表 |
| `channels` | dict | 通道信息 |
| `is_custom` | bool | 是否自定义扩展 |

### ExtensionVersion
| 字段 | 类型 | 描述 |
|------|------|------|
| `id` | string | UUID 主键 |
| `extension_id` | string | 关联扩展 ID |
| `version` | string | 版本号 |
| `channel` | string | 通道: stable/beta/dev |

### ExtensionBuild
| 字段 | 类型 | 描述 |
|------|------|------|
| `id` | string | UUID 主键 |
| `version_id` | string | 关联版本 ID |
| `postgres_version` | string | PostgreSQL 版本 |
| `arch` | string | 架构 |
| `os` | string | 操作系统 |
| `flavor` | string | 风味: pg/bf |
| `build` | string | 构建号 |
| `package_path` | string | 包相对路径 |
| `package_size` | int | 包大小 |
| `cached` | bool | 是否已缓存 |
| `verified` | bool | 是否已验证 |
| `last_accessed` | datetime | 最后访问时间 |

**代码位置**: [app/models/extension.py](file:///e:/stackgres/sg-erm/app/models/extension.py)

---

## 数据关系

```
Extension (扩展)
    │
    ├── ExtensionVersion (版本)
    │       │
    │       └── ExtensionBuild (构建)
    │               │
    │               ├── package_path
    │               ├── package_size
    │               ├── cached
    │               └── last_accessed
    │
    └── Publisher (发布者)
```

---

## 扩展包命名规则

**代码位置**: [app/services/naming.py](file:///e:/stackgres/sg-erm/app/services/naming.py)

**包名格式**: `{extension_name}-{version}-{flavor}-pg{postgres_version}`

**示例**: `postgis-3.4-pg-pg16.4`

**存储路径**: `{publisher}/{arch}/{os}/{package_name}.tar`

**示例**: `com.ongres/x86_64/linux/postgis-3.4-pg-pg16.4.tar`
