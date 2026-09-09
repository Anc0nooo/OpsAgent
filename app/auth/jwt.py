"""JWT token 生成与解析"""
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt as pyjwt

from app.config.settings import settings


def create_token(user_id: int, username: str, role: str = "user") -> str:
    """生成 JWT token（含 user_id + username + role）

    有效期：JWT_EXPIRE_MINUTES>0 时按分钟（调试用），否则按 JWT_EXPIRE_HOURS 小时（默认 5 小时）。
    """
    now = datetime.now(timezone.utc)
    if settings.JWT_EXPIRE_MINUTES and settings.JWT_EXPIRE_MINUTES > 0:
        expire_delta = timedelta(minutes=settings.JWT_EXPIRE_MINUTES)
    else:
        expire_delta = timedelta(hours=settings.JWT_EXPIRE_HOURS)
    payload: dict[str, Any] = {
        "user_id": user_id,
        "username": username,
        "role": role,
        "exp": now + expire_delta,
        "iat": now,
    }
    return pyjwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def decode_token(token: str) -> dict[str, Any] | None:
    """
    解析 JWT token，返回 payload；无效/过期返回 None。
    """
    try:
        payload = pyjwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
        return payload
    except pyjwt.PyJWTError:
        return None
