# -*- coding: utf-8 -*-
"""审计日志中间件。

自动记录所有 HTTP 请求的关键信息到 audit_log 表。
可配置跳过某些路径（如静态文件、健康检查）。
"""
import logging
import time
from datetime import datetime

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

from app.models.audit import AuditLog
from app.services.auth_service import get_current_principal

logger = logging.getLogger(__name__)

# 跳过审计的路径前缀
SKIP_PATHS = {
    "/static",
    "/health",
    "/docs",
    "/openapi.json",
    "/api/v1/auth/login",
    "/favicon.ico",
}

# 敏感操作路径（记录更详细）
SENSITIVE_PATHS = [
    "/api/v1/auth",
    "/api/v1/publish",
    "/api/v1/sync/trigger",
    "/api/v1/sources",
    "/api/v1/whitelist",
    "/api/v1/tokens",
]


class AuditMiddleware(BaseHTTPMiddleware):
    """审计日志中间件。"""

    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        # 跳过不需要审计的路径
        if any(path.startswith(p) for p in SKIP_PATHS):
            return await call_next(request)

        start_time = time.time()

        # 获取认证主体（异步，但中间件不支持 await Depends）
        # 这里我们只记录请求基本信息，认证信息从 request.state 获取
        actor = request.headers.get("X-Real-IP", "anonymous")
        client_ip = self._get_client_ip(request)

        # 执行请求
        response = await call_next(request)

        duration_ms = int((time.time() - start_time) * 1000)

        # 敏感操作记录到应用日志
        is_sensitive = any(path.startswith(p) for p in SENSITIVE_PATHS)
        if is_sensitive or response.status_code >= 400:
            log_level = logging.WARNING if response.status_code >= 400 else logging.INFO
            logger.log(
                log_level,
                f"[审计] {request.method} {path} status={response.status_code} "
                f"duration={duration_ms}ms ip={client_ip} actor={actor}"
            )

        # 记录审计日志（如果配置了数据库）
        try:
            await self._log_request(
                request=request,
                response=response,
                duration_ms=duration_ms,
                actor=actor,
                client_ip=client_ip,
            )
        except Exception as e:
            # 审计日志失败不应影响主请求
            logger.debug(f"[审计] 写入审计日志失败（非致命）: {e}")

        return response

    def _get_client_ip(self, request: Request) -> str:
        """获取客户端真实 IP。"""
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            return forwarded.split(",")[0].strip()
        real_ip = request.headers.get("X-Real-IP")
        if real_ip:
            return real_ip
        if request.client:
            return request.client.host
        return "unknown"

    async def _log_request(
        self,
        request: Request,
        response,
        duration_ms: int,
        actor: str,
        client_ip: str,
    ):
        """将请求记录到审计日志。"""
        from app.database import async_session_maker

        path = request.url.path
        method = request.method
        status_code = response.status_code

        # 构造 action
        action = f"{method.lower()}.{self._path_to_action(path)}"

        # 判断结果
        result = "success" if status_code < 400 else "failure"

        # 构造 detail
        detail = {
            "path": path,
            "method": method,
            "status_code": status_code,
            "duration_ms": duration_ms,
        }

        # 对于敏感操作，记录更多信息
        if any(path.startswith(p) for p in SENSITIVE_PATHS):
            detail["sensitive"] = True

        async with async_session_maker() as session:
            log = AuditLog(
                actor=actor,
                action=action,
                resource=path,
                detail=detail,
                result=result,
                client_ip=client_ip,
            )
            session.add(log)
            await session.commit()

    def _path_to_action(self, path: str) -> str:
        """将路径转换为动作名称。"""
        # 简化路径：/api/v1/extensions → extensions
        # /api/v1/extensions/postgis → extensions.detail
        parts = path.strip("/").split("/")

        if len(parts) >= 3 and parts[0] == "api" and parts[1] == "v1":
            resource = parts[2] if len(parts) > 2 else "unknown"
            if len(parts) > 3 and parts[3]:
                return f"{resource}.detail"
            return resource

        return path.strip("/").replace("/", ".")
