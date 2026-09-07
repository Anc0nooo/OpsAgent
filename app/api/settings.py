"""
模型配置接口（多用户：每用户独立配置，存 user_configs 表）

接口（均需登录）：
- GET  /api/settings/status  查询当前用户配置（api_key 掩码，不返回明文）
- POST /api/settings/keys    保存当前用户配置（api_key + base_url + 模型名 → user_configs，upsert）
- POST /api/settings/test    测试当前用户 api_key 连通性（临时 client 发一条 chat）

设计：
- 每个 user 在 user_configs 表有唯一一行（user_id + provider=dashscope）
- 保存后调用 invalidate_user_llm(user_id) 清除 LLM 客户端缓存，下次请求重建
- 敏感信息（API key）只在浏览器短暂存在，后端不回显明文
"""
import logging

from fastapi import APIRouter, Depends
from openai import OpenAI
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user
from app.config.settings import settings
from app.core.llm import invalidate_user_llm
from app.db.engine import get_db
from app.db.models import User, UserConfig
from app.models.common import fail, ok

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/settings", tags=["配置管理"])


class ConfigIn(BaseModel):
    """配置保存请求（每用户独立；接收 api_key + 可选 base_url/模型名）"""
    api_key: str = Field(default="", description="阿里百炼 API Key（空则不变）")
    base_url: str = Field(default="", description="兼容模式 base_url（空用默认）")
    chat_model: str = Field(default="", description="对话模型（空用默认）")
    embed_model: str = Field(default="", description="向量模型（空用默认）")
    rerank_model: str = Field(default="", description="重排模型（空用默认）")


class TestIn(BaseModel):
    """连接测试请求（仅接收 API Key；base_url/模型名用后端默认值）"""
    api_key: str


def _is_placeholder(val: str) -> bool:
    """判断 key 是否为占位符（空串 / sk-xxxx / example / your-key）"""
    if not val.strip():
        return True
    v = val.strip().lower()
    return "xxxx" in v or "example" in v or "your-key" in v


def _mask_key(val: str) -> str:
    """API-KEY 掩码（保留首 6 末 4，中间 ****）"""
    v = val.strip()
    if not v:
        return ""
    if len(v) <= 10:
        return v[:2] + "****"
    return v[:6] + "****" + v[-4:]


def _mask_key_short(val: str) -> str:
    """API-KEY 短掩码（前 3 后 4，中间 ****），用于设置弹窗提示"""
    v = val.strip()
    if not v:
        return ""
    if len(v) <= 7:
        return v[:1] + "****"
    return v[:3] + "****" + v[-4:]


def _get_user_config(db: Session, user_id: int) -> UserConfig:
    """获取当前用户配置行（不存在则创建默认行）"""
    cfg = db.query(UserConfig).filter(
        UserConfig.user_id == user_id, UserConfig.provider == "dashscope"
    ).first()
    if cfg is None:
        cfg = UserConfig(user_id=user_id, provider="dashscope")
        db.add(cfg)
        db.flush()
    return cfg


@router.get("/status")
def get_status(db: Session = Depends(get_db),
               user: User = Depends(get_current_user)) -> dict:
    """查询当前用户配置状态（不返回 key 明文；api_key_masked 供前端回填提示）"""
    cfg = _get_user_config(db, user.id)
    api_key = cfg.api_key or ""
    configured = bool(api_key) and not _is_placeholder(api_key)
    return ok({
        "chat_provider": "dashscope",
        "chat_provider_name": "阿里百炼",
        "chat_base_url": cfg.base_url or settings.DASHSCOPE_BASE_URL,
        "chat_api_key_masked": _mask_key(api_key) if configured else "",
        "has_api_key": configured,
        "api_key_masked": _mask_key_short(api_key) if configured else "",
        "chat_configured": configured,
        "chat_model": cfg.chat_model or settings.CHAT_MODEL,
        "dashscope_configured": configured,
        "dashscope_key_masked": _mask_key(api_key) if configured else "",
        "embed_model": cfg.embed_model or settings.EMBED_MODEL,
        "rerank_model": cfg.rerank_model or settings.RERANK_MODEL,
        "first_time": not configured,
    })


@router.post("/keys")
def save_config(body: ConfigIn, db: Session = Depends(get_db),
                user: User = Depends(get_current_user)) -> dict:
    """保存当前用户配置到 user_configs（upsert；空字段不覆盖）"""
    cfg = _get_user_config(db, user.id)
    if body.api_key.strip():
        cfg.api_key = body.api_key.strip()
    if body.base_url.strip():
        cfg.base_url = body.base_url.strip()
    if body.chat_model.strip():
        cfg.chat_model = body.chat_model.strip()
    if body.embed_model.strip():
        cfg.embed_model = body.embed_model.strip()
    if body.rerank_model.strip():
        cfg.rerank_model = body.rerank_model.strip()
    db.commit()

    # 清除当前用户 LLM 客户端缓存，下次请求按新配置重建
    invalidate_user_llm(user.id)
    logger.info("用户 %s 配置已保存并清除客户端缓存", user.id)
    return ok({"message": f"配置已生效，当前使用阿里百炼 {cfg.chat_model or settings.CHAT_MODEL} 模型"})


@router.post("/test")
def test_config(body: TestIn, user: User = Depends(get_current_user)) -> dict:
    """测试百炼连通性（用请求中的 api_key 临时发一条 chat；base_url/模型名用后端默认值）"""
    if not body.api_key.strip() or _is_placeholder(body.api_key):
        return fail(400, "请先填写 API Key")
    try:
        client = OpenAI(
            api_key=body.api_key.strip(),
            base_url=settings.DASHSCOPE_BASE_URL,
            timeout=20,
            max_retries=0,
        )
        resp = client.chat.completions.create(
            model=settings.CHAT_MODEL,
            messages=[{"role": "user", "content": "hi"}],
            max_tokens=5,
        )
        model_used = resp.model or settings.CHAT_MODEL
        return ok({"ok": True, "message": f"连接成功，模型 {model_used} 可用"})
    except Exception as e:  # noqa: BLE001
        msg = str(e)
        low = msg.lower()
        if "401" in msg or "invalid api key" in low or "incorrect api key" in low:
            return fail(401, "API Key 无效或格式错误")
        if "429" in msg or "quota" in low:
            return fail(429, "API 额度不足，请前往百炼控制台充值")
        if "403" in msg or "forbidden" in low:
            return fail(403, "API Key 无权限访问该模型")
        if "404" in msg or "model not found" in low or "does not exist" in low:
            return fail(404, f"模型不存在：{settings.CHAT_MODEL}")
        if "connect" in low or "timeout" in low or "network" in low:
            return fail(503, "网络连接失败，请检查网络或 base_url")
        return fail(500, f"连接失败：{msg}")
