"""
结果输出 - API（多用户：需登录，按 user_id 隔离会话与知识库）
- GET  /api/output/cards/{session_id}  当前会话四段总结卡片 + 查询卡片（前端渲染）
- POST /api/output/export/markdown     导出方案 .md（最后一条 AI 消息）
- POST /api/output/export/sql          导出挂起查询 .sql
- POST /api/output/export/csv          导出查询结果 .csv（前端传列名与数据行）
- POST /api/output/save_knowledge      把会话方案存为知识库文档（当前用户）

文件下载统一返回文本 + Content-Disposition（文件名含时间戳）。
"""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.agent import store
from app.auth.dependencies import get_current_user
from app.db.engine import get_db
from app.db.models import User
from app.output.exporter import export_csv, export_markdown, export_sql
from app.output.formatter import build_query_card, build_summary_sections
from app.models.common import fail, ok
from app.rag.service import ALLOWED_TYPES, knowledge_service

router = APIRouter(prefix="/api/output", tags=["结果输出"])


def _filename(ext: str) -> str:
    """生成带时间戳的下载文件名"""
    return f"opsagent_{datetime.now().strftime('%Y%m%d_%H%M%S')}.{ext}"


def _text_response(text: str, ext: str, media_type: str) -> Response:
    """统一文本下载响应（UTF-8，Content-Disposition）"""
    return Response(
        content=text,
        media_type=f"{media_type}; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{_filename(ext)}"'},
    )


def _get_last_assistant(db: Session, session_id: str, user_id: int) -> dict:
    """取会话最后一条 assistant 消息（不存在/无权访问则 404）"""
    session = store.get_session(db, session_id, user_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"会话不存在或无权访问: {session_id}")
    history = store.get_history(db, session_id, limit_turns=200)
    msgs = [m for m in history if m["role"] == "assistant"]
    if not msgs:
        raise HTTPException(status_code=404, detail="会话暂无方案内容")
    return msgs[-1]


class CsvIn(BaseModel):
    """CSV 导出请求"""
    columns: list[str]
    rows: list[list]


@router.get("/cards/{session_id}")
def cards(session_id: str, db: Session = Depends(get_db),
          user: User = Depends(get_current_user)) -> dict:
    """会话输出卡片：四段总结（最后一条方案）+ 查询卡片（挂起态）"""
    session = store.get_session(db, session_id, user.id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"会话不存在或无权访问: {session_id}")
    history = store.get_history(db, session_id, limit_turns=200)
    ai_msgs = [m["content"] for m in history if m["role"] == "assistant"]
    sections = build_summary_sections(ai_msgs[-1]) if ai_msgs else None
    pending = None
    if session["state"] == "QUERY_PENDING" and session["pending_query"]:
        import json
        pending = build_query_card(json.loads(session["pending_query"]))
    return ok({"sections": sections, "query_card": pending, "title": session["title"]})


class SessionIn(BaseModel):
    """会话参数请求体（导出接口统一从 JSON body 取会话 ID）"""
    session_id: str


@router.post("/export/markdown")
def export_md(body: SessionIn, db: Session = Depends(get_db),
              user: User = Depends(get_current_user)) -> Response:
    """导出最后一条方案为 .md"""
    msg = _get_last_assistant(db, body.session_id, user.id)
    session = store.get_session(db, body.session_id, user.id) or {}
    md = export_markdown(msg["content"], meta={"title": session.get("title"), "session_id": body.session_id})
    return _text_response(md, "md", "text/markdown")


@router.post("/export/sql")
def export_sql_file(body: SessionIn, db: Session = Depends(get_db),
                    user: User = Depends(get_current_user)) -> Response:
    """导出当前挂起查询为 .sql"""
    session = store.get_session(db, body.session_id, user.id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"会话不存在或无权访问: {body.session_id}")
    if session["state"] != "QUERY_PENDING" or not session["pending_query"]:
        raise HTTPException(status_code=400, detail="当前会话没有挂起的查询")
    import json
    return _text_response(export_sql(json.loads(session["pending_query"])), "sql", "text/plain")


@router.post("/export/csv")
def export_csv_file(body: CsvIn) -> Response:
    """导出查询结果为 .csv（UTF-8 BOM，Excel 兼容）"""
    return _text_response(export_csv(body.columns, body.rows), "csv", "text/csv")


class SaveKnowledgeIn(BaseModel):
    """存为知识请求（会话方案一键入库，存入当前用户知识库）"""
    session_id: str
    doc_type: str = "bug"  # 默认归为 bug 修复类，可选 ALLOWED_TYPES 中的类型


@router.post("/save_knowledge")
def save_knowledge(body: SaveKnowledgeIn, db: Session = Depends(get_db),
                   user: User = Depends(get_current_user)) -> dict:
    """把会话最后一条方案存为知识库文档（存入当前用户知识库）"""
    if body.doc_type not in ALLOWED_TYPES:
        return fail(400, f"不支持的文档类型: {body.doc_type}，可选: {ALLOWED_TYPES}")
    msg = _get_last_assistant(db, body.session_id, user.id)
    session = store.get_session(db, body.session_id, user.id) or {}
    title = (session.get("title") or "运维排障方案").strip()[:100]
    doc_title = f"[排障案例] {title}"
    # 文档正文复用导出格式（元信息头 + 方案全文），保证知识库内容结构一致
    doc_text = export_markdown(
        msg["content"],
        meta={"title": doc_title, "session_id": body.session_id},
    )
    doc_id = knowledge_service.add_document(
        db, user.id,
        title=doc_title,
        text=doc_text,
        doc_type=body.doc_type,
        source=f"session:{body.session_id}",
    )
    db.commit()
    return ok({"doc_id": doc_id, "title": doc_title, "message": "已存入知识库"})
