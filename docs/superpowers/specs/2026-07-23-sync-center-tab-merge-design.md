# 同步中心 Tab 融合设计

> 日期: 2026-07-23
> 状态: 已确认

## 1. 概述

将「仓库源」、「全局白名单」、「同步任务」（含同步策略）三个独立菜单合并为一个「同步中心」页面，通过 4 个 Tab 切换。与之前「扩展管理」Tab 融合采用相同模式。

### 合并前

侧边栏 7 个菜单项，其中第 3-5 项为各自独立的：
- 同步任务 (`/sync`) — sync.html，已有 2 Tab（同步任务 + 同步策略）
- 仓库源 (`/sources`) — sources.html，单表格页
- 全局白名单 (`/whitelist`) — whitelist.html，说明卡片 + 搜索 + 表格

### 合并后

侧边栏 5 个菜单项，新增「同步中心」一个菜单：
- 仪表盘、扩展管理、**同步中心**、审计日志、系统设置

## 2. Tab 结构

采用 `layui-tab-brief` 结构，4 个 Tab 按配置优先顺序排列。每个 Tab 的 `li` 元素需设置 `lay-id` 属性，用于 `element.tabChange` 切换：

| 序号 | Tab 名称 | lay-id | 默认 | 内容来源 | 数据接口 |
|---|---|---|---|---|---|
| 1 | 仓库源 | `tab-sources` | 是 | sources.html 迁移 | `GET /api/v1/sources` |
| 2 | 全局白名单 | `tab-whitelist` | 否 | whitelist.html 迁移 | `GET /api/v1/whitelist` |
| 3 | 同步任务 | `tab-tasks` | 否 | sync.html 现有 Tab 1 | `GET /api/v1/sync/tasks` |
| 4 | 同步策略 | `tab-policies` | 否 | sync.html 现有 Tab 2 | `GET /api/v1/sync/policies` |

### Tab 1：仓库源

- 顶部：标题行 + "添加仓库源"按钮
- 表格列：名称、URL、状态(启用/禁用)、优先级、健康状态(healthy/degraded/down/unknown 带色标)、最后同步时间、同步结果(success/failed/syncing)、操作(编辑/同步/删除)
- 表单弹窗(`lay-filter="source-form"`)：name、url、priority、sync_interval、enabled 开关
- 表格 elem ID: `#sources-table`

### Tab 2：全局白名单

- 顶部：标题行 + "添加条目"按钮
- 说明卡片：解释白名单作为同步基线的作用
- 搜索栏：关键词搜索框 + 搜索/重置按钮
- 表格列：扩展名、PG版本范围(badge)、架构(badge)、创建时间、操作(删除)
- 表单弹窗(`lay-filter="whitelist-form"`)：extension_name、postgres_versions、arch
- 表格 elem ID: `#whitelist-table`

### Tab 3：同步任务

- 顶部："触发同步"按钮（弹窗选择仓库源 + 模拟运行开关）
- 表格列：仓库源、状态(completed/running/failed/cancelled/pending 带色)、总包数、已下载、失败、跳过、开始时间、操作(取消/详情)
- 表格 elem ID: `#sync-tasks-table`

### Tab 4：同步策略

- 顶部："新建策略"按钮
- 表格列：策略名称、仓库源、调度(Cron)、启用、保留版本、操作(启用/禁用/编辑/删除)
- 策略表单(`lay-filter="policy-form"`)：名称、仓库源下拉、Cron调度、过滤条件(架构/OS/发布者/PG版本/通道/构建/扩展白名单黑名单)、保留版本、启用开关
- 表格 elem ID: `#sync-policies-table`

## 3. 跨 Tab 交互

### 交互 1：仓库源 → 同步任务

仓库源 Tab 表格行的"同步"按钮触发 `POST /api/v1/sync/trigger` 成功后：
1. 自动切换到「同步任务」Tab：`element.tabChange('sync-tab', 'tab-tasks')`
2. 刷新任务列表：`table.reload('sync-tasks-table')`
3. 用户立即看到新创建的任务状态

### 交互 2：同步策略 → 仓库源下拉

同步策略 Tab 的策略表单中"仓库源"下拉框直接从 `GET /api/v1/sources` 获取。现有 sync.html 已实现此逻辑，合并后保持不变。

### 交互 3：Tab 懒加载

Layui Tab 隐藏的 `layui-tab-item` 内容不渲染。首次点击某 Tab 时，通过 `element.on('tab(sync-tab)')` 事件回调触发对应表格的 `table.reload`，避免页面初始化时同时加载 4 个表格数据。

实现方式：
```javascript
var tabLoaded = { sources: false, whitelist: false, tasks: false, policies: false };
element.on('tab(sync-tab)', function(data) {
  var idx = data.index;
  if (idx === 0 && !tabLoaded.sources) { table.reload('sources-table'); tabLoaded.sources = true; }
  if (idx === 1 && !tabLoaded.whitelist) { table.reload('whitelist-table'); tabLoaded.whitelist = true; }
  // ... Tab 3, 4 同理
});
```

### 不新增的交互

- 白名单 Tab 保持独立，不与同步任务产生直接前端交互（白名单通过后端 `SyncEngine._get_filters` 自动影响同步，无需前端联动）
- 不添加跨 Tab 拖拽等复杂交互

## 4. 文件变更清单

### 修改的文件

| 文件 | 变更内容 |
|---|---|
| `app/templates/sync.html` | 从 2 Tab 扩展为 4 Tab，吸收 sources.html 和 whitelist.html 全部 HTML 和 JS |
| `app/templates/base.html` | 侧边栏移除"同步任务"、"仓库源"、"全局白名单"3 个菜单项，新增"同步中心"1 个菜单项(active_nav: "sync", 路由: /sync, 图标: layui-icon-refresh-3) |
| `app/main.py` | `/sources` 和 `/whitelist` 页面路由改为 302 重定向到 `/sync`；sync_page 函数的 active_nav 和 title 更新 |

### 删除的文件

| 文件 | 原因 |
|---|---|
| `app/templates/sources.html` | 内容已迁移到 sync.html Tab 1 |
| `app/templates/whitelist.html` | 内容已迁移到 sync.html Tab 2 |

### 不变的文件

- 所有 API 路由（`app/api/sources.py`、`app/api/whitelist.py`、`app/api/sync.py`）
- 所有数据模型（`app/models/source.py`、`app/models/whitelist.py`、`app/models/sync.py`）
- 所有服务层（`app/services/sync_engine.py`、`app/services/scheduler.py`、`app/services/health_checker.py`）
- 无新增文件

## 5. 关键注意事项

### ID 和 lay-filter 唯一性

合并后所有 elem ID 和 lay-filter 必须唯一：

| Tab | 表格 elem ID | 表单 lay-filter |
|---|---|---|
| 仓库源 | `sources-table` | `source-form` |
| 全局白名单 | `whitelist-table` | `whitelist-form` |
| 同步任务 | `sync-tasks-table` | `sync-trigger-form` |
| 同步策略 | `sync-policies-table` | `policy-form` |

### Layui 模块加载

合并后需要加载的 Layui 模块：
```javascript
layui.use(['table', 'layer', 'element', 'form', 'laydate', 'laycron'], function() { ... });
```

### 路由重定向兼容

`/sources` 和 `/whitelist` 改为重定向，确保旧书签和链接正常跳转：
```python
@app.get("/sources", response_class=HTMLResponse)
async def sources_page(request: Request) -> HTMLResponse:
    return RedirectResponse(url="/sync", status_code=302)
```

## 6. 测试要点

1. **Tab 切换**：4 个 Tab 切换正常，各表格数据独立懒加载
2. **跨 Tab 交互**：仓库源 Tab "同步"操作后自动跳转到同步任务 Tab 并刷新列表
3. **旧链接兼容**：`/sources`、`/whitelist` 正确重定向到 `/sync`
4. **表单唯一性**：各表单弹窗的 `lay-filter` 不冲突，提交正常
5. **复杂交互**：白名单搜索/分页、同步策略 Cron 表单、仓库源编辑表单等正常
6. **侧边栏高亮**：同步中心菜单项高亮正确（`active_nav: "sync"`）
7. **数据一致性**：各 Tab 的增删改操作后表格正确刷新
