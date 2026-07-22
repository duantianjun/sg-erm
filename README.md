# SG-ERM - StackGres Extension Repository Manager

> StackGres 扩展仓库全功能管理平台：Web 管理、精细化同步、混合缓存代理、自定义扩展发布、安全认证与监控。

## 特性

- **Web 管理界面** — layui 2.x + Jinja2，零构建链，8 个完整页面
- **精细化同步** — 按 arch / os / publisher / 扩展名 / PG 版本过滤，支持定时 Cron 调度
- **混合缓存代理** — 预同步白名单 + 代理兜底，兼容 StackGres SGCluster 原生接口
- **自定义扩展发布** — 上传 .tgz、RSA-2048 签名、自动打包、更新 index.json
- **多仓库源** — 聚合官方 / 第三方 / 自建仓库，优先级调度
- **安全认证** — JWT 登录、API Token（sgerm_ 前缀）、RBAC、审计日志
- **缓存淘汰** — 磁盘阈值 LRU + TTL + 版本保留三重策略
- **监控** — Prometheus `/metrics` 端点（12 个指标）
- **单容器部署** — FastAPI 一体化托管（静态文件 + API + 代理），Docker 多阶段构建

## 技术栈

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

## 快速开始

### 本地运行

```bash
# 克隆项目
cd sg-erm

# 安装依赖
pip install -r requirements.txt

# 设置数据目录
export SG_ERM_DATA_DIR=./data

# 数据库迁移
alembic upgrade head

# 启动
python -m uvicorn app.main:app --port 18070
```

访问 http://localhost:18070，默认账号 `admin / admin`。

### Docker Compose

```bash
docker-compose up -d
```

### Kubernetes

```bash
# 构建并推送镜像
docker build -t your-registry/sg-erm:latest .
docker push your-registry/sg-erm:latest

# 修改 k8s/sg-erm.yaml 中的镜像地址后部署
kubectl apply -f k8s/sg-erm.yaml
```

详细部署说明见 [DEPLOY.md](DEPLOY.md)。

## 项目结构

```
sg-erm/
├── app/
│   ├── api/                # REST API 路由
│   │   ├── auth.py         # 登录 / 用户管理
│   │   ├── audit.py        # 审计日志查询
│   │   ├── dashboard.py    # 仪表盘统计 + 缓存淘汰
│   │   ├── extensions.py   # 扩展目录 CRUD
│   │   ├── publish.py      # 自定义扩展发布
│   │   ├── sources.py      # 仓库源 CRUD
│   │   ├── sync.py         # 同步任务 + 策略 CRUD
│   │   ├── tokens.py       # API Token 管理
│   │   ├── whitelist.py    # 全局白名单 CRUD
│   │   └── response.py     # layui 兼容响应格式
│   ├── middleware/
│   │   └── audit.py        # 审计日志中间件
│   ├── models/             # SQLAlchemy ORM 模型 (11 张表)
│   ├── services/           # 核心业务逻辑
│   │   ├── sync_engine.py      # 异步同步引擎 (aiohttp)
│   │   ├── proxy_engine.py     # 混合代理 (HIT/MISS/404)
│   │   ├── publish_service.py  # 扩展发布 (签名/打包)
│   │   ├── crypto_service.py   # RSA 密钥管理
│   │   ├── auth_service.py     # JWT / API Token 认证
│   │   ├── cache_eviction.py   # 缓存淘汰 (LRU/TTL/版本)
│   │   ├── scheduler.py        # APScheduler 定时同步
│   │   ├── metrics.py          # Prometheus 指标
│   │   └── naming.py           # StackGres 包名/URL 生成
│   ├── static/layui/       # 前端 UI 资产
│   ├── templates/          # Jinja2 页面模板 (9 个)
│   ├── config.py           # pydantic-settings 配置
│   ├── database.py         # SQLAlchemy 异步引擎
│   └── main.py             # FastAPI 应用入口
├── alembic/                # 数据库迁移
├── k8s/                    # Kubernetes 部署清单
├── Dockerfile              # 多阶段构建
├── docker-compose.yml
├── entrypoint.sh           # 容器入口 (迁移 + 启动)
├── requirements.txt
└── DEPLOY.md               # 部署文档
```

## 核心概念

### 代理模式

SG-ERM 默认运行在 **混合模式 (hybrid)**，对 StackGres 集群透明：

```
StackGres 集群请求 .tar 文件
    │
    ├─ 本地存在 → HIT → 直接返回
    │
    └─ 本地不存在 → 从上游拉取 → 缓存到本地 → 返回 (MISS)
```

三种模式：
- **hybrid**（默认）— 预同步白名单 + 代理兜底
- **strict** — 仅返回本地已缓存的包，未命中返回 404
- **proxy_only** — 不预同步，所有请求按需从上游代理

### StackGres 兼容接口

| 路由 | 说明 |
|------|------|
| `/v2/index.json` | 扩展仓库索引 |
| `/{publisher}/{arch}/{os}/{package}.tar` | 扩展包下载 |

响应头 `X-Cache-Status: HIT|MISS` 标识缓存命中情况。

### 配置 StackGres 集群

```yaml
apiVersion: stackgres.io/v1
kind: SGCluster
spec:
  configurations:
    shielding:
      extensionsRepository: "http://sg-erm.stackgres.svc:18070"
```

## 环境变量

所有配置通过 `SG_ERM_` 前缀的环境变量控制：

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `SG_ERM_DATA_DIR` | `/data` | 数据根目录（PVC 挂载点） |
| `SG_ERM_LISTEN_PORT` | `18070` | 监听端口 |
| `SG_ERM_PROXY_MODE` | `hybrid` | 代理模式: hybrid / strict / proxy_only |
| `SG_ERM_UPSTREAM_REPO_URL` | `https://extensions.stackgres.io/postgres/repository` | 上游仓库 |
| `SG_ERM_SYNC_CONCURRENCY` | `8` | 并发下载数 |
| `SG_ERM_SECRET_KEY` | `change-me-in-production` | JWT 签名密钥（生产环境必须修改） |
| `SG_ERM_CACHE_MAX_DISK_USAGE` | `80` | 磁盘使用率阈值 (%) |
| `SG_ERM_CACHE_TTL_DAYS` | `7` | 缓存 TTL (天) |
| `SG_ERM_CACHE_KEEP_VERSIONS` | `3` | 每个扩展保留版本数 |

## API 概览

| 方法 | 路径 | 说明 | 认证 |
|------|------|------|------|
| POST | `/api/v1/auth/login` | 登录 | - |
| GET | `/api/v1/auth/me` | 当前用户 | JWT |
| GET | `/api/v1/auth/users` | 用户列表 | Admin |
| GET/POST | `/api/v1/tokens` | API Token | Admin |
| GET | `/api/v1/dashboard/stats` | 仪表盘统计 | - |
| POST | `/api/v1/dashboard/cache/evict` | 缓存淘汰 | Admin |
| GET | `/api/v1/extensions` | 扩展列表 | - |
| GET | `/api/v1/extensions/{name}` | 扩展详情 | - |
| GET/POST | `/api/v1/sources` | 仓库源 | Admin |
| GET/POST | `/api/v1/sync/policies` | 同步策略 | Admin |
| POST | `/api/v1/sync/trigger` | 触发同步 | Admin |
| GET | `/api/v1/sync/tasks` | 同步任务 | - |
| GET/POST | `/api/v1/whitelist` | 全局白名单 | Admin |
| GET/POST | `/api/v1/publish/publishers` | 发布者 | Admin |
| POST | `/api/v1/publish/upload` | 上传扩展 | Admin |
| GET | `/api/v1/audit/logs` | 审计日志 | Admin |
| GET | `/metrics` | Prometheus 指标 | - |
| GET | `/health` | 健康检查 | - |

OpenAPI 文档：`/docs`

## 许可证

内部使用