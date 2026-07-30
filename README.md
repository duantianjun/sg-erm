# SG-ERM

> StackGres Extension Repository Manager — StackGres 扩展仓库全功能管理平台

---

## 简介

SG-ERM 是一个为 StackGres 集群提供扩展仓库管理的一体化服务。它将 Web 管理界面、精细化同步引擎、混合缓存代理、自定义扩展发布、安全认证与审计集成在单个容器中，完全兼容 StackGres 原生扩展仓库接口。

### 核心特性

| 模块 | 能力 |
|------|------|
| Web 管理界面 | 5 大功能模块，layui 2.x + Jinja2 零构建前端 |
| 同步引擎 | 多源聚合、精细过滤、Cron 调度、异步断点续传、健康检查 |
| 混合缓存代理 | hybrid / strict / proxy_only 三种模式，兼容 SGCluster 原生接口 |
| 自定义发布 | 发布者管理、.tgz 上传、RSA-2048 签名、AES-256-GCM 加密 |
| 安全认证 | JWT + API Token + RBAC + 审计日志 + 白名单机制 |
| 监控运维 | Prometheus 指标、缓存淘汰 LRU+TTL、结构化日志轮转 |

---

## 技术栈

| 模块 | 技术 |
|------|------|
| 后端框架 | FastAPI (Python 3.12+) |
| 数据库 | SQLite (WAL) + SQLAlchemy 2.0 异步 + Alembic |
| 模板引擎 | Jinja2 |
| 前端 UI | layui 2.x |
| 异步下载 | aiohttp |
| 定时调度 | APScheduler |
| 认证 | JWT (python-jose) + passlib[bcrypt] |
| 扩展签名 | RSA-2048 + AES-256-GCM (cryptography) |
| 监控 | prometheus-client |
| 配置管理 | pydantic-settings（60+ 可配置参数） |

---

## 快速开始

### 本地运行

```bash
git clone https://github.com/duantianjun/sg-erm.git
cd sg-erm

# 安装依赖
pip install -r requirements.txt

# 生成密钥并写入 .env
cp .env.example .env
# 编辑 .env，填入 SG_ERM_SECRET_KEY（生成命令如下）
python -c "import secrets; print(secrets.token_hex(32))"

# 数据库迁移
alembic upgrade head

# 启动服务
python -m uvicorn app.main:app --port 18070 --reload
```

访问 http://localhost:18070，默认账号 `admin / admin`（请立即修改）。

### Docker Compose

```bash
git clone https://github.com/duantianjun/sg-erm.git
cd sg-erm

# 配置环境变量
cp .env.example .env
# 编辑 .env，必须设置 SG_ERM_SECRET_KEY

# 启动
docker compose up -d
```

### Kubernetes

```bash
# 1. 构建并推送镜像
docker build -t your-registry/sg-erm:latest .
docker push your-registry/sg-erm:latest

# 2. 修改配置（必须修改）
#    - k8s/config.yaml: SG_ERM_SECRET_KEY
#    - k8s/deployment.yaml: image 镜像地址

# 3. 部署
kubectl apply -f k8s/

# 4. 查看状态
kubectl -n stackgres get pods
kubectl -n stackgres get svc sg-erm
```

---

## 配置说明

所有配置通过 `SG_ERM_` 前缀的环境变量控制，支持 `.env` 文件。完整参数列表见 [.env.example](.env.example)。

### 必须配置

| 变量 | 说明 | 示例 |
|------|------|------|
| `SG_ERM_SECRET_KEY` | JWT 签名密钥（≥32 字符） | `python -c "import secrets; print(secrets.token_hex(32))"` |

### 常用配置

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `SG_ERM_DATA_DIR` | `/data` | 数据根目录（数据库 + 仓库 + 日志） |
| `SG_ERM_LISTEN_PORT` | `18070` | 监听端口 |
| `SG_ERM_PROXY_MODE` | `hybrid` | 代理模式：hybrid / strict / proxy_only |
| `SG_ERM_UPSTREAM_REPO_URL` | `https://extensions.stackgres.io/...` | 上游仓库地址 |
| `SG_ERM_SYNC_CONCURRENCY` | `8` | 并发下载数 |
| `SG_ERM_DEFAULT_ADMIN_USERNAME` | `admin` | 默认管理员用户名 |
| `SG_ERM_DEFAULT_ADMIN_PASSWORD` | `admin` | 默认管理员密码 |

### 完整参数清单

共 17 个分组、60+ 可配置参数，涵盖路径、网络、数据库、同步、缓存、安全、加密、日志、健康检查等。详见 [.env.example](.env.example) 中的分类注释。

---

## API 文档

| 端点 | 说明 |
|------|------|
| `/api/docs` | Swagger UI |
| `/api/redoc` | ReDoc |
| `/api/openapi.json` | OpenAPI Schema |
| `/health` | 健康检查 |
| `/metrics` | Prometheus 指标 |

### REST API

| 模块 | 前缀 |
|------|------|
| 认证 | `/api/v1/auth` |
| API Token | `/api/v1/tokens` |
| 仪表盘 | `/api/v1/dashboard` |
| 扩展目录 | `/api/v1/extensions` |
| 仓库文件 | `/api/v1/repo-files` |
| 仓库源 | `/api/v1/sources` |
| 同步 | `/api/v1/sync` |
| 白名单 | `/api/v1/whitelist` |
| 发布 | `/api/v1/publish` |
| 审计 | `/api/v1/audit` |

### StackGres 兼容接口

| 路由 | 说明 |
|------|------|
| `GET /v2/index.json` | 扩展仓库索引 |
| `GET /{publisher}/{arch}/{os}/{package}.tar` | 扩展包下载 |

---

## 功能模块

### 仪表盘
扩展总数、构建总数、缓存大小、磁盘使用率、同步状态一览，支持手动触发缓存淘汰。

### 扩展管理（3 Tab）
- **扩展目录**：浏览所有扩展元数据，支持搜索、筛选、详情查看
- **仓库文件**：浏览本地已缓存的 .tar 包，SHA256 验证、重下载、批量删除
- **自定义发布**：发布者管理、.tgz 上传、自动构建签名发布

### 同步中心（4 Tab）
- **仓库源**：上游仓库源 CRUD、健康状态、手动同步
- **全局白名单**：同步基线配置，按扩展名 / PG 版本 / 架构过滤
- **同步任务**：任务列表、进度查看、任务取消
- **同步策略**：Cron 调度 CRUD、过滤条件、版本保留

### 审计日志
全量操作审计，按动作、结果、时间范围筛选，含统计概览。

### 系统设置
修改密码、用户管理。

---

## 项目结构

```
sg-erm/
├── app/
│   ├── api/                 # REST API 路由（11 个模块）
│   ├── middleware/           # 审计中间件
│   ├── models/              # SQLAlchemy ORM（11 张表）
│   ├── services/            # 核心业务逻辑（12 个服务）
│   ├── static/              # layui 2.x 前端资产
│   ├── templates/           # Jinja2 模板（8 个页面）
│   ├── config.py            # 集中配置（60+ 参数）
│   ├── database.py          # 异步数据库引擎
│   ├── logging_config.py    # 日志配置
│   └── main.py              # FastAPI 入口
├── alembic/                 # 数据库迁移
├── k8s/                     # K8s 部署清单（8 个文件）
├── tests/                   # 单元测试 + 集成测试
├── Dockerfile               # 多阶段构建
├── docker-compose.yml       # 容器编排
├── entrypoint.sh            # 容器入口脚本
├── .env.example             # 环境变量示例
├── requirements.txt         # 依赖
└── alembic.ini              # 迁移配置
```

---

## 部署架构

```
                    ┌─────────────────────────────────────┐
                    │         SG-ERM 容器 (:18070)         │
                    │                                     │
  StackGres ───────►│  /v2/index.json   (兼容接口)         │
  SGCluster         │  /{p}/{a}/{o}/{pkg}.tar             │
                    │                                     │
  Web 浏览器 ──────►│  / (Web UI - layui)                 │
                    │  /api/v1/* (REST API)               │
                    │  /api/docs (Swagger)                │
                    │  /metrics (Prometheus)              │
                    │  /health (K8s 探针)                  │
                    │         │                           │
                    │    ┌────┴────┐                      │
                    │    │ SQLite  │ (WAL, /data/)       │
                    │    │ (本地)  │                      │
                    │    └─────────┘                      │
                    │         │                           │
                    │    aiohttp 异步下载                   │
                    │         ▼                           │
                    │  上游 StackGres 扩展仓库             │
                    └─────────────────────────────────────┘
```

**关键约束**：SQLite + 本地文件存储，必须单副本部署（`replicas: 1`，`strategy: Recreate`）。

---

## 配置 StackGres 集群

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

## 日志

| 日志文件 | 路径 | 轮转 | 说明 |
|----------|------|------|------|
| 应用日志 | `{data_dir}/logs/sg-erm.log` | 10MB × 5 | 业务运行日志 |
| 任务日志 | `{data_dir}/logs/sg-erm-task.log` | 10MB × 10 | 同步/调度专项日志 |

控制台输出 INFO 级别，文件包含 DEBUG 级别。日志轮转参数可通过环境变量调整。

---

## 开发

```bash
# 安装开发依赖
pip install -r requirements.txt -r requirements-dev.txt

# 运行测试
pytest

# 查看覆盖率
pytest --cov=app --cov-report=html
```

---

## 许可证

内部使用
