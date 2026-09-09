"""
版本更新 API

- GET /api/version  获取当前版本号 + 更新日志（任意已登录用户可读）
- PUT /api/version  管理员更新版本号 / 更新日志（require_admin）

前端登录后拉取版本，与 localStorage 的 last_version 比对，不一致则弹更新弹窗。
"""
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user, require_admin
from app.auth.log import log_operation
from app.db.engine import get_db
from app.db.models import AppVersion, User
from app.models.common import ok

router = APIRouter(prefix="/api/version", tags=["版本更新"])


class VersionIn(BaseModel):
    version: str = Field(..., min_length=1, max_length=20, description="版本号，如 v1.1")
    changelog: str = Field("", max_length=100000, description="更新日志（Markdown 富文本）")


def _get_or_create_row(db: Session) -> AppVersion:
    row = db.query(AppVersion).filter(AppVersion.id == 1).first()
    if row is None:
        row = AppVersion(id=1, version="v1.0", changelog="")
        db.add(row)
        db.commit()
        db.refresh(row)
    return row


@router.get("")
def get_version(
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> dict:
    """获取当前版本与更新日志（任意已登录用户）"""
    row = _get_or_create_row(db)
    return ok({
        "version": row.version,
        "changelog": row.changelog or "",
        "updated_at": str(row.updated_at) if row.updated_at else "",
    })


@router.put("")
def update_version(
    body: VersionIn,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
) -> dict:
    """管理员更新版本号 / 更新日志"""
    row = _get_or_create_row(db)
    old_version = row.version
    row.version = body.version.strip()
    row.changelog = body.changelog or ""
    db.commit()
    db.refresh(row)
    log_operation(db, admin.id, "update_version",
                  f"版本更新: {old_version} → {row.version}", username=admin.username)
    return ok({
        "version": row.version,
        "changelog": row.changelog,
        "updated_at": str(row.updated_at) if row.updated_at else "",
    })
