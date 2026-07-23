# 扩展管理 Tab 融合设计

> 日期: 2026-07-23
> 状态: 已确认

## 1. 背景与目标

### 1.1 问题

当前系统有三个功能高度重叠的菜单：

- **扩展目录** (`/extensions`) — 按 Extension 聚合的逻辑视图，仅查看
- **仓库文件** (`/repo-files`) — 按 ExtensionBuild 展开的物理视图，支持文件操作
- **自定义扩展发布** (`/publish`) — 自定义扩展上传和发布者管理

三者数据底层相同（Extension → ExtensionVersion → ExtensionBuild），但分散在不同页面，用户需要频繁跳转才能完成"查看扩展 → 验证文件 → 删除包"等操作。

### 1.2 目标

将三个页面合并为一个统一的 **"扩展管理"** 页面，通过 Tab 切换，减少跳转、增强差异、提升操作效率。

## 2. 整体架构

```
扩展管理 (/extensions)
├── Tab 1: 扩展目录（逻辑视图 — 按扩展聚合）
│   ├── 列表（含批量删除）
│   └── 详情页（含构建级文件操作 + 批量删除构建）
│
├── Tab 2: 仓库文件（物理视图 — 按包文件展开）
│   ├── 列表（增加扩展元数据列）
│   ├── 树形目录浏览
│   └── 一致性检查 / 修复
│
└── Tab 3: 自定义发布（管理自定义扩展）
    ├── 发布者管理
    ├── 上传新扩展
    └── 已发布扩展列表
```

**移除的菜单**：扩展目录、仓库文件、自定义扩展发布 → 合并为"扩展管理"

## 3. 详细设计

### 3.1 Tab 1: 扩展目录（增强后）

#### 列表字段

| 列 | 字段 | 宽度 | 说明 |
|---|---|---|---|
| 复选框 | - | 40 | checkbox，用于批量操作 |
| 扩展名 | name | 180 | 排序 |
| 发布者 | publisher | 120 | |
| 版本数 | version_count | 90 | |
| 构建数 | build_count | 90 | |
| 缓存构建数 | cached_build_count | 100 | 新增，格式：已缓存/总构建数 |
| 磁盘大小 | total_size | 100 | 新增，该扩展所有缓存包总大小 |
| 类型 | is_custom | 90 | 官方/自定义标签 |
| 描述 | description | min 300 | |
| 许可证 | license | 100 | |
| 操作 | - | 100 | 详情按钮，fixed right |

#### 批量操作

- 列表第一列为 checkbox
- 顶部工具栏增加"批量删除"按钮
- 点击后二次确认，删除选中扩展的：
  - 所有磁盘上的 .tar 文件
  - 数据库记录（Extension → ExtensionVersion → ExtensionBuild 级联删除）
- 删除完成后记录审计日志

#### 详情页增强

在现有详情页的构建表格中，每行增加操作列：

| 操作 | 说明 |
|---|---|
| 验证 | 计算并比对 SHA256 |
| 重下载 | 从上游重新下载 |
| 删除 | 删除单个构建包 |

构建表格首列增加 checkbox，支持批量删除选中构建。

### 3.2 Tab 2: 仓库文件（增强后）

#### 列表新增列

在现有列基础上增加：

| 列 | 字段 | 说明 |
|---|---|---|
| 扩展描述 | description | 从 JOIN Extension 获取 |
| 许可证 | license | 从 JOIN Extension 获取 |

其余功能保持不变：
- 扁平视图 / 树形视图切换
- 一致性检查（含修复）
- 单文件操作（验证、重下载、删除）

### 3.3 Tab 3: 自定义发布

将现有 `publish.html` 内容作为 Tab 3 嵌入，功能不变：
- 发布者管理（创建/删除）
- 上传新扩展表单
- 已发布扩展列表

## 4. 后端 API 变更

### 4.1 修改现有接口

| 接口 | 变更内容 |
|---|---|
| `GET /api/v1/extensions` | 响应增加 `cached_build_count`、`total_size` 字段 |
| `GET /api/v1/repo-files/packages` | 响应增加 `description`、`license` 字段 |

#### `GET /api/v1/extensions` 响应新增字段

```json
{
  "id": "...",
  "name": "postgis",
  "cached_build_count": 5,
  "total_size": 12345678,
  ...
}
```

查询方式：对当前页扩展批量聚合 `ExtensionBuild.cached == True` 的 count 和 `package_size` sum。

#### `GET /api/v1/repo-files/packages` 响应新增字段

```json
{
  "build_id": "...",
  "description": "PostGIS geometry and geography...",
  "license": "GPLv2",
  ...
}
```

查询方式：JOIN Extension 时额外 select `Extension.description`、`Extension.license`。

### 4.2 新增接口

#### `DELETE /api/v1/extensions/batch`

批量删除扩展（含磁盘文件 + 数据库级联删除）。

```json
// Request Body
{
  "ids": ["ext-uuid-1", "ext-uuid-2"]
}

// Response
{
  "code": 0,
  "msg": "已删除 2 个扩展",
  "data": [{"deleted": 2, "failed": 0}]
}
```

流程：
1. 遍历传入的 extension id
2. 查询关联的所有 ExtensionBuild.package_path
3. 删除磁盘上的 .tar 文件
4. 删除数据库记录（Extension 级联删除 Version → Build）
5. 记录审计日志

#### `DELETE /api/v1/extensions/{name}/builds/batch`

批量删除指定扩展的构建包。

```json
// Request Body
{
  "build_ids": ["build-uuid-1", "build-uuid-2"]
}

// Response
{
  "code": 0,
  "msg": "已删除 2 个构建包",
  "data": [{"deleted": 2, "failed": 0}]
}
```

流程：
1. 遍历传入的 build_id
2. 删除磁盘上的 .tar 文件
3. 删除 ExtensionBuild 记录（不删除 Extension/Version）
4. 记录审计日志

## 5. 前端页面结构

### 5.1 统一页面 `extensions.html`

```html
<div class="layui-tab layui-tab-brief" lay-filter="ext-tab">
  <ul class="layui-tab-title">
    <li class="layui-this">扩展目录</li>
    <li>仓库文件</li>
    <li>自定义发布</li>
  </ul>
  <div class="layui-tab-content">
    <!-- Tab 1: 扩展目录 -->
    <div class="layui-tab-item layui-show">
      <!-- 搜索栏 + 批量操作栏 -->
      <!-- 扩展列表表格（含 checkbox） -->
    </div>

    <!-- Tab 2: 仓库文件 -->
    <div class="layui-tab-item">
      <!-- 工具栏 + 搜索 -->
      <!-- 左侧树形视图 + 右侧包列表 -->
    </div>

    <!-- Tab 3: 自定义发布 -->
    <div class="layui-tab-item">
      <!-- 发布者管理 + 上传表单 + 已发布列表 -->
    </div>
  </div>
</div>
```

### 5.2 扩展详情页

保持独立页面 `/extensions/{name}`，增强构建表格：
- 首列 checkbox
- 末列操作列（验证/重下载/删除）
- 顶部"批量删除选中构建"按钮

### 5.3 导航菜单变更

`base.html` 侧边栏：

```
移除：
- 扩展目录 (/extensions)
- 仓库文件 (/repo-files)
- 自定义扩展发布 (/publish)

新增：
- 扩展管理 (/extensions)
```

`main.py` 路由变更：
- `/repo-files` → 重定向到 `/extensions`（兼容旧链接）
- `/publish` → 重定向到 `/extensions`（兼容旧链接）

## 6. 文件变更清单

### 后端

| 文件 | 变更 |
|---|---|
| `app/api/extensions.py` | `list_extensions` 增加缓存统计字段；新增批量删除接口 |
| `app/api/repo_files.py` | `list_packages` 增加扩展元数据字段 |
| `app/main.py` | `/repo-files`、`/publish` 路由改为重定向 |

### 前端

| 文件 | 变更 |
|---|---|
| `app/templates/extensions.html` | 重写为三 Tab 结构，融合三个页面内容 |
| `app/templates/extension_detail.html` | 构建表格增加 checkbox + 操作列 |
| `app/templates/base.html` | 侧边栏菜单合并 |
| `app/templates/repo_files.html` | 内容迁移到 extensions.html Tab 2，文件可删除 |
| `app/templates/publish.html` | 内容迁移到 extensions.html Tab 3，文件可删除 |

## 7. 错误处理

- 批量删除时，单个扩展/构建删除失败不中断整体流程，继续处理后续项
- 返回结果中包含成功数和失败数
- 磁盘文件不存在时跳过删除，仅删除数据库记录
- 审计日志记录每次批量操作的汇总信息

## 8. 测试要点

- 批量删除扩展：验证磁盘文件和数据库记录都被清除
- 批量删除构建：验证只删除 Build 记录，Extension/Version 保留
- 扩展列表缓存统计：验证 cached_build_count 和 total_size 正确
- 仓库文件列表：验证新增的 description/license 字段有值
- Tab 切换：验证三个 Tab 内容独立加载、不互相干扰
- 旧链接兼容：访问 `/repo-files`、`/publish` 自动重定向到 `/extensions`
