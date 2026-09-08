"""
正式对话接口（阶段 4，多用户：需登录，按 user_id 隔离会话与知识库）
- POST /api/chat          非流式对话（复用规划器 handle_message）
- POST /api/chat/stream   SSE 流式对话（status → delta → done 事件）

SSE 事件格式（data: JSON\n\n）：
- {"type":"status","stage":"intent|rag|analyze|plan","text":"..."}  处理阶段提示
- {"type":"delta","content":"..."}                                  正文增量（打字机）
- {"type":"done","session_id","state","need_query","query_round","pending_query"?}  结束（挂起时带 SQL 卡片数据）
- {"type":"error","message":"..."}                                  异常
"""
import asyncio
import json

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.agent import store
from app.agent.planner import planner
from app.auth.dependencies import get_current_user
from app.auth.log import log_operation
from app.db.engine import get_db
from app.db.models import User
from app.models.common import ok

router = APIRouter(prefix="/api/chat", tags=["对话"])


class ChatIn(BaseModel):
    """对话请求"""
    session_id: str | None = None  # 不传则新建会话
    text: str = Field(..., min_length=1)


@router.post("/chat")
def chat(body: ChatIn, request: Request, db: Session = Depends(get_db),
         user: User = Depends(get_current_user)) -> dict:
    """非流式对话（一次性返回完整结果）"""
    log_operation(db, user.id, "chat", f"消息: {body.text[:80]}",
                  request, username=user.username)
    return ok(planner.handle_message(db, user, body.session_id, body.text))


@router.post("/stream")
async def chat_stream(body: ChatIn, request: Request, db: Session = Depends(get_db),
                     user: User = Depends(get_current_user)) -> StreamingResponse:
    """SSE 流式对话：逐事件推送处理状态与正文增量"""
    log_operation(db, user.id, "chat", f"流式消息: {body.text[:80]}",
                  request, username=user.username)

    async def event_source():
        try:
            async for ev in planner.handle_message_stream(db, user, body.session_id, body.text):
                yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
                # 挂起/闲聊是整段 delta，主动让出事件循环，避免缓冲不刷新
                await asyncio.sleep(0)
            yield "data: [DONE]\n\n"
        except Exception as e:  # noqa: BLE001
            err = {"type": "error", "message": f"处理失败: {e}"}
            yield f"data: {json.dumps(err, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # 关闭代理缓冲
        },
    )


# 会话管理（供前端加载历史/新会话；详细增删查在 /api/agent/sessions）
@router.get("/sessions")
def sessions(db: Session = Depends(get_db),
             user: User = Depends(get_current_user)) -> dict:
    """会话列表（当前用户）"""
    return ok(store.list_sessions(db, user.id))
