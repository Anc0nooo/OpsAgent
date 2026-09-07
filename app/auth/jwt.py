"""JWT token 生成与解析"""
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt as pyjwt

from app.config.settings import settings


def create_token(user_id: int, username: str) -> str:
    """生成 JWT token（含 user_id + username，有效期 JWT_EXPIRE_DAYS 天）"""
    payload: dict[str, Any] = {
        "user_id": user_id,
        "username": username,
        "exp": datetime.now(timezone.utc) + timedelta(days=settings.JWT_EXPIRE_DAYS),
        "iat": datetime.now(timezone.utc),
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
