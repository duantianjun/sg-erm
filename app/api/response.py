# -*- coding: utf-8 -*-
"""layui 兼容响应格式。

layui table 组件期望的 JSON 格式:
{
    "code": 0,    // 0=成功, 非0=失败
    "msg": "",    // 错误信息
    "count": 100, // 总记录数（用于分页）
    "data": [...]  // 数据列表
}
"""
from typing import Any, Optional

from fastapi.responses import JSONResponse


def success(
    data: Any = None,
    count: Optional[int] = None,
    msg: str = "",
) -> dict:
    """成功响应。

    Args:
        data: 数据（列表或对象）
        count: 总记录数（分页时使用，非列表时可省略）
        msg: 消息
    """
    if isinstance(data, list):
        if count is None:
            count = len(data)
    elif data is None:
        data = []
        count = count or 0
    else:
        # 单个对象
        if count is None:
            count = 1
        data = [data]

    return {"code": 0, "msg": msg, "count": count, "data": data}


def error(msg: str, code: int = 1) -> dict:
    """错误响应。"""
    return {"code": code, "msg": msg, "count": 0, "data": []}


def success_response(
    data: Any = None,
    count: Optional[int] = None,
    msg: str = "",
    status_code: int = 200,
) -> JSONResponse:
    """成功 JSONResponse。"""
    return JSONResponse(
        content=success(data, count, msg),
        status_code=status_code,
    )


def error_response(
    msg: str,
    code: int = 1,
    status_code: int = 400,
) -> JSONResponse:
    """错误 JSONResponse。"""
    return JSONResponse(
        content=error(msg, code),
        status_code=status_code,
    )
