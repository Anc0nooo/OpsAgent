"""
操作日志工具（管理员可见）

在关键接口调用点埋点：登录/注册/对话/上传/删除/检索/配置保存/SQL 查询
用法：
    from app.auth.log import log_operation
    log_operation(db, user_id, "chat", "用户消息摘要", request)
"""
from fastapi import Request
from sqlalchemy.orm import Session

from app.db.models import OperationLog


def log_operation(
    db: Session,
    user_id: int,
    action: str,
    detail: str = "",
    request: Request | None = None,
    username: str | None = None,
) -> None:
    """
    写一条操作日志（静默失败，不影响主流程）

    Args:
        db: 数据库会话
        user_id: 操作者 ID
        action: 操作类型（login/logout/chat/upload_doc/delete_doc/search/retrieve/config_save/sql_query/register）
        detail: 操作详情摘要
        request: FastAPI 请求对象（取 IP）
        username: 用户名（已知时直接传，省去查表；删除用户后日志仍可读）
    """
    try:
        ip = ""
        if request is not None and request.client:
            ip = request.client.host or ""

        log = OperationLog(
            user_id=user_id,
            username=username or "",
            action=action,
            detail=detail[:500] if detail else None,  # 截断防超长
            ip=ip or None,
        )
        db.add(log)
        db.commit()
    except Exception:  # noqa: BLE001
        # 日志写入失败不应阻断业务
        db.rollback()
