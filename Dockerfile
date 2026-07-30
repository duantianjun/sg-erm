# ═══════════════════════════════════════════════════════════════
# SG-ERM Dockerfile
# 多阶段构建：builder 编译依赖 → runtime 精简运行
#
# 构建:
#   docker build -t sg-erm:latest .
#
# 运行:
#   docker run -d -p 18070:18070 \
#     -e SG_ERM_SECRET_KEY=$(python -c "import secrets; print(secrets.token_hex(32))") \
#     -v sg-erm-data:/data \
#     sg-erm:latest
#
# 注意：SG_ERM_SECRET_KEY 必须通过环境变量或 .env 文件提供，
#       Dockerfile 中不设置默认值，未设置时应用启动会报错。
# ═══════════════════════════════════════════════════════════════

# ─── Stage 1: Builder ─────────────────────────────────────────
FROM python:3.12-slim AS builder

LABEL stage=builder

WORKDIR /build

# 编译依赖（cryptography 等需要 gcc）
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libc6-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ─── Stage 2: Runtime ──────────────────────────────────────────
FROM python:3.12-slim

LABEL maintainer="SG-ERM"
LABEL description="StackGres Extension Repository Manager"
LABEL version="latest"

# 运行时系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    tzdata \
    && rm -rf /var/lib/apt/lists/* \
    && ln -snf /usr/share/zoneinfo/Asia/Shanghai /etc/localtime \
    && echo "Asia/Shanghai" > /etc/timezone

# 从 builder 复制 Python 依赖
COPY --from=builder /install /usr/local

# 非 root 用户
RUN groupadd -r sg-erm && useradd -r -g sg-erm -s /sbin/nologin sg-erm

# 数据目录（PVC 挂载点）
RUN mkdir -p /data/repo /data/logs && chown -R sg-erm:sg-erm /data

WORKDIR /app

# 复制应用代码
COPY --chown=sg-erm:sg-erm app/ ./app/
COPY --chown=sg-erm:sg-erm alembic/ ./alembic/
COPY --chown=sg-erm:sg-erm alembic.ini ./
COPY --chown=sg-erm:sg-erm requirements.txt ./
COPY --chown=sg-erm:sg-erm entrypoint.sh ./
RUN chmod +x ./entrypoint.sh

USER sg-erm

# 环境变量默认值（不含 SECRET_KEY，必须由部署方提供）
ENV SG_ERM_DATA_DIR=/data \
    SG_ERM_LISTEN_HOST=0.0.0.0 \
    SG_ERM_LISTEN_PORT=18070 \
    SG_ERM_PROXY_MODE=hybrid \
    SG_ERM_SYNC_CONCURRENCY=8 \
    SG_ERM_UPSTREAM_REPO_URL=https://extensions.stackgres.io/postgres/repository \
    TZ=Asia/Shanghai

EXPOSE 18070

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -fs http://localhost:18070/health || exit 1

ENTRYPOINT ["./entrypoint.sh"]
