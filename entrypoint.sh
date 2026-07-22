#!/bin/sh
# SG-ERM 容器入口脚本
#
# 功能:
#   1. 执行 Alembic 数据库迁移（自动建表/升级）
#   2. 启动 uvicorn FastAPI 服务
#
# 环境变量:
#   SG_ERM_DATA_DIR       数据根目录（默认 /data）
#   SG_ERM_LISTEN_HOST    监听地址（默认 0.0.0.0）
#   SG_ERM_LISTEN_PORT    监听端口（默认 18070）
#   SG_ERM_SECRET_KEY     JWT 密钥（生产环境必须覆盖）

set -e

# 颜色输出
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

log_info()  { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

# ========== 主流程 ==========

log_info "========================================"
log_info "SG-ERM 容器启动"
log_info "========================================"

# 数据目录检查
DATA_DIR="${SG_ERM_DATA_DIR:-/data}"
REPO_DIR="${DATA_DIR}/repo"

log_info "数据目录: $DATA_DIR"
log_info "仓库目录: $REPO_DIR"

mkdir -p "$DATA_DIR" "$REPO_DIR"

# 1. 执行数据库迁移
log_info "执行数据库迁移..."
cd /app

if alembic upgrade head 2>&1; then
    log_info "数据库迁移完成"
else
    log_error "数据库迁移失败"
    exit 1
fi

# 2. 显示启动信息
HOST="${SG_ERM_LISTEN_HOST:-0.0.0.0}"
PORT="${SG_ERM_LISTEN_PORT:-18070}"

log_info "监听地址: $HOST:$PORT"
log_info "代理模式: ${SG_ERM_PROXY_MODE:-hybrid}"
log_info "上游仓库: ${SG_ERM_UPSTREAM_REPO_URL}"
log_info "========================================"

# 3. 启动 uvicorn
exec python -m uvicorn app.main:app \
    --host "$HOST" \
    --port "$PORT" \
    --workers 1 \
    --no-access-log
