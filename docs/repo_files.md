# 仓库文件浏览器文档

## 概述

仓库文件浏览器 API 提供本地仓库中缓存扩展包的浏览、删除、重新下载、SHA256 验证和一致性检查功能。

**基础路径**: `/api/v1/repo-files`

**代码位置**: [app/api/repo_files.py](file:///e:/stackgres/sg-erm/app/api/repo_files.py)

---

## 接口列表

| 路径 | 方法 | 描述 | 权限 |
|------|------|------|------|
| `/packages` | GET | 包列表（分页） | 需认证 |
| `/tree` | GET | 目录树 | 需认证 |
| `/packages/{build_id}` | DELETE | 删除包文件 | 需认证 |
| `/packages/{build_id}/redownload` | POST | 重新下载包 | 需认证 |
| `/packages/{build_id}/verify` | POST | SHA256 验证 | 需认证 |
| `/consistency-check` | POST | 文件系统与数据库一致性检查 | 需认证 |

---

## 详细接口说明

### 1. 包列表

**路径**: `GET /api/v1/repo-files/packages?page=1&limit=20&publisher=com.ongres`

**查询参数**:
| 参数 | 类型 | 必填 | 描述 |
|------|------|------|------|
| `page` | int | 否 | 页码，默认 1 |
| `limit` | int | 否 | 每页条数，默认 20，最大 100 |
| `publisher` | string | 否 | 发布者名称过滤 |
| `arch` | string | 否 | 架构过滤 |
| `os` | string | 否 | 操作系统过滤 |
| `keyword` | string | 否 | 关键词搜索 |

**响应格式**:
```json
{
    "code": 0,
    "data": [
        {
            "build_id": "uuid",
            "publisher": "com.ongres",
            "arch": "x86_64",
            "os": "linux",
            "package_name": "postgis-3.4-pg16.4.tar",
            "extension_name": "postgis",
            "version": "3.4",
            "postgres_version": "16.4",
            "flavor": "pg",
            "build": "1",
            "package_path": "com.ongres/x86_64/linux/postgis-3.4-pg16.4.tar",
            "package_size": 12345678,
            "sha256": "abc123def456...",
            "cached": true,
            "file_exists": true
        }
    ],
    "count": 100
}
```

**相关代码**: [repo_files.py:61-141](file:///e:/stackgres/sg-erm/app/api/repo_files.py#L61-L141)

---

### 2. 目录树

**路径**: `GET /api/v1/repo-files/tree`

**响应格式**:
```json
{
    "code": 0,
    "data": [
        {
            "publisher": "com.ongres",
            "count": 50,
            "children": [
                {
                    "arch": "x86_64",
                    "count": 30,
                    "children": [
                        {"os": "linux", "count": 30}
                    ]
                },
                {
                    "arch": "aarch64",
                    "count": 20,
                    "children": [
                        {"os": "linux", "count": 20}
                    ]
                }
            ]
        }
    ]
}
```

**结构**: publisher → arch → os（三级嵌套）

**相关代码**: [repo_files.py:146-191](file:///e:/stackgres/sg-erm/app/api/repo_files.py#L146-L191)

---

### 3. 删除包

**路径**: `DELETE /api/v1/repo-files/packages/{build_id}`

**响应格式**:
```json
{
    "code": 0,
    "message": "已删除文件，缓存已清除",
    "data": {
        "build_id": "uuid"
    },
    "count": 1
}
```

**流程逻辑**:
1. 查询构建记录
2. 验证路径合法性（防止路径遍历攻击）
3. 删除磁盘文件
4. 更新数据库（cached=False）
5. 写入审计日志

**安全机制**:
- 使用 `_validate_path()` 验证路径在仓库目录内
- 使用 `os.path.normpath()` 规范化路径

**相关代码**: [repo_files.py:195-239](file:///e:/stackgres/sg-erm/app/api/repo_files.py#L195-L239)

---

### 4. 重新下载

**路径**: `POST /api/v1/repo-files/packages/{build_id}/redownload`

**响应格式**:
```json
{
    "code": 0,
    "data": {
        "build_id": "uuid",
        "package_path": "com.ongres/x86_64/linux/postgis-3.4-pg16.4.tar",
        "package_size": 12345678,
        "sha256": "abc123def456...",
        "cached": true
    },
    "count": 1,
    "message": "重新下载成功"
}
```

**流程逻辑**:
1. 查询构建记录及关联的发布者和仓库源
2. 验证路径合法性
3. 从上游重新下载包
4. 计算 SHA256 哈希
5. 更新数据库（cached=True, package_size, sha256）
6. 写入审计日志

**相关代码**: [repo_files.py:244-332](file:///e:/stackgres/sg-erm/app/api/repo_files.py#L244-L332)

---

### 5. SHA256 验证

**路径**: `POST /api/v1/repo-files/packages/{build_id}/verify`

**响应格式**:
```json
{
    "code": 0,
    "data": {
        "matched": true,
        "computed": "abc123def456...",
        "stored": "abc123def456..."
    },
    "count": 1
}
```

**流程逻辑**:
1. 查询构建记录
2. 验证路径合法性
3. 计算文件 SHA256
4. 与数据库记录比对
5. 写入审计日志

**相关代码**: [repo_files.py:337-391](file:///e:/stackgres/sg-erm/app/api/repo_files.py#L337-L391)

---

### 6. 一致性检查

**路径**: `POST /api/v1/repo-files/consistency-check`

**响应格式**:
```json
{
    "code": 0,
    "data": {
        "missing_files": [
            {
                "build_id": "uuid",
                "package_path": "com.ongres/x86_64/linux/postgis-3.4-pg16.4.tar",
                "extension_name": "postgis"
            }
        ],
        "orphan_files": [
            {
                "file_path": "unknown/path/file.tar",
                "file_size": 123456
            }
        ]
    },
    "count": 1
}
```

**流程逻辑**:
1. 扫描文件系统收集所有 .tar 文件
2. 查询数据库中 cached=True 的记录
3. 计算差集：
   - `missing_files`: 数据库有但磁盘没有的文件
   - `orphan_files`: 磁盘有但数据库没有的文件

**相关代码**: [repo_files.py:396-463](file:///e:/stackgres/sg-erm/app/api/repo_files.py#L396-L463)

---

## 路径验证机制

**代码位置**: [repo_files.py:39-56](file:///e:/stackgres/sg-erm/app/api/repo_files.py#L39-L56)

```python
def _validate_path(relative_path: str, base_dir: str) -> str:
    full_path = os.path.normpath(os.path.join(base_dir, relative_path))
    base_path = os.path.normpath(base_dir)
    if not full_path.startswith(base_path + os.sep) and full_path != base_path:
        raise ValueError(f"非法路径: {relative_path}")
    return full_path
```

**安全机制**:
- 使用 `os.path.normpath()` 规范化路径
- 验证路径在仓库目录内
- 防止路径遍历攻击（如 `../../../etc/passwd`）

---

## 文件存储结构

```
repo/
├── com.ongres/
│   ├── x86_64/
│   │   └── linux/
│   │       └── postgis-3.4-pg16.4.tar
│   └── aarch64/
│       └── linux/
│           └── postgis-3.4-pg16.4.tar
├── my-company/
│   └── x86_64/
│       └── linux/
│           └── myext-1.0-pg16.4.tar
└── v2/
    └── index.json
```
