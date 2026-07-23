# 仪表盘文档

## 概述

仪表盘 API 提供系统统计数据和缓存管理功能。

**基础路径**: `/api/v1/dashboard`

**代码位置**: [app/api/dashboard.py](file:///e:/stackgres/sg-erm/app/api/dashboard.py)

---

## 接口列表

| 路径 | 方法 | 描述 | 权限 |
|------|------|------|------|
| `/stats` | GET | 仪表盘统计数据 | 需认证 |
| `/cache/evict` | POST | 手动触发缓存淘汰 | 管理员 |

---

## 详细接口说明

### 1. 统计数据

**路径**: `GET /api/v1/dashboard/stats`

**响应格式**:
```json
{
    "code": 0,
    "data": {
        "extensions": {
            "total": 50,
            "custom": 5
        },
        "packages": {
            "total": 500,
            "cached": 400
        },
        "sources": {
            "total": 2,
            "enabled": 1
        },
        "whitelist": {
            "total": 10
        },
        "sync": {
            "total_tasks": 20,
            "running": 0,
            "recent": [
                {
                    "id": "uuid",
                    "source_name": "test-sync-source",
                    "status": "completed",
                    "total": 100,
                    "downloaded": 50,
                    "failed": 0,
                    "started_at": "2026-07-23T10:30:00Z"
                }
            ]
        },
        "disk": {
            "total_bytes": 107374182400,
            "used_bytes": 21474836480,
            "free_bytes": 85899345920,
            "usage_percent": 20.0,
            "repo_size_bytes": 1073741824,
            "file_count": 400
        },
        "proxy_mode": "hybrid"
    },
    "count": 1
}
```

**统计项说明**:
| 类别 | 字段 | 描述 |
|------|------|------|
| extensions | total | 扩展总数 |
| extensions | custom | 自定义扩展数 |
| packages | total | 包总数（构建数） |
| packages | cached | 已缓存的包数 |
| sources | total | 仓库源总数 |
| sources | enabled | 启用的仓库源数 |
| whitelist | total | 白名单条目数 |
| sync | total_tasks | 同步任务总数 |
| sync | running | 运行中的任务数 |
| sync | recent | 最近 5 个任务 |
| disk | total_bytes | 磁盘总容量 |
| disk | used_bytes | 已用容量 |
| disk | free_bytes | 可用容量 |
| disk | usage_percent | 使用百分比 |
| disk | repo_size_bytes | 仓库目录大小 |
| disk | file_count | 仓库文件数 |

**相关代码**: [dashboard.py:32-133](file:///e:/stackgres/sg-erm/app/api/dashboard.py#L32-L133)

---

### 2. 缓存淘汰

**路径**: `POST /api/v1/dashboard/cache/evict?mode=full`

**查询参数**:
| 参数 | 类型 | 必填 | 描述 |
|------|------|------|------|
| `mode` | string | 否 | 淘汰模式: full/disk/ttl/versions，默认 full |

**响应格式**:
```json
{
    "code": 0,
    "message": "缓存淘汰完成",
    "data": {
        "disk_threshold": {"removed": 10},
        "ttl": {"removed": 5},
        "old_versions": {"removed": 3},
        "disk_after": {
            "usage_percent": 65.0,
            "repo_size_bytes": 536870912
        }
    },
    "count": 1
}
```

**淘汰模式说明**:
| 模式 | 描述 |
|------|------|
| `full` | 全部策略（磁盘阈值 + TTL + 版本保留） |
| `disk` | 仅磁盘阈值 |
| `ttl` | 仅 TTL |
| `versions` | 仅版本保留 |

**相关代码**: [dashboard.py:168-208](file:///e:/stackgres/sg-erm/app/api/dashboard.py#L168-L208)

---

## 缓存淘汰机制

**代码位置**: [app/services/cache_eviction.py](file:///e:/stackgres/sg-erm/app/services/cache_eviction.py)

### 三种淘汰策略

#### 1. 磁盘阈值淘汰
- 当磁盘使用率超过 `cache_max_disk_usage`（默认 80%）时触发
- 删除最久未访问的包，直到降至 `cache_target_disk_usage`（默认 70%）
- 使用 `last_accessed` 字段判断访问时间

#### 2. TTL 淘汰
- 删除超过 `cache_ttl_days`（默认 7 天）未访问的包
- 使用 `last_accessed` 字段判断

#### 3. 版本保留淘汰
- 每个扩展只保留 `cache_keep_versions`（默认 3）个最新版本
- 删除旧版本的包

---

## 磁盘用量计算

**代码位置**: [dashboard.py:136-165](file:///e:/stackgres/sg-erm/app/api/dashboard.py#L136-L165)

```python
def _get_disk_usage(path: Path) -> dict:
    # 文件系统使用情况
    usage = shutil.disk_usage(str(path))
    
    # 仓库目录下文件总大小
    total_size = 0
    file_count = 0
    for f in path.rglob("*"):
        if f.is_file():
            total_size += f.stat().st_size
            file_count += 1
```

---

## 配置参数

**代码位置**: [app/config.py](file:///e:/stackgres/sg-erm/app/config.py)

| 参数 | 默认值 | 描述 |
|------|--------|------|
| `cache_max_disk_usage` | 80 | 磁盘使用率阈值（%） |
| `cache_target_disk_usage` | 70 | 淘汰后回落到的使用率（%） |
| `cache_ttl_days` | 7 | TTL（天） |
| `cache_keep_versions` | 3 | 每个扩展保留的版本数 |
| `proxy_mode` | hybrid | 代理模式: hybrid/strict/proxy_only |
