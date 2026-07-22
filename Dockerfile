# SG-ERM (StackGres Extension Repository Manager) Docker 镜像
# 多阶段构建：builder 安装依赖，runtime 精简运行
#
# 构建:
#   docker build -t sg-erm:latest .
#
# 运行:
#   docker run -d -p 18070:18070 \
#     -e SG_ERM_UPSTREAM_REPO_URL=https://extensions.stackgres.io/postgres/repository \
#     -e SG_ERM_PROXY_MODE=hybrid \
#     -v sg-erm-data:/data \
#     sg-erm:latest
#
# K8s 部署:
#   kubectl apply -f k8s/sg-erm.yaml

# ─── Stage 1: Builder ─────────────────────────────────
FROM python:3.11-slim AS builder

LABEL stage=builder

WORKDIR /build

# 安装构建依赖（编译某些 Python 包需要 gcc）
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libc6-dev \
    && rm -rf /var/lib/apt/lists/*

# 复制依赖文件并安装到 /install 目录
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ─── Stage 2: Runtime ─────────────────────────────────
FROM python:3.11-slim

LABEL maintainer="SG-ERM"
LABEL description="StackGres Extension Repository Manager - FastAPI 一体化容器"

# 安装运行时系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    tzdata \
    && rm -rf /var/lib/apt/lists/* \
    && ln -snf /usr/share/zoneinfo/Asia/Shanghai /etc/localtime \
    && echo "Asia/Shanghai" > /etc/timezone

# 从 builder 复制已安装的 Python 依赖
COPY --from=builder /install /usr/local

# 创建非 root 用户
RUN groupadd -r sg-erm && useradd -r -g sg-erm -s /sbin/nologin sg-erm

# 创建数据目录（PVC 挂载点）
RUN mkdir -p /data/repo && chown -R sg-erm:sg-erm /data

# 设置工作目录
WORKDIR /app

# 复制应用代码
COPY --chown=sg-erm:sg-erm app/ ./app/
COPY --chown=sg-erm:sg-erm alembic/ ./alembic/
COPY --chown=sg-erm:sg-erm alembic.ini ./
COPY --chown=sg-erm:sg-erm requirements.txt ./
COPY --chown=sg-erm:sg-erm entrypoint.sh ./
RUN chmod +x ./entrypoint.sh

# 切换到非 root 用户
USER sg-erm

# 环境变量默认值
ENV SG_ERM_DATA_DIR=/data \
    SG_ERM_LISTEN_HOST=0.0.0.0 \
    SG_ERM_LISTEN_PORT=18070 \
    SG_ERM_PROXY_MODE=hybrid \
    SG_ERM_SYNC_CONCURRENCY=8 \
    SG_ERM_UPSTREAM_REPO_URL=https://extensions.stackgres.io/postgres/repository \
    SG_ERM_SECRET_KEY=change-me-in-production \
    TZ=Asia/Shanghai

# 暴露端口
EXPOSE 18070

# 健康检查
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fs http://localhost:18070/health || exit 1

# 入口脚本：先执行数据库迁移，再启动 uvicorn
ENTRYPOINT ["./entrypoint.sh"]
