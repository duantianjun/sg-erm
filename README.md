# SG-ERM - StackGres Extension Repository Manager

> StackGres 扩展仓库全功能管理平台：Web 管理界面、精细化同步引擎、混合缓存代理、自定义扩展发布、安全认证与审计。

---

## ✨ 特性

### 🎛️ Web 管理界面
- **5 大功能模块**：仪表盘、扩展管理、同步中心、审计日志、系统设置
- **Tab 融合设计**：扩展管理（3 Tab）+ 同步中心（4 Tab），减少页面跳转
- **零构建前端**：layui 2.x + Jinja2，开箱即用
- **响应式布局**：左侧导航 + 右侧内容区，适配桌面端

### 🔄 同步引擎
- **多源聚合**：支持多个上游仓库源，按优先级聚合 index.json
- **精细过滤**：按架构 / 操作系统 / 发布者 / PG 版本 / 通道 / 扩展名过滤
- **Cron 调度**：定时同步策略，支持灵活的 Cron 表达式
- **异步下载**：aiohttp 异步并发，支持断点续传（Range 206）
- **健康检查**：自动检测上游仓库可用性（healthy / degraded / down）
- **模拟运行**：dry-run 模式预览同步结果，不实际下载

### 📦 混合缓存代理
- **三种模式**：
  - `hybrid`（默认）— 预同步白名单 + 代理兜底
  - `strict` — 仅返回本地已缓存的包，未命中返回 404
  - `proxy_only` — 不预同步，所有请求按需从上游代理
- **StackGres 兼容**：完全兼容 SGCluster 原生扩展仓库接口
- **缓存标识**：响应头 `X-Cache-Status: HIT|MISS` 标识缓存命中状态

### 🚀 自定义扩展发布
- **发布者管理**：多个发布者独立管理
- **一键发布**：上传 .tgz 源码包，自动构建并签名
- **RSA-2048 签名**：扩展包签名验证，AES-256-GCM 加密私钥
- **自动索引**：发布后自动更新本地 index.json

### 🔒 安全认证
- **JWT 登录**：用户名密码认证，token_version 支持一键吊销
- **API Token**：`sgerm_` 前缀，支持命名管理，8 字符前缀索引快速校验
- **RBAC 权限**：管理员 / 普通用户角色区分
- **审计日志**：全量操作审计，记录操作者、动作、资源、状态、IP
- **密码策略**：≥8 位，需包含大小写字母和数字/特殊字符
- **路径安全**：文件路径规范化校验，防止路径遍历
- **白名单机制**：同步任务必须经过白名单过滤，空白名单拒绝所有同步

### 📊 监控与运维
- **仪表盘**：扩展数、构建数、缓存大小、同步状态一览
- **缓存淘汰**：磁盘阈值 LRU + TTL + 版本保留三重策略
- **Prometheus 指标**：`/metrics` 端点，12 项核心指标
- **健康检查**：`/health` 端点，K8s liveness/readiness 探针
- **结构化日志**：应用日志 + 任务日志分离，自动轮转

---

## 🛠️ 技术栈

| 模块 | 技术 |
|------|------|
| 后端框架 | FastAPI (Python 3.11+) |
| 数据库 | SQLite (WAL 模式) + SQLAlchemy 2.0 异步 + Alembic |
| 模板引擎 | Jinja2 |
| 前端 UI | layui 2.x |
| 异步下载 | aiohttp |
| 定时调度 | APScheduler |
| 认证 | JWT (python-jose) + pbkdf2_sha256 |
| 扩展签名 | RSA-2048 + AES-256-GCM (cryptography) |
| 监控 | prometheus-client |

---

## 🚀 快速开始

### 前置要求

- Python 3.11+
- pip
- 生产环境必须设置 `SG_ERM_SECRET_KEY`（≥32 字节）

### 本地运行

```bash
# 克隆项目
git clone https://github.com/your-org/sg-erm.git
cd sg-erm

# 安装依赖
pip install -r requirements.txt

# 设置环境变量（生产环境请使用强密钥）
export SG_ERM_SECRET_KEY=your-32-byte-secret-key-here
export SG_ERM_DATA_DIR=./data

# 数据库迁移
alembic upgrade head

# 启动服务
python -m uvicorn app.main:app --port 18070 --reload
```

访问 http://localhost:18070，默认账号 `admin / admin`（请立即修改默认密码）。

### Docker Compose

```bash
# 克隆项目
git clone https://github.com/your-org/sg-erm.git
cd sg-erm

# 启动（数据持久化在 ./data 目录）
docker-compose up -d
```

### Kubernetes

```bash
# 构建并推送镜像
docker build -t your-registry/sg-erm:latest .
docker push your-registry/sg-erm:latest

# 修改 k8s/sg-erm.yaml 中的镜像地址和 Secret 后部署
kubectl apply -f k8s/sg-erm.yaml
```

详细部署说明见 [DEPLOY.md](DEPLOY.md)。

---

## 📋 功能模块

### 仪表盘
- 扩展总数、构建总数、缓存大小统计
- 同步任务状态概览
- 磁盘使用率监控
- 缓存淘汰手动触发

### 扩展管理
| Tab | 功能 |
|-----|------|
| **扩展目录** | 浏览所有扩展元数据（名称、描述、版本、许可证），支持搜索、筛选、详情查看 |
| **仓库文件** | 浏览本地已缓存的 .tar 包，支持 SHA256 验证、重下载、批量删除 |
| **自定义发布** | 管理发布者、上传 .tgz 源码包、自动构建签名发布 |

### 同步中心
| Tab | 功能 |
|-----|------|
| **仓库源** | 上游仓库源的增删改查、健康状态、手动同步触发 |
| **全局白名单** | 同步基线扩展配置，按扩展名 / PG 版本 / 架构过滤 |
| **同步任务** | 同步任务列表、进度查看、任务取消、详情查看 |
| **同步策略** | 定时同步策略 CRUD，Cron 调度、过滤条件、版本保留 |

### 审计日志
- 全量操作审计记录
- 按动作、结果、时间范围筛选
- 统计概览（总记录、成功数、失败数、近 24 小时）

### 系统设置
- 修改密码
- 用户管理（管理员）

---

## 🔐 安全说明

### 生产环境必须配置

```bash
# JWT 签名密钥（必须 ≥ 32 字节，强烈建议 64 字节）
export SG_ERM_SECRET_KEY=your-very-long-secret-key-at-least-32-bytes

# 数据目录（生产环境使用持久化存储）
export SG_ERM_DATA_DIR=/data
```

### 安全特性

| 特性 | 说明 |
|------|------|
| JWT token_version | 修改密码后自动吊销所有旧 Token |
| API Token 前缀索引 | 8 字符明文前缀 + 哈希存储，兼顾安全与性能 |
| 密码复杂度 | ≥8 位，必须包含大小写字母和数字/特殊字符 |
| 路径遍历防护 | 所有文件路径使用 `os.path.normpath()` 校验 |
| 白名单强制 | 同步任务必须经过白名单，空白名单拒绝所有同步 |
| 审计日志 | 所有操作可追溯，含操作者 IP |

---

## ⚙️ 环境变量

所有配置通过 `SG_ERM_` 前缀的环境变量控制：

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `SG_ERM_DATA_DIR` | `/data` | 数据根目录（PVC 挂载点） |
| `SG_ERM_LISTEN_HOST` | `0.0.0.0` | 监听地址 |
| `SG_ERM_LISTEN_PORT` | `18070` | 监听端口 |
| `SG_ERM_SECRET_KEY` | **必须设置** | JWT 签名密钥（≥32 字节） |
| `SG_ERM_PROXY_MODE` | `hybrid` | 代理模式: hybrid / strict / proxy_only |
| `SG_ERM_UPSTREAM_REPO_URL` | `https://extensions.stackgres.io/postgres/repository` | 上游仓库 |
| `SG_ERM_SYNC_CONCURRENCY` | `8` | 并发下载数 |
| `SG_ERM_SYNC_DOWNLOAD_TIMEOUT` | `120` | 下载超时（秒） |
| `SG_ERM_SYNC_MAX_RETRIES` | `3` | 下载重试次数 |
| `SG_ERM_CACHE_MAX_DISK_USAGE` | `80` | 磁盘使用率阈值（%） |
| `SG_ERM_CACHE_TARGET_DISK_USAGE` | `70` | 淘汰后目标使用率（%） |
| `SG_ERM_CACHE_TTL_DAYS` | `7` | 缓存 TTL（天） |
| `SG_ERM_CACHE_KEEP_VERSIONS` | `3` | 每个扩展保留版本数 |
| `SG_ERM_JWT_ALGORITHM` | `HS256` | JWT 算法 |
| `SG_ERM_ACCESS_TOKEN_EXPIRE_MINUTES` | `1440` | Token 过期时间（分钟） |
| `SG_ERM_SCHEDULER_ENABLED` | `True` | 启用定时调度器 |
| `SG_ERM_DB_FILENAME` | `sg-erm.db` | SQLite 文件名 |
| `SG_ERM_REPO_DIRNAME` | `repo` | 仓库子目录名 |

---

## 📡 API 文档

### OpenAPI / Swagger

- Swagger UI: `http://localhost:18070/api/docs`
- ReDoc: `http://localhost:18070/api/redoc`
- OpenAPI JSON: `http://localhost:18070/api/openapi.json`

### 主要 API 分类

| 模块 | 前缀 | 说明 |
|------|------|------|
| 认证 | `/api/v1/auth` | 登录、登出、当前用户、密码修改 |
| API Token | `/api/v1/tokens` | API Token 管理 |
| 仪表盘 | `/api/v1/dashboard` | 统计数据、缓存淘汰 |
| 扩展 | `/api/v1/extensions` | 扩展列表、详情、批量删除 |
| 仓库文件 | `/api/v1/repo-files` | 文件列表、验证、重下载、删除 |
| 仓库源 | `/api/v1/sources` | 仓库源 CRUD、健康检查 |
| 同步 | `/api/v1/sync` | 任务列表、触发、取消、策略 CRUD |
| 白名单 | `/api/v1/whitelist` | 白名单条目 CRUD |
| 发布 | `/api/v1/publish` | 发布者管理、扩展上传发布 |
| 审计 | `/api/v1/audit` | 审计日志查询、统计 |

### StackGres 兼容接口

| 路由 | 说明 |
|------|------|
| `GET /v2/index.json` | 扩展仓库索引 |
| `GET /{publisher}/{arch}/{os}/{package}.tar` | 扩展包下载 |

### 监控端点

| 路由 | 说明 |
|------|------|
| `GET /health` | 健康检查 |
| `GET /metrics` | Prometheus 指标 |

---

## 📁 项目结构

```
sg-erm/
├── app/
│   ├── api/                    # REST API 路由
│   │   ├── auth.py             # 认证
│   │   ├── audit.py            # 审计日志
│   │   ├── dashboard.py        # 仪表盘
│   │   ├── extensions.py       # 扩展目录
│   │   ├── publish.py          # 自定义发布
│   │   ├── repo_files.py       # 仓库文件
│   │   ├── sources.py          # 仓库源
│   │   ├── sync.py             # 同步任务/策略
│   │   ├── tokens.py           # API Token
│   │   ├── whitelist.py        # 全局白名单
│   │   └── response.py         # 统一响应格式
│   ├── middleware/
│   │   └── audit.py            # 审计中间件
│   ├── models/                 # SQLAlchemy ORM 模型 (11 张表)
│   ├── services/               # 核心业务逻辑
│   │   ├── sync_engine.py      # 异步同步引擎
│   │   ├── proxy_engine.py     # 混合代理引擎
│   │   ├── publish_service.py  # 扩展发布服务
│   │   ├── crypto_service.py   # 加密/签名服务
│   │   ├── auth_service.py     # 认证服务
│   │   ├── cache_eviction.py   # 缓存淘汰
│   │   ├── scheduler.py        # APScheduler 调度
│   │   ├── health_checker.py   # 健康检查
│   │   ├── index_aggregator.py # 多源索引聚合
│   │   ├── metrics.py          # Prometheus 指标
│   │   └── naming.py           # 包名/URL 生成
│   ├── static/layui/           # 前端 UI 资产
│   ├── templates/              # Jinja2 页面模板 (8 个)
│   ├── config.py               # 配置管理
│   ├── database.py             # 数据库引擎
│   ├── logging_config.py       # 日志配置
│   └── main.py                 # FastAPI 应用入口
├── alembic/                    # 数据库迁移
├── docs/                       # 功能文档
│   └── superpowers/specs/      # 设计规格文档
├── k8s/                        # Kubernetes 部署清单
├── Dockerfile                  # 多阶段构建
├── docker-compose.yml
├── entrypoint.sh               # 容器入口脚本
├── requirements.txt
├── DEPLOY.md                   # 部署文档
└── README.md
```

---

## 🤝 配置 StackGres 集群

将 StackGres 集群的扩展仓库地址指向 SG-ERM 服务：

```yaml
apiVersion: stackgres.io/v1
kind: SGCluster
spec:
  configurations:
    shielding:
      extensionsRepository: "http://sg-erm.stackgres.svc:18070"
```

---

## 📝 日志说明

| 日志文件 | 路径 | 轮转策略 | 说明 |
|----------|------|----------|------|
| 应用日志 | `{data_dir}/logs/sg-erm.log` | 10MB × 5 | 业务模块运行日志 |
| 任务日志 | `{data_dir}/logs/sg-erm-task.log` | 10MB × 10 | 同步/调度专项日志 |

控制台仅输出 INFO 级别日志，文件日志包含 DEBUG 级别。

---

## 📄 许可证

内部使用
