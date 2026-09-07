"""健康检查接口"""
from fastapi import APIRouter

from app.config.settings import settings
from app.models.common import ok

router = APIRouter(prefix="/api")


@router.get("/health")
def health() -> dict:
    """健康检查：返回服务状态与关键配置（不含敏感信息）"""
    return ok({
        "status": "ok",
        "app": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "sql_dialect": settings.SQL_DIALECT,
        "chat_model": settings.CHAT_MODEL,
        "embed_model": settings.EMBED_MODEL,
        "rerank_model": settings.RERANK_MODEL,
        "api_key_configured": bool(settings.DASHSCOPE_API_KEY),
    })
