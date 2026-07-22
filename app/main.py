"""SG-ERM FastAPI 应用入口。

启动流程：
1. lifespan 启动时初始化数据库
2. 挂载静态文件（layui 资产）
3. 注册 Jinja2 模板
4. 提供 /health 健康检查端点
5. 提供 / 首页（渲染 base.html 占位，Task 5 替换为完整仪表盘）

验证目标：
    cd e:\\stackgres\\sg-erm
    uvicorn app.main:app --port 18070
    - /health 返回 {"status":"healthy","version":"0.1.0"}
    - /static/layui/layui.js 返回 200
    - / 返回 HTML 页面
"""
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.api import audit as audit_api
from app.api import auth, dashboard, extensions, publish, repo_files, sources, sync, tokens, whitelist
from app.config import settings
from app.database import async_session_factory, close_db, init_db
from app.middleware.audit import AuditMiddleware
from app.services.proxy_engine import proxy_engine
from app.services.scheduler import start_scheduler, stop_scheduler
from app.services.health_checker import start_health_checker

# 路径常量
BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
TEMPLATE_DIR = BASE_DIR / "templates"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """应用生命周期：启动时初始化数据库，关闭时释放资源。"""
    # === 启动 ===
    await init_db()

    # 初始化默认管理员账号（如果不存在）
    await _init_default_admin()

    # 启动定时同步调度器
    start_scheduler()

    # 启动仓库源健康检查
    start_health_checker()

    yield
    # === 关闭 ===
    stop_scheduler()
    await close_db()


async def _init_default_admin() -> None:
    """创建默认管理员账号（admin/admin）。

    仅在数据库中没有用户时创建。
    生产环境部署后应立即修改密码。
    """
    from sqlalchemy import func, select

    from app.models import User
    from app.services.auth_service import get_password_hash

    async with async_session_factory() as session:
        count = await session.scalar(select(func.count()).select_from(User))
        if count == 0:
            admin = User(
                username="admin",
                password_hash=get_password_hash("admin"),
                email="admin@sg-erm.local",
                is_admin=True,
                is_active=True,
            )
            session.add(admin)
            await session.commit()
            print("[INFO] 已创建默认管理员账号: admin / admin")
            print("[WARN] 生产环境请立即修改默认密码！")


app = FastAPI(
    title="SG-ERM",
    description="StackGres Extension Repository Manager",
    version="0.1.0",
    lifespan=lifespan,
)

# 挂载静态文件（layui 等前端资产）
app.mount(
    "/static",
    StaticFiles(directory=str(STATIC_DIR)),
    name="static",
)

# Jinja2 模板引擎
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))

# 注册 API 路由
app.include_router(auth.router)
app.include_router(audit_api.router)
app.include_router(dashboard.router)
app.include_router(extensions.router)
app.include_router(publish.router)
app.include_router(repo_files.router)
app.include_router(sources.router)
app.include_router(sync.router)
app.include_router(tokens.router)
app.include_router(whitelist.router)

# 添加审计中间件
app.add_middleware(AuditMiddleware)


@app.get("/health", response_class=JSONResponse)
async def health() -> dict:
    """健康检查端点。"""
    return {
        "status": "healthy",
        "version": app.version,
        "proxy_mode": settings.proxy_mode,
    }


@app.get("/metrics")
async def metrics():
    """Prometheus 监控指标。"""
    from app.services.metrics import metrics_response
    return metrics_response()


# ─── Web 界面页面路由 ─────────────────────────────────────────────
# Phase 1 已实现页面: dashboard, extensions, sync, sources, whitelist
# Phase 2 待实现页面: publish, audit, settings（渲染占位模板）


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request) -> HTMLResponse:
    """登录页面。"""
    return templates.TemplateResponse(
        request,
        "login.html",
        {},
    )


@app.get("/", response_class=HTMLResponse)
async def dashboard_page(request: Request) -> HTMLResponse:
    """仪表盘页面。"""
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {"title": "仪表盘", "active_nav": "dashboard"},
    )


@app.get("/extensions", response_class=HTMLResponse)
async def extensions_page(request: Request) -> HTMLResponse:
    """扩展目录页面。"""
    return templates.TemplateResponse(
        request,
        "extensions.html",
        {"title": "扩展目录", "active_nav": "extensions"},
    )


@app.get("/sync", response_class=HTMLResponse)
async def sync_page(request: Request) -> HTMLResponse:
    """同步任务页面。"""
    return templates.TemplateResponse(
        request,
        "sync.html",
        {"title": "同步任务", "active_nav": "sync"},
    )


@app.get("/sources", response_class=HTMLResponse)
async def sources_page(request: Request) -> HTMLResponse:
    """仓库源管理页面。"""
    return templates.TemplateResponse(
        request,
        "sources.html",
        {"title": "仓库源", "active_nav": "sources"},
    )


@app.get("/whitelist", response_class=HTMLResponse)
async def whitelist_page(request: Request) -> HTMLResponse:
    """全局白名单页面。"""
    return templates.TemplateResponse(
        request,
        "whitelist.html",
        {"title": "全局白名单", "active_nav": "whitelist"},
    )


@app.get("/publish", response_class=HTMLResponse)
async def publish_page(request: Request) -> HTMLResponse:
    """自定义扩展发布页面。"""
    return templates.TemplateResponse(
        request,
        "publish.html",
        {"title": "自定义扩展", "active_nav": "publish"},
    )


@app.get("/repo-files", response_class=HTMLResponse)
async def repo_files_page(request: Request) -> HTMLResponse:
    """仓库文件浏览页面。"""
    return templates.TemplateResponse(
        request,
        "repo_files.html",
        {"title": "仓库文件", "active_nav": "repo_files"},
    )


@app.get("/extensions/{name}", response_class=HTMLResponse)
async def extension_detail_page(request: Request, name: str) -> HTMLResponse:
    """扩展详情页面。"""
    return templates.TemplateResponse(
        request,
        "extension_detail.html",
        {"title": f"扩展: {name}", "active_nav": "extensions", "ext_name": name},
    )


@app.get("/audit", response_class=HTMLResponse)
async def audit_page(request: Request) -> HTMLResponse:
    """审计日志页面。"""
    return templates.TemplateResponse(
        request,
        "audit.html",
        {"title": "审计日志", "active_nav": "audit"},
    )


@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request) -> HTMLResponse:
    """系统设置页面。"""
    return templates.TemplateResponse(
        request,
        "settings.html",
        {"title": "系统设置", "active_nav": "settings"},
    )


# ─── StackGres 兼容接口 ──────────────────────────────────────────
# 这些路由必须在所有 API 和静态文件路由之后注册，避免冲突。
# StackGres 集群通过这些端点获取 index.json 和扩展包。


@app.get("/v2/index.json")
async def stackgres_index():
    """StackGres 兼容：返回 index.json。

    优先返回本地缓存的 index.json；
    如果本地不存在，从上游获取并缓存。
    """
    index_path = await proxy_engine.handle_index_request()
    if index_path and index_path.exists():
        return FileResponse(
            str(index_path),
            media_type="application/json",
            headers={"Cache-Control": "public, max-age=300"},
        )
    # 回退：重定向到上游
    return RedirectResponse(
        url=f"{settings.upstream_repo_url}/v2/index.json",
        status_code=302,
    )


@app.get("/{publisher}/{arch}/{os_name}/{package_name}.tar")
async def stackgres_package(
    publisher: str,
    arch: str,
    os_name: str,
    package_name: str,
):
    """StackGres 兼容：代理下载扩展包。

    路由: /{publisher}/{arch}/{os}/{package_name}.tar
    示例: /com.ongres/x86_64/linux/postgis-3.4-pg16.4.tar

    流程:
    - HIT: 本地有 → 返回文件
    - MISS: 本地无 → 从上游拉取 → 缓存 → 返回
    - 404: 上游也没有 → 404
    """
    # 排除 API 和静态文件路径（虽然它们先注册，但作为安全检查）
    if publisher in ("api", "static", "health"):
        raise HTTPException(status_code=404)

    file_path, status = await proxy_engine.handle_package_request(
        publisher, arch, os_name, package_name,
    )

    if file_path and file_path.exists():
        return FileResponse(
            str(file_path),
            media_type="application/octet-stream",
            filename=f"{package_name}.tar",
            headers={
                "X-Cache-Status": status,  # HIT 或 MISS
                "Cache-Control": "public, max-age=86400",
            },
        )

    # 404
    raise HTTPException(
        status_code=404,
        detail=f"Package not found: {publisher}/{arch}/{os_name}/{package_name}.tar",
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=settings.listen_host,
        port=settings.listen_port,
        reload=True,
    )
