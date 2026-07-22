# 仓库文件浏览器设计文档

## 概述

在 SG-ERM 中新增"仓库文件"页面，让用户浏览同步后本地仓库中缓存的扩展包文件（`.tar`），支持树形/扁平两种视图，并可对包进行删除、重新下载、SHA256 验证操作，以及磁盘一致性检查。

## 背景

当前同步后的数据只能通过"扩展目录"页面查看扩展级别的信息（名称、描述、发布者等），无法直观查看本地 `repo/` 目录下实际存储的 `.tar` 包文件。用户需要一个文件浏览器来管理和验证同步产出的物理文件。

## 数据来源

采用混合模式（方案 C）：

- **主查询走数据库**：从 `ExtensionBuild` 表查询 `cached=True` 的记录，JOIN Extension/Version/Publisher 获取完整元数据
- **文件系统校验**：操作时检查文件是否存在；提供"一致性检查"功能扫描文件系统与数据库的差异

## 页面设计

### 导航入口

- 左侧导航栏新增"仓库文件"菜单项
- 位于"自定义扩展"和"全局白名单"之间
- 路由 `/repo-files`，模板 `repo_files.html`

### 布局结构

左右分栏，比例 3:7：

```
┌─────────────────────────────────────────────────────┐
│ [树形/扁平 切换]  [一致性检查]  [刷新]               │
├──────────────┬──────────────────────────────────────┤
│  树形视图     │  包列表表格                           │
│  (3/7 宽度)  │  (7/7 宽度)                          │
│              │  路径面包屑（树形模式时显示）          │
│  ▸ com.ongres│  ┌──────────────────────────────┐   │
│    ▸ x86_64  │  │ 包名 | 版本 | PG | 大小 | 操作│   │
│      ▸ linux  │  │ ...                          │   │
│              │  └──────────────────────────────┘   │
└──────────────┴──────────────────────────────────────┘
```

#### 树形视图

- 三级结构：`发布者 / 架构 / 操作系统`
- 每个节点显示该层级的包数量（如 `com.ongres (42)`）
- 点击叶子节点刷新右侧包列表，只显示该路径下的包

#### 扁平表格

- 所有包在一个 layui table 中
- 列定义：

| 列 | 字段 | 宽度 | 说明 |
|----|------|------|------|
| 发布者 | publisher | 120 | |
| 架构 | arch | 90 | |
| 操作系统 | os | 80 | |
| 包名 | package_name | 200 | 从 package_path 解析 |
| 扩展名 | extension_name | 120 | JOIN Extension |
| 版本 | version | 80 | JOIN ExtensionVersion |
| PG版本 | postgres_version | 90 | |
| 大小 | package_size | 100 | 格式化为 MB/KB |
| SHA256 | sha256 | 120 | 显示前12位，hover 显示全部 |
| 缓存状态 | cached | 80 | 绿色"已缓存"/红色"文件缺失" |
| 操作 | - | 200 | 验证/重下载/删除 |

- 支持按 publisher、arch、os、关键词筛选
- 分页，默认每页 20 条

## API 设计

新增 `app/api/repo_files.py`，路由前缀 `/api/v1/repo-files`，router 级别 `require_auth`。

### 接口列表

| 接口 | 方法 | 路径 | 说明 |
|------|------|------|------|
| 包列表 | GET | `/api/v1/repo-files/packages` | 分页查询，支持 publisher/arch/os/keyword 参数 |
| 目录树 | GET | `/api/v1/repo-files/tree` | 返回聚合结构 `[{publisher, arch, os, count}]` |
| 删除包 | DELETE | `/api/v1/repo-files/packages/{build_id}` | 删除磁盘文件 + 更新 cached=False |
| 重新下载 | POST | `/api/v1/repo-files/packages/{build_id}/redownload` | 从上游重新下载该包 |
| SHA256 验证 | POST | `/api/v1/repo-files/packages/{build_id}/verify` | 计算文件 SHA256 与数据库比对 |
| 一致性检查 | POST | `/api/v1/repo-files/consistency-check` | 扫描文件系统与数据库比对 |

### 包列表接口返回结构

```json
{
  "code": 0,
  "count": 42,
  "data": [{
    "build_id": "uuid",
    "publisher": "com.ongres",
    "arch": "x86_64",
    "os": "linux",
    "package_name": "postgis-3.4-pg16.4",
    "extension_name": "postgis",
    "version": "3.4",
    "postgres_version": "16.4",
    "package_path": "com.ongres/x86_64/linux/postgis-3.4-pg16.4.tar",
    "package_size": 12345678,
    "sha256": "abc123...",
    "cached": true,
    "file_exists": true
  }]
}
```

### 目录树接口返回结构

```json
{
  "code": 0,
  "data": [{
    "publisher": "com.ongres",
    "children": [{
      "arch": "x86_64",
      "children": [{
        "os": "linux",
        "count": 42
      }]
    }]
  }]
}
```

### 一致性检查接口返回结构

```json
{
  "code": 0,
  "data": [{
    "missing_files": [{
      "build_id": "uuid",
      "package_path": "com.ongres/x86_64/linux/xxx.tar",
      "extension_name": "postgis"
    }],
    "orphan_files": [{
      "file_path": "com.ongres/x86_64/linux/orphan.tar",
      "file_size": 12345
    }]
  }]
}
```

## 操作交互

### 删除包

1. 点击操作列"删除"按钮
2. `layer.confirm` 确认对话框
3. 调用 `DELETE /api/v1/repo-files/packages/{build_id}`
4. 后端：`os.remove(repo_dir / package_path)` → 更新 `ExtensionBuild.cached = False` → 写 `AuditLog`
5. 前端：刷新表格，提示"已删除 xxx.tar，缓存已清除"

### 重新下载

1. 点击"重新下载"按钮
2. 按钮变为 loading 状态"下载中..."
3. 调用 `POST /api/v1/repo-files/packages/{build_id}/redownload`
4. 后端：从 `ExtensionBuild` 元数据重建上游 URL → 调用 `SyncEngine._download_single_package()`
5. 成功：`cached = True`，按钮恢复，行状态变为"已缓存"
6. 失败：按钮恢复可点击，提示错误信息

### SHA256 验证

1. 点击"验证"按钮
2. 调用 `POST /api/v1/repo-files/packages/{build_id}/verify`
3. 后端：`hashlib.sha256(open(file, 'rb').read()).hexdigest()` 与 `ExtensionBuild.sha256` 比对
4. 返回结果在行内显示：绿色"匹配"或红色"不匹配"

### 一致性检查

1. 点击顶部"一致性检查"按钮
2. 弹窗显示进度
3. 调用 `POST /api/v1/repo-files/consistency-check`
4. 后端：`os.walk(repo_dir)` 收集所有 `.tar` 路径 → 与数据库 `package_path` 集合做差集
5. 完成后展示两列结果：
   - 左侧："数据库有记录但文件缺失"（可勾选 → 删除数据库记录）
   - 右侧："磁盘有文件但数据库无记录"（可勾选 → 删除文件）
6. 孤儿文件只做删除，不自动导入数据库

## 数据流

### 包列表查询

```
ExtensionBuild (cached=True)
  JOIN ExtensionVersion → version, channel
  JOIN Extension → name, description
  JOIN Publisher → publisher name
WHERE cached = True
  AND [publisher = ?] [AND arch = ?] [AND os = ?]
ORDER BY package_path
LIMIT ? OFFSET ?
```

文件存在性校验：对每条结果检查 `os.path.exists(repo_dir / package_path)`，`file_exists` 字段反映真实状态。如果 `cached=True` 但 `file_exists=False`，缓存状态列显示红色"文件缺失"。

### 一致性检查流程

```
1. os.walk(repo_dir) → 收集所有 .tar 相对路径 → disk_files (set)
2. SELECT package_path FROM extension_build WHERE cached=True → db_files (set)
3. missing_files = db_files - disk_files  (数据库有但磁盘没有)
4. orphan_files = disk_files - db_files  (磁盘有但数据库没有)
5. 返回两个列表
```

## 错误处理

| 场景 | HTTP 状态 | 处理 |
|------|-----------|------|
| 文件不存在时删除/验证 | 404 | 返回"文件不存在" |
| 重新下载时上游不可达 | 502 | 返回错误信息，按钮恢复 |
| 一致性检查超时 | 200 | 30 秒超时，返回部分结果 + 提示"仓库较大，已检查 N 个文件" |
| 数据库查询失败 | 500 | 返回错误信息 |

所有写操作（删除/重下载/验证）记 `AuditLog`，action 分别为：
- `repo_file_delete`
- `repo_file_redownload`
- `repo_file_verify`

## 涉及文件

### 新增文件

| 文件 | 说明 |
|------|------|
| `app/api/repo_files.py` | API 路由 |
| `app/templates/repo_files.html` | 页面模板 |

### 修改文件

| 文件 | 修改 |
|------|------|
| `app/main.py` | 注册 `/repo-files` 路由 + 引入 repo_files router |
| `app/templates/base.html` | 导航栏新增"仓库文件"菜单项 |

## 测试要点

1. 树形视图三级展开/折叠正确
2. 点击树节点筛选右侧包列表
3. 扁平/树形视图切换
4. 删除包后文件消失 + cached 状态更新
5. 重新下载后文件恢复 + cached=True
6. SHA256 验证匹配/不匹配场景
7. 一致性检查结果展示（missing + orphan）
8. 一致性检查中勾选删除操作
9. 文件不存在时的 404 处理
10. 审计日志正确记录
