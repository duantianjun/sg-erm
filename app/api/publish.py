"""自定义扩展发布 API。

提供扩展上传、发布者管理和已发布扩展列表。
"""
import os
import tempfile
import uuid

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.response import error_response, success, success_response
from app.database import get_db
from app.models import Extension, ExtensionBuild, ExtensionVersion, Publisher, User
from app.services.auth_service import require_admin
from app.services.publish_service import create_custom_publisher, publish_extension

router = APIRouter(
    prefix="/api/v1/publish",
    tags=["publish"],
    dependencies=[Depends(require_admin)],
)


# ─── 发布者管理 ──────────────────────────────────────

class PublisherCreate(BaseModel):
    """创建自定义发布者请求。"""
    name: str
    display_name: str | None = None


@router.get("/publishers")
async def list_publishers(
    db: AsyncSession = Depends(get_db),
):
    """自定义发布者列表。"""
    result = await db.execute(
        select(Publisher)
        .where(Publisher.is_custom == True)
        .order_by(Publisher.name)
    )
    publishers = result.scalars().all()

    data = [
        {
            "id": p.id,
            "name": p.name,
            "display_name": p.display_name,
            "public_key": p.public_key,
            "is_custom": p.is_custom,
            "created_at": p.created_at.isoformat() if p.created_at else None,
        }
        for p in publishers
    ]
    return success(data, len(data))


@router.post("/publishers")
async def create_publisher(
    body: PublisherCreate,
    db: AsyncSession = Depends(get_db),
):
    """创建自定义发布者（自动生成 RSA 密钥对）。"""
    # 检查名称是否已存在
    existing = await db.scalar(
        select(func.count()).select_from(Publisher).where(Publisher.name == body.name)
    )
    if existing and existing > 0:
        return error_response(f"发布者 '{body.name}' 已存在")

    publisher = await create_custom_publisher(db, body.name, body.display_name)

    return success(
        {
            "id": publisher.id,
            "name": publisher.name,
            "display_name": publisher.display_name,
            "public_key": publisher.public_key,
        },
        1,
        "创建成功",
    )


@router.delete("/publishers/{publisher_id}")
async def delete_publisher(
    publisher_id: str,
    db: AsyncSession = Depends(get_db),
):
    """删除自定义发布者（谨慎操作，会删除关联的自定义扩展）。"""
    publisher = await db.get(Publisher, publisher_id)
    if not publisher:
        return error_response("发布者不存在", status_code=404)

    if not publisher.is_custom:
        return error_response("不能删除系统发布者")

    await db.delete(publisher)
    await db.commit()

    return success({"id": publisher_id}, 1, "删除成功")


# ─── 扩展发布 ────────────────────────────────────────

@router.post("/upload")
async def upload_extension(
    publisher_id: str = Form(..., description="发布者 ID"),
    ext_name: str = Form(..., description="扩展名称"),
    version: str = Form(..., description="版本号"),
    flavor: str = Form("pg", description="风味: pg/bf"),
    pg_version: str = Form(..., description="PostgreSQL 版本，如 16.4"),
    arch: str = Form("x86_64", description="架构"),
    os_name: str = Form("linux", description="操作系统"),
    build_num: str | None = Form(None, description="构建号"),
    channel: str = Form("stable", description="通道: stable/beta/dev"),
    description: str = Form("", description="扩展描述"),
    license_str: str = Form("", description="许可证"),
    tags: str = Form("", description="标签，逗号分隔"),
    tgz_file: UploadFile = File(..., description=".tgz 扩展包文件"),
    db: AsyncSession = Depends(get_db),
):
    """上传并发布自定义扩展。

    流程:
    1. 接收 .tgz 文件和元数据
    2. 校验 .tgz（检查 .control 文件）
    3. 用发布者私钥签名 → .sha256
    4. 打包为 .tar（.sha256 + .tgz）
    5. 写入本地存储
    6. 更新 index.json 和数据库
    """
    # 验证文件类型
    if not tgz_file.filename or not tgz_file.filename.endswith(".tgz"):
        return error_response("只接受 .tgz 文件")

    # 解析标签
    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []

    # 保存上传文件到临时目录
    tmp_dir = tempfile.mkdtemp(prefix="sg-erm-upload-")
    tmp_path = os.path.join(tmp_dir, f"{uuid.uuid4()}.tgz")

    try:
        with open(tmp_path, "wb") as f:
            content = await tgz_file.read()
            f.write(content)

        # 发布
        result = await publish_extension(
            session=db,
            publisher_id=publisher_id,
            tgz_path=tmp_path,
            ext_name=ext_name,
            version=version,
            flavor=flavor,
            pg_version=pg_version,
            arch=arch,
            os_name=os_name,
            build_num=build_num,
            channel=channel,
            description=description,
            license_str=license_str,
            tags=tag_list,
        )

        if result["success"]:
            return success(
                {
                    "package_path": result["package_path"],
                    "ext_name": ext_name,
                    "version": version,
                    "publisher": publisher_id,
                },
                1,
                "发布成功",
            )
        else:
            return error_response(result["error"])

    finally:
        # 清理临时文件
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        if os.path.exists(tmp_dir):
            os.rmdir(tmp_dir)


# ─── 已发布扩展列表 ──────────────────────────────────

@router.get("/extensions")
async def list_published_extensions(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    publisher_id: str = Query("", description="发布者过滤"),
    db: AsyncSession = Depends(get_db),
):
    """已发布的自定义扩展列表（仅 is_custom=True）。"""
    # 预加载 publisher 关系，避免懒加载
    query = (
        select(Extension)
        .options(selectinload(Extension.publisher))
        .where(Extension.is_custom == True)
        .order_by(Extension.name)
    )

    if publisher_id:
        query = query.where(Extension.publisher_id == publisher_id)

    # 总数
    count_query = select(func.count()).select_from(query.subquery())
    total = await db.scalar(count_query) or 0

    # 分页
    query = query.offset((page - 1) * limit).limit(limit)
    result = await db.execute(query)
    extensions = result.scalars().all()

    # 一次性获取所有扩展的版本数和构建数（避免 N+1）
    ext_ids = [ext.id for ext in extensions]
    ver_counts = {}
    build_counts = {}

    if ext_ids:
        # 版本数
        ver_result = await db.execute(
            select(ExtensionVersion.extension_id, func.count())
            .where(ExtensionVersion.extension_id.in_(ext_ids))
            .group_by(ExtensionVersion.extension_id)
        )
        for eid, cnt in ver_result.all():
            ver_counts[eid] = cnt

        # 构建数（通过 version_id 关联）
        build_result = await db.execute(
            select(ExtensionVersion.extension_id, func.count())
            .join(ExtensionBuild)
            .where(ExtensionVersion.extension_id.in_(ext_ids))
            .group_by(ExtensionVersion.extension_id)
        )
        for eid, cnt in build_result.all():
            build_counts[eid] = cnt

    data = []
    for ext in extensions:
        data.append({
            "id": ext.id,
            "name": ext.name,
            "description": ext.description or "",
            "publisher": ext.publisher.name if ext.publisher else "",
            "publisher_id": ext.publisher_id,
            "license": ext.license or "",
            "version_count": ver_counts.get(ext.id, 0),
            "build_count": build_counts.get(ext.id, 0),
            "updated_at": ext.updated_at.isoformat() if ext.updated_at else None,
        })

    return success(data, total)
