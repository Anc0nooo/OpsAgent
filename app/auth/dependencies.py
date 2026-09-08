"""
FastAPI 认证依赖：get_current_user / require_admin

- get_current_user：从请求头 Authorization: Bearer <token> 解析 JWT，返回当前用户
- require_admin：在 get_current_user 基础上校验 role == 'ancon'，非管理员返回 403
"""
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.auth.jwt import decode_token
from app.db.engine import get_db
from app.db.models import User

# Bearer token 提取器（auto_error=False：缺头/格式错时返回 None，统一由 get_current_user 抛 401）
security = HTTPBearer(auto_error=False)


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
    db: Session = Depends(get_db),
) -> User:
    """
    解析 JWT 并返回当前用户对象。
    缺失 Authorization 头 / 无效 token / 用户不存在 → 401；用户被禁用 → 403。
    """
    if credentials is None or not credentials.credentials:
        # 缺失/格式错误的 Authorization 头统一返回 401（前端据此跳登录页）
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="未登录或登录已过期，请重新登录",
            headers={"WWW-Authenticate": "Bearer"},
        )

    payload = decode_token(credentials.credentials)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token 无效或已过期，请重新登录",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user_id = payload.get("user_id")
    if user_id is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token 无效")

    user = db.query(User).filter(User.id == user_id).first()
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户不存在")

    if user.status != 1:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="账号已被禁用")

    return user


def require_admin(user: User = Depends(get_current_user)) -> User:
    """管理员鉴权：role == 'ancon' 才允许访问，否则 403"""
    if user.role != "ancon":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="无管理员权限",
        )
    return user
