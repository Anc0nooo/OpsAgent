"""
统一响应模型
所有 REST 接口返回 {code, message, data} 结构。
"""
from typing import Any, Optional

from pydantic import BaseModel


class ApiResponse(BaseModel):
    """统一响应体"""

    code: int = 0                 # 0=成功，非 0=业务/系统错误码
    message: str = "ok"           # 提示信息
    data: Optional[Any] = None    # 业务数据


def ok(data: Any = None, message: str = "ok") -> dict[str, Any]:
    """成功响应快捷构造"""
    return {"code": 0, "message": message, "data": data}


def fail(code: int, message: str, data: Any = None) -> dict[str, Any]:
    """失败响应快捷构造"""
    return {"code": code, "message": message, "data": data}
