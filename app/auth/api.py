"""
认证 API 路由
- POST /api/auth/register：注册（username + password）
- POST /api/auth/login：登录（返回 JWT token）
- GET  /api/auth/me：当前用户信息（需鉴权，含头像）
- POST /api/auth/avatar：上传头像（需鉴权，存 base64 data URL）
- DELETE /api/auth/avatar：移除头像（恢复默认用户名首字符）
"""
import base64

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user
from app.auth.jwt import create_token
from app.auth.password import hash_password, verify_password
from app.db.engine import get_db
from app.db.models import User, UserConfig

router = APIRouter(prefix="/api/auth", tags=["认证"])

# 头像限制：2MB，仅图片类型
AVATAR_MAX_BYTES = 2 * 1024 * 1024
_AVATAR_TYPES = {
    "image/jpeg": "jpeg", "image/png": "png",
    "image/webp": "webp", "image/gif": "gif",
}


class RegisterIn(BaseModel):
    username: str = Field(..., min_length=2, max_length=50, description="用户名")
    password: str = Field(..., min_length=6, max_length=128, description="密码")


class LoginIn(BaseModel):
    username: str
    password: str


@router.post("/register")
def register(body: RegisterIn, db: Session = Depends(get_db)) -> dict:
    """注册新用户：用户名不重复，密码 bcrypt 哈希后存库"""
    # 校验用户名不重复
    existing = db.query(User).filter(User.username == body.username).first()
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="用户名已存在")

    user = User(
        username=body.username,
        password_hash=hash_password(body.password),
    )
    db.add(user)
    db.flush()  # 拿到 user.id

    # 为新用户创建默认配置行（provider=dashscope）
    cfg = UserConfig(user_id=user.id, provider="dashscope")
    db.add(cfg)
    db.commit()
    db.refresh(user)

    return {"code": 0, "message": "注册成功", "data": {"user_id": user.id, "username": user.username}}


@router.post("/login")
def login(body: LoginIn, db: Session = Depends(get_db)) -> dict:
    """登录：校验密码，返回 JWT token"""
    user = db.query(User).filter(User.username == body.username).first()
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户名或密码错误")
    if user.status != 1:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="账号已被禁用")

    token = create_token(user.id, user.username)
    return {
        "code": 0,
        "message": "登录成功",
        "data": {"token": token, "user_id": user.id, "username": user.username},
    }


@router.get("/me")
def me(user: User = Depends(get_current_user)) -> dict:
    """当前用户信息（需鉴权；avatar 为 data URL，空表示用用户名首字符兜底）"""
    return {
        "code": 0,
        "message": "ok",
        "data": {
            "user_id": user.id,
            "username": user.username,
            "status": user.status,
            "avatar": user.avatar or "",
        },
    }


@router.post("/avatar")
async def upload_avatar(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    """上传头像（multipart 图片文件，≤2MB，存 base64 data URL 到 users.avatar）"""
    data = await file.read()
    if len(data) > AVATAR_MAX_BYTES:
        raise HTTPException(status_code=400, detail="头像文件过大（最大 2MB）")

    mime = (file.content_type or "").lower()
    if mime not in _AVATAR_TYPES:
        # content_type 缺失时按扩展名兜底
        name = (file.filename or "").lower()
        ext_map = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
                   ".webp": "image/webp", ".gif": "image/gif"}
        mime = next((v for k, v in ext_map.items() if name.endswith(k)), "")
        if not mime:
            raise HTTPException(status_code=400, detail="仅支持 jpg/png/webp/gif 图片")

    data_url = f"data:{mime};base64,{base64.b64encode(data).decode()}"
    user.avatar = data_url
    db.commit()
    return {"code": 0, "message": "头像已更新", "data": {"avatar": data_url}}


@router.delete("/avatar")
def remove_avatar(db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> dict:
    """移除头像（前端恢复为用户名首字符默认头像）"""
    user.avatar = None
    db.commit()
    return {"code": 0, "message": "头像已移除", "data": {"avatar": ""}}
