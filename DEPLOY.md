# SG-ERM 部署指南

> StackGres Extension Repository Manager 部署文档

## 1. 部署架构

```
┌─────────────────────────────────────────────────┐
│              Docker 容器 (单镜像)                 │
│                                                 │
│  FastAPI :18070                                 │
│  ├── Web UI (Jinja2 + layui)                   │
│  ├── REST API (/api/v1/*)                       │
│  ├── StackGres 兼容代理 (/{publisher}/*.tar)    │
│  └── 静态文件 (/static/*)                        │
│                                                 │
│  数据层 (PVC 挂载 /data):                       │
│  ├── sg-erm.db (SQLite 元数据)                  │
│  └── repo/ (扩展包文件存储)                      │
└─────────────────────────────────────────────────┘
```

**与旧方案的区别**：

| 对比项 | 旧方案 (nginx + 脚本) | 新方案 (SG-ERM) |
|--------|----------------------|-----------------|
| 容器数 | 2 (nginx + CronJob) | 1 (FastAPI 一体化) |
| 同步触发 | CronJob 定时 | API/UI/定时 (灵活) |
| 管理界面 | 无 | Web UI (layui) |
| 代理模式 | 无 | 混合代理 (HIT/MISS) |
| 数据库 | 无 | SQLite (元数据) |

## 2. 本地运行

### 2.1 直接运行（开发）

```bash
cd e:\stackgres\sg-erm

# 设置数据目录
set SG_ERM_DATA_DIR=e:\stackgres\sg-erm\data

# 执行数据库迁移
python -m alembic upgrade head

# 启动服务
python -m uvicorn app.main:app --port 18070 --reload
```

访问 http://localhost:18070

### 2.2 Docker Compose 运行

```bash
cd e:\stackgres\sg-erm
docker-compose up -d
```

### 2.3 Docker 单独运行

```bash
# 构建镜像
docker build -t sg-erm:latest .

# 运行容器
docker run -d \
  --name sg-erm \
  -p 18070:18070 \
  -e SG_ERM_SECRET_KEY=your-secret-key \
  -v sg-erm-data:/data \
  sg-erm:latest
```

## 3. K8s 部署

### 3.1 部署

```bash
kubectl apply -f k8s/sg-erm.yaml
```

### 3.2 验证

```bash
# 检查 Pod 状态
kubectl get pods -n stackgres -l app=sg-erm

# 检查服务
kubectl get svc -n stackgres sg-erm

# 端口转发访问
kubectl port-forward -n stackgres svc/sg-erm 18070:18070

# 浏览器访问 http://localhost:18070
```

### 3.3 配置说明

K8s 部署通过 ConfigMap 和 Secret 管理配置：

| 配置项 | ConfigMap/Secret | 默认值 | 说明 |
|--------|-----------------|--------|------|
| `SG_ERM_PROXY_MODE` | ConfigMap | hybrid | 代理模式: hybrid/strict/proxy_only |
| `SG_ERM_SYNC_CONCURRENCY` | ConfigMap | 8 | 并发下载数 |
| `SG_ERM_UPSTREAM_REPO_URL` | ConfigMap | 官方仓库 | 上游仓库 URL |
| `SG_ERM_CACHE_MAX_DISK_USAGE` | ConfigMap | 80 | 磁盘阈值 (%) |
| `SG_ERM_CACHE_TTL_DAYS` | ConfigMap | 7 | 缓存 TTL (天) |
| `SG_ERM_SECRET_KEY` | Secret | (必须修改) | JWT 签名密钥 |

### 3.4 更新镜像

```bash
# 修改代码后重新构建
docker build -t 192.168.24.18/common/sg-erm:latest .
docker push 192.168.24.18/common/sg-erm:latest

# K8s 滚动更新
kubectl set image deployment/sg-erm \
  sg-erm=192.168.24.18/common/sg-erm:latest \
  -n stackgres
```

## 4. 首次使用

### 4.1 添加仓库源

1. 访问 Web UI → 仓库源 → 添加仓库源
2. 填写名称（如"官方仓库"）和 URL
3. 默认上游: `https://extensions.stackgres.io/postgres/repository`

或通过 API：

```bash
curl -X POST http://localhost:18070/api/v1/sources \
  -H "Content-Type: application/json" \
  -d '{"name":"官方仓库","url":"https://extensions.stackgres.io/postgres/repository"}'
```

### 4.2 配置白名单

1. 访问 Web UI → 全局白名单 → 添加条目
2. 添加常用扩展（如 postgis, pgaudit, pg_stat_statements）

### 4.3 触发同步

1. 访问 Web UI → 同步任务 → 触发同步
2. 选择仓库源，可选模拟运行
3. 查看同步进度和结果

### 4.4 配置 StackGres 集群

将 StackGres 集群的扩展仓库地址指向 SG-ERM 服务：

```yaml
# StackGres SGCluster 配置
apiVersion: stackgres.io/v1
kind: SGCluster
spec:
  configurations:
    shielding:
      extensionsRepository: "http://sg-erm.stackgres.svc:18070"
```

## 5. 环境变量参考

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `SG_ERM_DATA_DIR` | `/data` | 数据根目录 |
| `SG_ERM_LISTEN_HOST` | `0.0.0.0` | 监听地址 |
| `SG_ERM_LISTEN_PORT` | `18070` | 监听端口 |
| `SG_ERM_DB_FILENAME` | `sg-erm.db` | SQLite 文件名 |
| `SG_ERM_REPO_DIRNAME` | `repo` | 仓库子目录名 |
| `SG_ERM_PROXY_MODE` | `hybrid` | 代理模式 |
| `SG_ERM_UPSTREAM_REPO_URL` | 官方仓库 | 上游仓库 URL |
| `SG_ERM_SYNC_CONCURRENCY` | `8` | 并发下载数 |
| `SG_ERM_SYNC_DOWNLOAD_TIMEOUT` | `120` | 下载超时(秒) |
| `SG_ERM_SYNC_MAX_RETRIES` | `3` | 下载重试次数 |
| `SG_ERM_CACHE_MAX_DISK_USAGE` | `80` | 磁盘阈值(%) |
| `SG_ERM_CACHE_TARGET_DISK_USAGE` | `70` | 淘汰后目标(%) |
| `SG_ERM_CACHE_TTL_DAYS` | `7` | 缓存TTL(天) |
| `SG_ERM_CACHE_KEEP_VERSIONS` | `3` | 保留版本数 |
| `SG_ERM_SECRET_KEY` | (必须修改) | JWT密钥 |
| `SG_ERM_JWT_ALGORITHM` | `HS256` | JWT算法 |
| `SG_ERM_ACCESS_TOKEN_EXPIRE_MINUTES` | `1440` | Token过期(分钟) |
| `SG_ERM_SCHEDULER_ENABLED` | `True` | 启用定时调度 |

## 6. 监控

### 6.1 健康检查

```bash
curl http://localhost:18070/health
# {"status":"healthy","version":"0.1.0","proxy_mode":"hybrid"}
```

### 6.2 仪表盘统计

```bash
curl http://localhost:18070/api/v1/dashboard/stats
```

### 6.3 Prometheus 指标

> TODO: Phase 2 将实现 `/metrics` 端点

## 7. 故障排查

### 数据库迁移失败

```bash
# 进入容器检查
kubectl exec -it -n stackgres deployment/sg-erm -- sh

# 手动执行迁移
cd /app
alembic upgrade head

# 查看迁移历史
alembic history
```

### 同步失败

```bash
# 查看同步任务
curl http://localhost:18070/api/v1/sync/tasks

# 查看任务详情
curl http://localhost:18070/api/v1/sync/tasks/{task_id}
```

### 磁盘空间不足

1. 检查磁盘用量: 仪表盘 → 磁盘用量
2. 调整缓存策略: 环境变量 `SG_ERM_CACHE_TTL_DAYS` 和 `SG_ERM_CACHE_KEEP_VERSIONS`
3. 手动清理旧包: `find /data/repo -name "*.tar" -atime +7 -delete`
