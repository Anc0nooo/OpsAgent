"""
LLM 客户端封装（模型层核心，多用户架构）

设计：
- LLMClient 按用户配置初始化（api_key / base_url / 模型名 从 user_configs 表读取）
- 全局 llm_client 保留作为兜底（用 .env 配置，用于未登录场景如健康检查）
- get_user_llm(db, user_id) 按用户配置创建/缓存客户端实例
- 所有调用自带【重试 + 降级】策略
"""
import asyncio
import json
import logging
import time
from typing import Any, AsyncIterator, Iterator

import httpx
from openai import AsyncOpenAI, OpenAI
from sqlalchemy.orm import Session

from app.config.settings import settings

logger = logging.getLogger(__name__)


class LLMApiError(Exception):
    """LLM API 调用失败（重试与降级均失败后抛出）"""

    def __init__(self, message: str, error_code: str = "unknown") -> None:
        super().__init__(message)
        self.error_code = error_code


def _classify_error(err: Exception) -> str:
    """根据异常信息归类错误码，供上层结构化提示用户"""
    msg = str(err)
    low = msg.lower()
    if "401" in msg or "invalid api key" in low or "incorrect api key" in low:
        return "invalid_key"
    if "429" in msg or "quota" in low or "rate limit" in low:
        return "quota_exceeded"
    if "403" in msg:
        return "forbidden"
    if "404" in msg or "model not found" in low:
        return "model_not_found"
    if "network" in low or "connect" in low or "timeout" in low or "connection" in low:
        return "network"
    return "unknown"


class LLMClient:
    """模型客户端（按配置初始化，支持多用户隔离）"""

    def __init__(
        self,
        api_key: str = "",
        base_url: str = "",
        chat_model: str = "",
        backup_model: str = "",
        embed_model: str = "",
        rerank_model: str = "",
    ) -> None:
        self._api_key = api_key or settings.DASHSCOPE_API_KEY
        self._base_url = base_url or settings.DASHSCOPE_BASE_URL
        self._chat_model = chat_model or settings.CHAT_MODEL
        self._backup_model = backup_model or settings.BACKUP_CHAT_MODEL
        self._embed_model = embed_model or settings.EMBED_MODEL
        self._rerank_model = rerank_model or settings.RERANK_MODEL
        self._init_clients()

    def _init_clients(self) -> None:
        """创建 OpenAI 兼容客户端"""
        api_key = self._api_key or "EMPTY"
        self._client = OpenAI(
            api_key=api_key,
            base_url=self._base_url,
            timeout=settings.LLM_TIMEOUT,
            max_retries=0,
        )
        self._aclient = AsyncOpenAI(
            api_key=api_key,
            base_url=self._base_url,
            timeout=settings.LLM_TIMEOUT,
            max_retries=0,
        )
        self._embed_client = self._client

    def reinit(self) -> None:
        """热重载"""
        self._init_clients()

    @property
    def configured(self) -> bool:
        """是否已配置有效 API Key"""
        return bool(self._api_key) and self._api_key != "EMPTY"

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------
    def _candidate_models(self, model: str | None, with_fallback: bool = True) -> list[str]:
        primary = model or self._chat_model
        backup = self._backup_model.strip()
        if not with_fallback or not backup or primary == backup:
            return [primary]
        return [primary, backup]

    @staticmethod
    def _retry_delay(attempt: int) -> float:
        return 0.5 * (2 ** attempt)

    # ------------------------------------------------------------------
    # 对话补全：非流式
    # ------------------------------------------------------------------
    def chat(self, messages: list[dict[str, Any]], model: str | None = None,
             temperature: float | None = None, **kwargs: Any) -> str:
        last_err: Exception | None = None
        for m in self._candidate_models(model):
            for attempt in range(settings.LLM_MAX_RETRIES + 1):
                try:
                    resp = self._client.chat.completions.create(
                        model=m, messages=messages,
                        temperature=temperature if temperature is not None else settings.LLM_TEMPERATURE,
                        **kwargs,
                    )
                    return resp.choices[0].message.content or ""
                except Exception as e:  # noqa: BLE001
                    last_err = e
                    if attempt < settings.LLM_MAX_RETRIES:
                        time.sleep(self._retry_delay(attempt))
        raise LLMApiError(f"chat 调用失败: {last_err}", error_code=_classify_error(last_err)) from last_err

    # ------------------------------------------------------------------
    # 对话补全：流式
    # ------------------------------------------------------------------
    def chat_stream(self, messages: list[dict[str, Any]], model: str | None = None,
                    temperature: float | None = None, **kwargs: Any) -> Iterator[str]:
        last_err: Exception | None = None
        for m in self._candidate_models(model):
            for attempt in range(settings.LLM_MAX_RETRIES + 1):
                try:
                    stream = self._client.chat.completions.create(
                        model=m, messages=messages, stream=True,
                        temperature=temperature if temperature is not None else settings.LLM_TEMPERATURE,
                        **kwargs,
                    )
                    for chunk in stream:
                        delta = chunk.choices[0].delta
                        if delta and delta.content:
                            yield delta.content
                    return
                except Exception as e:  # noqa: BLE001
                    last_err = e
                    if attempt < settings.LLM_MAX_RETRIES:
                        time.sleep(self._retry_delay(attempt))
        raise LLMApiError(f"chat_stream 调用失败: {last_err}", error_code=_classify_error(last_err)) from last_err

    # ------------------------------------------------------------------
    # 异步流式
    # ------------------------------------------------------------------
    async def chat_stream_async(self, messages: list[dict[str, Any]], model: str | None = None,
                                temperature: float | None = None, **kwargs: Any) -> AsyncIterator[str]:
        last_err: Exception | None = None
        for m in self._candidate_models(model):
            for attempt in range(settings.LLM_MAX_RETRIES + 1):
                try:
                    stream = await self._aclient.chat.completions.create(
                        model=m, messages=messages, stream=True,
                        temperature=temperature if temperature is not None else settings.LLM_TEMPERATURE,
                        **kwargs,
                    )
                    async for chunk in stream:
                        delta = chunk.choices[0].delta
                        if delta and delta.content:
                            yield delta.content
                    return
                except Exception as e:  # noqa: BLE001
                    last_err = e
                    if attempt < settings.LLM_MAX_RETRIES:
                        await asyncio.sleep(self._retry_delay(attempt))
        raise LLMApiError(f"chat_stream_async 调用失败: {last_err}", error_code=_classify_error(last_err)) from last_err

    # ------------------------------------------------------------------
    # 结构化输出
    # ------------------------------------------------------------------
    def structured_output(self, messages: list[dict[str, Any]], model: str | None = None,
                         **kwargs: Any) -> dict[str, Any]:
        merged = list(messages)
        merged.insert(0, {"role": "system",
                           "content": "你必须只输出一个合法的 JSON 对象，不要输出任何解释、markdown 代码块或其他文字。"})
        last_err: Exception | None = None
        for m in self._candidate_models(model):
            for attempt in range(settings.LLM_MAX_RETRIES + 1):
                try:
                    resp = self._client.chat.completions.create(
                        model=m, messages=merged, temperature=0.1,
                        response_format={"type": "json_object"}, **kwargs,
                    )
                    return json.loads(resp.choices[0].message.content or "")
                except (json.JSONDecodeError, Exception) as e:  # noqa: BLE001
                    last_err = e
                    if attempt < settings.LLM_MAX_RETRIES:
                        time.sleep(self._retry_delay(attempt))
        raise LLMApiError(f"structured_output 调用失败: {last_err}", error_code=_classify_error(last_err)) from last_err

    # ------------------------------------------------------------------
    # 向量化
    # ------------------------------------------------------------------
    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        last_err: Exception | None = None
        for attempt in range(settings.LLM_MAX_RETRIES + 1):
            try:
                resp = self._embed_client.embeddings.create(
                    model=self._embed_model, input=texts,
                )
                data = sorted(resp.data, key=lambda d: d.index)
                return [d.embedding for d in data]
            except Exception as e:  # noqa: BLE001
                last_err = e
                if attempt < settings.LLM_MAX_RETRIES:
                    time.sleep(self._retry_delay(attempt))
        raise LLMApiError(f"embed 调用失败: {last_err}", error_code=_classify_error(last_err)) from last_err

    # ------------------------------------------------------------------
    # 重排
    # ------------------------------------------------------------------
    def rerank(self, query: str, documents: list[str], top_n: int = 5) -> list[dict[str, Any]]:
        if not documents:
            return []
        headers = {"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"}
        payload = {"model": self._rerank_model,
                   "input": {"query": query, "documents": documents},
                   "parameters": {"return_documents": True, "top_n": top_n}}
        last_err: Exception | None = None
        for attempt in range(settings.LLM_MAX_RETRIES + 1):
            try:
                resp = httpx.post(settings.RERANK_API_URL, json=payload, headers=headers,
                                  timeout=settings.LLM_TIMEOUT)
                resp.raise_for_status()
                results = resp.json()["output"]["results"]
                for item in results:
                    if "score" not in item and "relevance_score" in item:
                        item["score"] = item["relevance_score"]
                return results
            except Exception as e:  # noqa: BLE001
                last_err = e
                if attempt < settings.LLM_MAX_RETRIES:
                    time.sleep(self._retry_delay(attempt))
        raise LLMApiError(f"rerank 调用失败: {last_err}", error_code=_classify_error(last_err)) from last_err


# ----------------------------------------------------------------------
# 用户级 LLM 客户端工厂（按 user_id 缓存）
# ----------------------------------------------------------------------
_user_clients: dict[int, LLMClient] = {}


def get_user_llm(db: Session, user_id: int) -> LLMClient:
    """
    按 user_id 获取 LLM 客户端（从 user_configs 表读取配置，缓存实例）。
    用户未配置 API Key 时回退全局 .env 配置。
    """
    if user_id in _user_clients:
        return _user_clients[user_id]

    from app.db.models import UserConfig
    cfg = db.query(UserConfig).filter(
        UserConfig.user_id == user_id, UserConfig.provider == "dashscope"
    ).first()

    client = LLMClient(
        api_key=cfg.api_key if cfg and cfg.api_key else settings.DASHSCOPE_API_KEY,
        base_url=cfg.base_url if cfg else settings.DASHSCOPE_BASE_URL,
        chat_model=cfg.chat_model if cfg else settings.CHAT_MODEL,
        embed_model=cfg.embed_model if cfg else settings.EMBED_MODEL,
        rerank_model=cfg.rerank_model if cfg else settings.RERANK_MODEL,
    )
    _user_clients[user_id] = client
    return client


def invalidate_user_llm(user_id: int) -> None:
    """用户配置变更后清除缓存，下次请求重新创建"""
    _user_clients.pop(user_id, None)


# 全局兜底客户端（用 .env 配置，用于未登录场景）
llm_client = LLMClient()
