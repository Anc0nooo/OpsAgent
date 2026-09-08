"""
管理员 API 路由（均需 require_admin 鉴权，role == 'ancon'）

接口：
- GET    /api/admin/users              用户列表（分页 + 关键词搜索）
- PATCH  /api/admin/users/{id}/status   启用/禁用用户
- PATCH  /api/admin/users/{id}/role     修改角色（ancon/user）
- POST   /api/admin/users/{id}/reset-password  重置密码
- DELETE /api/admin/users/{id}          删除用户（级联清理 + Chroma collection）
- GET    /api/admin/logs                全部日志（分页 + 筛选）
- GET    /api/admin/logs/user/{user_id} 指定用户日志
- GET    /api/admin/logs/export         导出日志 CSV
"""
import csv
import io
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth.dependencies import require_admin
from app.auth.log import log_operation
from app.auth.password import hash_password
from app.db.engine import get_db
from app.db.models import (
    Conversation, KnowledgeChunk, KnowledgeDoc, Message, OperationLog,
    User, UserConfig,
)
from app.models.common import ok
from app.rag.service import knowledge_service

router = APIRouter(prefix="/api/admin", tags=["管理员"])


# ------------------------------------------------------------------
# 请求模型
# ------------------------------------------------------------------
class StatusIn(BaseModel):
    status: int = Field(..., ge=0, le=1, description="1=启用 0=禁用")


class RoleIn(BaseModel):
    role: str = Field(..., pattern="^(ancon|user)$", description="ancon 或 user")


class ResetPasswordIn(BaseModel):
    password: str = Field(..., min_length=6, max_length=128)


# ------------------------------------------------------------------
# 用户管理
# ------------------------------------------------------------------
@router.get("/users")
def list_users(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    keyword: str = Query("", description="用户名关键词"),
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
) -> dict:
    """用户列表（分页 + 关键词搜索 username）"""
    q = db.query(User)
    if keyword:
        q = q.filter(User.username.like(f"%{keyword}%"))
    total = q.count()
    users = q.order_by(User.id).offset((page - 1) * page_size).limit(page_size).all()
    return ok({
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [
            {
                "id": u.id,
                "username": u.username,
                "role": u.role,
                "status": u.status,
                "avatar": u.avatar or "",
                "created_at": str(u.created_at) if u.created_at else "",
            }
            for u in users
        ],
    })


@router.patch("/users/{user_id}/status")
def update_status(
    user_id: int,
    body: StatusIn,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
) -> dict:
    """启用/禁用用户（禁用后该用户 token 失效，登录时拒绝）"""
    user = db.query(User).filter(User.id == user_id).first()
    if user is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    if user.id == admin.id and body.status == 0:
        raise HTTPException(status_code=400, detail="不能禁用自己的账号")
    user.status = body.status
    db.commit()
    log_operation(db, admin.id, "update_status",
                  f"{'启用' if body.status == 1 else '禁用'}用户 {user.username}", username=admin.username)
    return ok({"message": f"已{'启用' if body.status == 1 else '禁用'}", "status": body.status})


@router.patch("/users/{user_id}/role")
def update_role(
    user_id: int,
    body: RoleIn,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
) -> dict:
    """修改角色（ancon / user）"""
    user = db.query(User).filter(User.id == user_id).first()
    if user is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    if user.id == admin.id and body.role != "ancon":
        raise HTTPException(status_code=400, detail="不能取消自己的管理员角色")
    old_role = user.role
    user.role = body.role
    db.commit()
    log_operation(db, admin.id, "update_role",
                  f"修改 {user.username} 角色: {old_role} → {body.role}", username=admin.username)
    return ok({"message": f"角色已更新为 {body.role}", "role": body.role})


@router.post("/users/{user_id}/reset-password")
def reset_password(
    user_id: int,
    body: ResetPasswordIn,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
) -> dict:
    """重置用户密码（管理员设置新密码，bcrypt 哈希存库）"""
    user = db.query(User).filter(User.id == user_id).first()
    if user is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    user.password_hash = hash_password(body.password)
    db.commit()
    log_operation(db, admin.id, "reset_password",
                  f"重置用户 {user.username} 密码", username=admin.username)
    return ok({"message": "密码已重置"})


@router.delete("/users/{user_id}")
def delete_user(
    user_id: int,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
) -> dict:
    """
    删除用户 + 级联清理：
    - MySQL: user_configs / knowledge_docs / knowledge_chunks / conversations / messages（CASCADE）
    - Chroma: 删除 user_{user_id}_knowledge collection
    - 禁止删除自己
    """
    if user_id == admin.id:
        raise HTTPException(status_code=400, detail="不能删除自己的账号")

    user = db.query(User).filter(User.id == user_id).first()
    if user is None:
        raise HTTPException(status_code=404, detail="用户不存在")

    username_del = user.username

    # 1. Chroma collection 清理（先删向量，避免遗留）
    try:
        knowledge_service._chroma_client.delete_collection(
            knowledge_service._collection_name(user_id)
        )
        knowledge_service._collections.pop(user_id, None)
        knowledge_service._bm25_map.pop(user_id, None)
    except Exception as e:  # noqa: BLE001
        # collection 可能不存在（用户未上传过文档），忽略
        pass

    # 2. MySQL 级联删除（外键 CASCADE 自动清理子表）
    db.delete(user)
    db.commit()

    log_operation(db, admin.id, "delete_user",
                  f"删除用户 {username_del}（含配置/会话/文档/向量）", username=admin.username)
    return ok({"message": f"用户 {username_del} 已删除（含所有关联数据）"})


# ------------------------------------------------------------------
# 操作日志
# ------------------------------------------------------------------
def _log_to_dict(log: OperationLog) -> dict:
    return {
        "id": log.id,
        "user_id": log.user_id,
        "username": log.username or "",
        "action": log.action,
        "detail": log.detail or "",
        "ip": log.ip or "",
        "created_at": str(log.created_at) if log.created_at else "",
    }


@router.get("/logs")
def list_logs(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    user_id: int | None = Query(None, description="按用户筛选"),
    action: str = Query("", description="按操作类型筛选"),
    start_time: str = Query("", description="开始时间 YYYY-MM-DD"),
    end_time: str = Query("", description="结束时间 YYYY-MM-DD"),
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
) -> dict:
    """全部操作日志（分页 + 按 user_id / action / 时间范围筛选）"""
    q = db.query(OperationLog)
    if user_id is not None:
        q = q.filter(OperationLog.user_id == user_id)
    if action:
        q = q.filter(OperationLog.action == action)
    if start_time:
        try:
            dt = datetime.strptime(start_time, "%Y-%m-%d")
            q = q.filter(OperationLog.created_at >= dt)
        except ValueError:
            pass
    if end_time:
        try:
            dt = datetime.strptime(end_time, "%Y-%m-%d").replace(
                hour=23, minute=59, second=59
            )
            q = q.filter(OperationLog.created_at <= dt)
        except ValueError:
            pass
    total = q.count()
    logs = q.order_by(OperationLog.id.desc()).offset(
        (page - 1) * page_size
    ).limit(page_size).all()
    return ok({
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [_log_to_dict(l) for l in logs],
    })


@router.get("/logs/user/{user_id}")
def user_logs(
    user_id: int,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
) -> dict:
    """指定用户的操作日志"""
    q = db.query(OperationLog).filter(OperationLog.user_id == user_id)
    total = q.count()
    logs = q.order_by(OperationLog.id.desc()).offset(
        (page - 1) * page_size
    ).limit(page_size).all()
    return ok({
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [_log_to_dict(l) for l in logs],
    })


@router.get("/logs/export")
def export_logs(
    user_id: int | None = Query(None),
    action: str = Query(""),
    start_time: str = Query(""),
    end_time: str = Query(""),
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
) -> StreamingResponse:
    """导出日志 CSV（支持同样筛选条件）"""
    q = db.query(OperationLog)
    if user_id is not None:
        q = q.filter(OperationLog.user_id == user_id)
    if action:
        q = q.filter(OperationLog.action == action)
    if start_time:
        try:
            q = q.filter(OperationLog.created_at >= datetime.strptime(start_time, "%Y-%m-%d"))
        except ValueError:
            pass
    if end_time:
        try:
            q = q.filter(OperationLog.created_at <= datetime.strptime(end_time, "%Y-%m-%d").replace(
                hour=23, minute=59, second=59))
        except ValueError:
            pass
    logs = q.order_by(OperationLog.id.desc()).limit(10000).all()

    buf = io.StringIO()
    buf.write("\ufeff")  # BOM（Excel 识别 UTF-8）
    writer = csv.writer(buf)
    writer.writerow(["ID", "用户ID", "用户名", "操作", "详情", "IP", "时间"])
    for l in logs:
        writer.writerow([
            l.id, l.user_id, l.username or "", l.action,
            l.detail or "", l.ip or "",
            str(l.created_at) if l.created_at else "",
        ])
    buf.seek(0)

    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=operation_logs.csv"},
    )
