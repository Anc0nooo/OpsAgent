"""
Agent 规划器 - 会话存储（MySQL + SQLAlchemy，多用户隔离）
表：
- conversations：会话（状态机当前状态、挂起查询、轮次，按 user_id 隔离）
- messages：消息历史（user/assistant，供上下文裁剪与回溯，通过 conversation_id 间接隔离）
"""
import json
import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import Conversation, Message


def init_store() -> None:
    """建表由 ORM create_all 统一处理（保留函数兼容 main.py 调用）"""
    pass


# ----------------------------------------------------------------------
# conversations
# ----------------------------------------------------------------------
def create_session(db: Session, user_id: int, title: str = "新对话") -> dict[str, Any] | None:
    """新建会话（绑定当前用户）"""
    sid = uuid.uuid4().hex[:12]
    conv = Conversation(id=sid, user_id=user_id, title=title)
    db.add(conv)
    db.flush()
    return get_session(db, sid, user_id)


def get_session(db: Session, session_id: str, user_id: int | None = None) -> dict[str, Any] | None:
    """查询会话；传 user_id 时校验归属（防越权）"""
    q = db.query(Conversation).filter(Conversation.id == session_id)
    if user_id is not None:
        q = q.filter(Conversation.user_id == user_id)
    c = q.first()
    if c is None:
        return None
    return _conv_to_dict(c)


def update_session(
    db: Session,
    session_id: str,
    user_id: int,
    state: str | None = None,
    pending_query: dict | None = None,
    clear_pending: bool = False,
    query_round: int | None = None,
    title: str | None = None,
) -> bool:
    """更新会话状态（状态机流转 / 挂起 / 轮次 / 标题）；校验 user_id"""
    c = db.query(Conversation).filter(
        Conversation.id == session_id, Conversation.user_id == user_id
    ).first()
    if c is None:
        return False
    if state is not None:
        c.state = state
    if clear_pending:
        c.pending_query = None
    elif pending_query is not None:
        c.pending_query = json.dumps(pending_query, ensure_ascii=False)
    if query_round is not None:
        c.query_round = query_round
    if title is not None:
        c.title = title
    db.flush()
    return True


def list_sessions(db: Session, user_id: int, limit: int = 100) -> list[dict[str, Any]]:
    """当前用户的会话列表（置顶优先，再按更新时间倒序）"""
    rows = db.query(Conversation).filter(
        Conversation.user_id == user_id
    ).order_by(
        Conversation.is_pinned.desc(), Conversation.updated_at.desc()
    ).limit(limit).all()
    return [_conv_to_dict(c) for c in rows]


def delete_session(db: Session, session_id: str, user_id: int) -> bool:
    """删除会话及其消息（校验 user_id）"""
    c = db.query(Conversation).filter(
        Conversation.id == session_id, Conversation.user_id == user_id
    ).first()
    if c is None:
        return False
    db.query(Message).filter(Message.conversation_id == session_id).delete()
    db.delete(c)
    db.flush()
    return True


def set_pinned(db: Session, session_id: str, user_id: int, pinned: bool) -> bool:
    """置顶 / 取消置顶（不更新 updated_at）"""
    c = db.query(Conversation).filter(
        Conversation.id == session_id, Conversation.user_id == user_id
    ).first()
    if c is None:
        return False
    c.is_pinned = 1 if pinned else 0
    db.flush()
    return True


def delete_sessions(db: Session, session_ids: list[str], user_id: int) -> int:
    """批量删除会话及其消息（仅当前用户的，返回实际删除条数）"""
    convs = db.query(Conversation).filter(
        Conversation.id.in_(session_ids), Conversation.user_id == user_id
    ).all()
    count = len(convs)
    for c in convs:
        db.query(Message).filter(Message.conversation_id == c.id).delete()
        db.delete(c)
    db.flush()
    return count


# ----------------------------------------------------------------------
# messages
# ----------------------------------------------------------------------
def add_message(db: Session, session_id: str, role: str, content: str) -> None:
    """追加消息"""
    msg = Message(conversation_id=session_id, role=role, content=content)
    db.add(msg)
    db.flush()


def get_history(db: Session, session_id: str, limit_turns: int = 8) -> list[dict[str, str]]:
    """
    读取最近 limit_turns 条消息（时间正序），供上下文裁剪。
    返回 [{"role":..., "content":...}, ...]
    """
    rows = db.query(Message).filter(
        Message.conversation_id == session_id
    ).order_by(Message.id.desc()).limit(limit_turns).all()
    return [{"role": r.role, "content": r.content} for r in reversed(rows)]


def _conv_to_dict(c: Conversation) -> dict[str, Any]:
    """ORM 对象 → 字典"""
    return {
        "id": c.id, "user_id": c.user_id, "title": c.title, "state": c.state,
        "pending_query": c.pending_query, "query_round": c.query_round,
        "is_pinned": bool(c.is_pinned),
        "created_at": str(c.created_at) if c.created_at else "",
        "updated_at": str(c.updated_at) if c.updated_at else "",
    }
