"""
Agent 规划器 - 临时 API（阶段 2 提供非流式内部调用链，供状态机测试）
阶段 4 将实现正式对话接口（POST /api/chat + GET /api/chat/stream SSE 流式）。

接口（均需登录，按 user_id 隔离）：
- POST /api/agent/chat    发送消息（非流式，返回状态机结果）
- GET  /api/agent/sessions            会话列表（当前用户）
- GET  /api/agent/sessions/{id}       会话详情（含消息历史）
- DELETE /api/agent/sessions/{id}     删除会话
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.agent import store
from app.agent.planner import planner
from app.auth.dependencies import get_current_user
from app.db.engine import get_db
from app.db.models import User
from app.models.common import ok

router = APIRouter(prefix="/api/agent", tags=["Agent规划器"])


class ChatIn(BaseModel):
    """对话请求"""
    session_id: str | None = None  # 不传则新建会话
    text: str = Field(..., min_length=1)


class PinIn(BaseModel):
    """置顶请求"""
    pinned: bool


class BatchDeleteIn(BaseModel):
    """批量删除请求"""
    ids: list[str]


@router.post("/chat")
def chat(body: ChatIn, db: Session = Depends(get_db),
         user: User = Depends(get_current_user)) -> dict:
    """发送一条消息（非流式），返回助手回复与状态机状态"""
    reply = planner.handle_message(db, user, body.session_id, body.text)
    return ok(reply)


@router.get("/sessions")
def sessions(db: Session = Depends(get_db),
             user: User = Depends(get_current_user)) -> dict:
    """会话列表（当前用户）"""
    return ok(store.list_sessions(db, user.id))


@router.get("/sessions/{session_id}")
def session_detail(session_id: str, db: Session = Depends(get_db),
                   user: User = Depends(get_current_user)) -> dict:
    """会话详情 + 消息历史（校验归属）"""
    session = store.get_session(db, session_id, user.id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"会话不存在或无权访问: {session_id}")
    session["messages"] = store.get_history(db, session_id, limit_turns=200)
    return ok(session)


@router.patch("/sessions/{session_id}/pin")
def pin_session(session_id: str, body: PinIn, db: Session = Depends(get_db),
                user: User = Depends(get_current_user)) -> dict:
    """置顶 / 取消置顶会话（校验归属）"""
    if not store.set_pinned(db, session_id, user.id, body.pinned):
        raise HTTPException(status_code=404, detail=f"会话不存在或无权访问: {session_id}")
    db.commit()
    return ok({"message": "已置顶" if body.pinned else "已取消置顶"})


@router.delete("/sessions/batch")
def batch_delete_sessions(body: BatchDeleteIn, db: Session = Depends(get_db),
                          user: User = Depends(get_current_user)) -> dict:
    """批量删除会话（仅当前用户的；注意：必须注册在 /sessions/{session_id} 之前，否则 batch 会被当作 id）"""
    n = store.delete_sessions(db, body.ids, user.id)
    db.commit()
    return ok({"deleted": n, "message": f"已删除 {n} 个会话"})


@router.delete("/sessions/{session_id}")
def remove_session(session_id: str, db: Session = Depends(get_db),
                   user: User = Depends(get_current_user)) -> dict:
    """删除会话（校验归属）"""
    if not store.delete_session(db, session_id, user.id):
        raise HTTPException(status_code=404, detail=f"会话不存在或无权访问: {session_id}")
    db.commit()
    return ok({"message": "删除成功"})
