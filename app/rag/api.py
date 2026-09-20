"""
RAG 知识库 - API 路由（多用户：所有接口需登录，按 user_id 隔离数据）
接口清单：
- POST /api/knowledge/upload        上传文档（txt/md/pdf/docx，支持增量追加）
- POST /api/knowledge/text          粘贴文本入库（表结构/SQL脚本/操作文档等）
- POST /api/knowledge/table_schema  批量导入表结构（DESC 输出或 DDL）
- GET  /api/knowledge/docs          文档列表（当前用户）
- DELETE /api/knowledge/docs/{id}   删除文档（校验归属）
- GET  /api/knowledge/settings      每用户切分参数（chunk_size/overlap）
- PUT  /api/knowledge/settings      保存切分参数（改后需重建索引）
- GET  /api/knowledge/image/{id}    知识库原图回显（支持 ?token= 鉴权）
- POST /api/knowledge/search        混合检索（BM25+向量+重排，返回 top-5 与来源）
- GET  /api/knowledge/progress      查询入库/重建进度（前端轮询）
- POST /api/knowledge/test          检索效果测试（批量问题输出命中与 top-5）
"""
import asyncio
import logging

import chromadb
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.security import HTTPAuthorizationCredentials
from fastapi.responses import FileResponse
import pymupdf  # PyMuPDF
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user, security
from app.auth.jwt import decode_token
from app.auth.log import log_operation
from app.config.settings import settings
from app.db.engine import get_db
from app.db.models import User
from app.models.common import fail, ok
from app.rag import db as rag_db
from app.rag.service import ALLOWED_TYPES, knowledge_service, set_progress, get_progress

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/knowledge", tags=["RAG知识库"])


# ----------------------------------------------------------------------
# 请求体模型
# ----------------------------------------------------------------------
class TextIn(BaseModel):
    """文本入库请求"""
    title: str = Field(..., min_length=1, max_length=200)
    text: str = Field(..., min_length=1)
    doc_type: str = "guide"
    append_doc_id: int | None = None  # 提供则增量追加到该文档


class TableSchemaIn(BaseModel):
    """表结构导入请求（DESC 输出或 DDL，DDL 可含多表）"""
    content: str = Field(..., min_length=1)
    table_name: str = ""  # DESC 输出必填；DDL 可不填


class ChatParseIn(BaseModel):
    """聊天记录解析/导入请求"""
    source: str = "auto"            # wechat / feishu / auto
    text: str = Field(..., min_length=1)
    split_mode: str = "merge"      # merge（合并）/ day（按天）/ person（按人）
    filter_system: bool = True     # 过滤系统消息
    filter_media: bool = True      # 过滤 [表情][图片] 等占位符


class SearchIn(BaseModel):
    """检索请求"""
    query: str = Field(..., min_length=1)
    doc_type: str | None = None
    top_k: int = 5


class TestIn(BaseModel):
    """检索测试请求"""
    queries: list[str] = Field(..., min_length=1)


# ----------------------------------------------------------------------
# 文件解析工具
# ----------------------------------------------------------------------
def read_file_text(filename: str, data: bytes) -> str:
    """按扩展名解析上传文件为纯文本（docx 由 read_docx_text 单独处理）"""
    lower = filename.lower()
    if lower.endswith((".txt", ".md", ".sql")):
        return data.decode("utf-8", errors="ignore")
    if lower.endswith(".pdf"):
        with pymupdf.open(stream=data, filetype="pdf") as pdf:
            return "\n".join(page.get_text() for page in pdf)
    raise ValueError(f"不支持的文件类型: {filename}（支持 txt/md/sql/pdf/docx）")


def read_docx_text(data: bytes) -> str:
    """解析 docx 为纯文本"""
    import io

    from docx import Document as _Docx

    doc = _Docx(io.BytesIO(data))
    return "\n".join(p.text for p in doc.paragraphs if p.text.strip())


# ----------------------------------------------------------------------
# 接口
# ----------------------------------------------------------------------
@router.post("/upload")
async def upload_doc(
    request: Request,
    file: UploadFile = File(...),
    doc_type: str = Form("guide"),
    title: str = Form(""),
    append_doc_id: int | None = Form(None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    """上传文档入库（txt/md/sql/pdf/docx），支持增量追加。

    PDF/Word 走结构化解析：表格转 Markdown、内嵌图片提取落盘 + qwen-vl OCR、
    页眉页脚/页码写入块元数据；txt/md/sql 走纯文本解析。
    """
    if doc_type not in ALLOWED_TYPES:
        return fail(400, f"不支持的文档类型: {doc_type}，可选: {ALLOWED_TYPES}")
    data = await file.read()
    uid = user.id
    # 进度回调（供 GET /progress 轮询）
    def _progress(phase, done, total):
        label = "向量化" if phase == "embedding" else "解析分块"
        set_progress(uid, status="processing", phase=phase,
                     done=done, total=total,
                     detail=f"{label} {done}/{total}")
    set_progress(uid, status="processing", phase="parsing",
                 done=0, total=0, detail="解析文档中…")
    try:
        loop = asyncio.get_event_loop()
        doc_id: int | None = await loop.run_in_executor(
            None,
            lambda: knowledge_service.ingest_file(
                db, uid,
                title=title or file.filename or "未命名文档",
                filename=file.filename or "",
                data=data,
                doc_type=doc_type,
                append_doc_id=append_doc_id,
                progress_cb=_progress,
            ),
        )
        db.commit()
        log_operation(db, uid, "upload_doc",
                      f"上传文档《{title or file.filename}》(id={doc_id})",
                      request, username=user.username)
        set_progress(uid, status="done", detail="入库成功")
        return ok({"doc_id": doc_id, "message": "入库成功"})
    except ValueError as e:
        set_progress(uid, status="error", detail=str(e))
        return fail(400, str(e))
    except chromadb.errors.InternalError:
        # Chroma HNSW 索引损坏：本次写入已回滚，自动修复后提示重新上传
        logger.exception("上传触发 Chroma 索引损坏")
        try:
            knowledge_service.repair_index(db, uid)
            db.commit()
        except Exception as repair_err:  # noqa: BLE001
            logger.exception("Chroma 自动修复失败")
            return fail(
                500,
                f"知识库索引损坏且自动修复失败（{repair_err}），"
                "请停止后端后将 data/chroma 目录改名留底再重启",
            )
        return fail(500, "知识库索引损坏，已自动修复，请重新上传")
    except Exception as e:  # noqa: BLE001
        logger.exception("上传入库失败")
        set_progress(uid, status="error", detail=f"入库失败: {e}")
        return fail(500, f"入库失败: {e}")


@router.post("/text")
def add_text(body: TextIn, db: Session = Depends(get_db),
             user: User = Depends(get_current_user)) -> dict:
    """粘贴文本直接入库"""
    try:
        doc_id = knowledge_service.add_document(
            db, user.id,
            title=body.title,
            text=body.text,
            doc_type=body.doc_type,
            source="text_paste",
            doc_id=body.append_doc_id,
            append=body.append_doc_id is not None,
        )
        db.commit()
        return ok({"doc_id": doc_id, "message": "入库成功"})
    except Exception as e:  # noqa: BLE001
        return fail(400, f"入库失败: {e}")


@router.post("/table_schema")
def import_table_schema(body: TableSchemaIn, db: Session = Depends(get_db),
                        user: User = Depends(get_current_user)) -> dict:
    """批量导入表结构（DESC 输出或 CREATE TABLE DDL）"""
    try:
        doc_ids = knowledge_service.import_table_schema(db, user.id, body.content, body.table_name)
        db.commit()
        return ok({"doc_ids": doc_ids, "count": len(doc_ids), "message": f"成功导入 {len(doc_ids)} 张表结构"})
    except ValueError as e:
        return fail(400, str(e))
    except Exception as e:  # noqa: BLE001
        logger.exception("表结构导入失败")
        return fail(500, f"导入失败: {e}")


@router.post("/chat/parse")
def chat_parse(body: ChatParseIn, user: User = Depends(get_current_user)) -> dict:
    """解析聊天记录预览（不入库），返回消息数/参与人/时间范围/前10条。

    识别失败不再报 400：返回 recognized=false + 字数，由前端提示"按纯文本导入"，用户可选继续。
    """
    from app.rag.chat_parser import build_preview, filter_messages, parse_text
    try:
        msgs, fmt = parse_text(body.text, body.source)
        if not msgs:
            # 降级：未识别为聊天记录格式 → 提示按纯文本文档导入
            return ok({
                "recognized": False,
                "count": 0,
                "participants": [],
                "time_range": "",
                "preview": [],
                "format": "plain",
                "split_mode": body.split_mode,
                "text_length": len(body.text or ""),
                "message": "未识别为聊天记录格式，将作为纯文本文档导入",
            })
        msgs = filter_messages(msgs, body.filter_system, body.filter_media)
        preview = build_preview(msgs)
        preview["format"] = fmt
        preview["split_mode"] = body.split_mode
        preview["recognized"] = True
        return ok(preview)
    except Exception as e:  # noqa: BLE001
        logger.exception("聊天记录解析失败")
        return fail(500, f"解析失败: {e}")


@router.post("/chat/import")
def chat_import(body: ChatParseIn, db: Session = Depends(get_db),
                user: User = Depends(get_current_user)) -> dict:
    """聊天记录导入知识库（类型固定 other，复用文本入库流程：切块→向量化→存 Chroma）。

    识别失败时降级：整段文本作为 1 个纯文本文档入库（doc_type=other），不报错卡死。
    """
    from app.rag.chat_parser import filter_messages, parse_text, split_to_docs
    try:
        msgs, fmt = parse_text(body.text, body.source)
        if not msgs:
            # 降级：纯文本文档整段入库
            text = (body.text or "").strip()
            if not text:
                return fail(400, "导入内容为空")
            from datetime import datetime as _dt
            title = f"纯文本导入-{_dt.now().strftime('%Y%m%d-%H%M')}"
            doc_id = knowledge_service.add_document(
                db, user.id, title=title, text=text, doc_type="other", source="chat_import:plain",
            )
            db.commit()
            return ok({
                "doc_ids": [doc_id],
                "count": 1,
                "titles": [title],
                "fallback": True,
                "message": "未识别为聊天记录格式，已按纯文本文档导入（1 个）",
            })
        msgs = filter_messages(msgs, body.filter_system, body.filter_media)
        docs = split_to_docs(msgs, body.split_mode)
        if not docs:
            return fail(400, "过滤后无可导入内容")
        doc_ids = []
        for d in docs:
            did = knowledge_service.add_document(
                db, user.id,
                title=d.title,
                text=d.text,
                doc_type="other",
                source=f"chat_import:{fmt}",
            )
            doc_ids.append(did)
        db.commit()
        return ok({
            "doc_ids": doc_ids,
            "count": len(doc_ids),
            "titles": [d.title for d in docs],
            "message": f"成功导入 {len(doc_ids)} 个文档",
        })
    except Exception as e:  # noqa: BLE001
        logger.exception("聊天记录导入失败")
        return fail(500, f"导入失败: {e}")


@router.get("/docs")
def get_docs(db: Session = Depends(get_db),
             user: User = Depends(get_current_user)) -> dict:
    """文档列表（当前用户）"""
    return ok(knowledge_service.list_docs(db, user.id))


@router.delete("/docs/{doc_id}")
def remove_doc(doc_id: int, request: Request, db: Session = Depends(get_db),
               user: User = Depends(get_current_user)) -> dict:
    """删除文档（同步清理向量与索引；校验归属）"""
    deleted = knowledge_service.delete_document(db, user.id, doc_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"文档不存在或无权访问: {doc_id}")
    db.commit()
    log_operation(db, user.id, "delete_doc", f"删除文档 id={doc_id}",
                  request, username=user.username)
    return ok({"message": "删除成功"})


class DocUpdateIn(BaseModel):
    """文档编辑请求（重新切分入库，保留 doc_id）"""
    title: str = Field(..., min_length=1, max_length=200)
    text: str = Field(..., min_length=1)
    doc_type: str = "guide"


@router.get("/docs/{doc_id}")
def get_doc_detail(doc_id: int, db: Session = Depends(get_db),
                   user: User = Depends(get_current_user)) -> dict:
    """文档详情（含全文，供在线查看/编辑）"""
    doc = knowledge_service.get_doc_full(db, doc_id, user.id)
    if doc is None:
        raise HTTPException(status_code=404, detail=f"文档不存在或无权访问: {doc_id}")
    return ok(doc)


@router.put("/docs/{doc_id}")
def update_doc(doc_id: int, body: DocUpdateIn, db: Session = Depends(get_db),
               user: User = Depends(get_current_user)) -> dict:
    """编辑文档（保留 doc_id，重新切分入库，向量与索引同步重建）"""
    if body.doc_type not in ALLOWED_TYPES:
        return fail(400, f"不支持的文档类型: {body.doc_type}，可选: {ALLOWED_TYPES}")
    try:
        ok_flag = knowledge_service.update_document(db, user.id, doc_id, body.title, body.text, body.doc_type)
        if not ok_flag:
            raise HTTPException(status_code=404, detail=f"文档不存在或无权访问: {doc_id}")
        db.commit()
        return ok({"message": "保存成功"})
    except ValueError as e:
        return fail(400, str(e))
    except Exception as e:  # noqa: BLE001
        logger.exception("文档更新失败")
        return fail(500, f"保存失败: {e}")


class DocTypeIn(BaseModel):
    """文档类型切换请求（轻量，不重新切分）"""
    doc_type: str


@router.patch("/docs/{doc_id}/type")
def change_doc_type(doc_id: int, body: DocTypeIn, db: Session = Depends(get_db),
                    user: User = Depends(get_current_user)) -> dict:
    """轻量切换文档类型（仅更新 doc/chunk 表 + 向量 metadata，不重新切分/嵌入）"""
    if body.doc_type not in ALLOWED_TYPES:
        return fail(400, f"不支持的文档类型: {body.doc_type}，可选: {ALLOWED_TYPES}")
    try:
        ok_flag = knowledge_service.change_doc_type(db, user.id, doc_id, body.doc_type)
        if not ok_flag:
            raise HTTPException(status_code=404, detail=f"文档不存在或无权访问: {doc_id}")
        db.commit()
        return ok({"message": "类型已更新"})
    except ValueError as e:
        return fail(400, str(e))
    except Exception as e:  # noqa: BLE001
        logger.exception("文档类型切换失败")
        return fail(500, f"切换失败: {e}")


# ----------------------------------------------------------------------
# 切分参数（每用户可调，仅模式B：guide/bug/other 生效）
# ----------------------------------------------------------------------
class KbSettingsIn(BaseModel):
    """切分参数设置（范围由后端强校验）"""
    chunk_size: int = Field(..., ge=300, le=1000)
    chunk_overlap: int = Field(..., ge=0, le=200)


@router.get("/settings")
def get_settings(db: Session = Depends(get_db),
                 user: User = Depends(get_current_user)) -> dict:
    """读取当前用户的切分参数（未设置过返回默认 500/50）"""
    return ok(rag_db.get_kb_settings(db, user.id))


@router.put("/settings")
def save_settings(body: KbSettingsIn, request: Request, db: Session = Depends(get_db),
                  user: User = Depends(get_current_user)) -> dict:
    """保存当前用户的切分参数（修改后需重建索引才对存量文档生效）"""
    saved = rag_db.upsert_kb_settings(db, user.id, body.chunk_size, body.chunk_overlap)
    db.commit()
    log_operation(db, user.id, "kb_settings_save",
                  f"切分参数 chunk_size={saved['chunk_size']} overlap={saved['chunk_overlap']}",
                  request, username=user.username)
    return ok({**saved, "message": "已保存，修改后需重建索引生效"})


# ----------------------------------------------------------------------
# 知识库原图回显（<img> 标签无法带 Authorization 头，允许 ?token= 鉴权）
# ----------------------------------------------------------------------
def get_image_user(
    token: str = Query("", description="img 标签场景的 query token"),
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
    db: Session = Depends(get_db),
) -> User:
    """图片接口鉴权：Bearer 头优先，回退 query 参数 token"""
    raw = credentials.credentials if credentials and credentials.credentials else token
    if not raw:
        raise HTTPException(status_code=401, detail="未登录或登录已过期，请重新登录")
    payload = decode_token(raw)
    user_id = (payload or {}).get("user_id")
    user = db.query(User).filter(User.id == user_id).first() if user_id is not None else None
    if user is None:
        raise HTTPException(status_code=401, detail="Token 无效或已过期，请重新登录")
    if user.status != 1:
        raise HTTPException(status_code=403, detail="账号已被禁用")
    return user


@router.get("/image/{image_id}")
def get_kb_image(image_id: int, db: Session = Depends(get_db),
                 user: User = Depends(get_image_user)) -> FileResponse:
    """回显知识库原图（校验图片归属当前用户）"""
    im = rag_db.get_image(db, image_id)
    if im is None or im["user_id"] != user.id:
        raise HTTPException(status_code=404, detail="图片不存在或无权访问")
    path = settings.DATA_DIR / im["path"]
    if not path.is_file():
        raise HTTPException(status_code=404, detail="图片文件不存在或已被清理")
    return FileResponse(path)


@router.post("/search")
def search(body: SearchIn, request: Request, db: Session = Depends(get_db),
           user: User = Depends(get_current_user)) -> dict:
    """混合检索：BM25 + 向量 + gte-rerank，返回 top_k（默认5，含页码/页眉/原图）"""
    try:
        results = knowledge_service.retrieve(db, user.id, body.query,
                                             doc_type=body.doc_type, top_k=body.top_k)
        log_operation(db, user.id, "retrieve",
                      f"检索「{body.query[:50]}」→ {len(results)} 条",
                      request, username=user.username)
        return ok(results)
    except Exception as e:  # noqa: BLE001
        logger.exception("检索失败")
        return fail(500, f"检索失败: {e}")


@router.post("/reindex")
async def reindex(db: Session = Depends(get_db),
                  user: User = Depends(get_current_user)) -> dict:
    """重建索引：按当前分块策略对当前用户全部文档重新切分 + 重新向量化（耗时操作）。"""
    uid = user.id
    def _progress(phase, done, total):
        label = "向量化" if phase == "embedding" else "解析分块"
        set_progress(uid, status="processing", phase=phase,
                     done=done, total=total,
                     detail=f"{label} {done}/{total}")
    set_progress(uid, status="processing", phase="splitting",
                 done=0, total=0, detail="重建索引中…")
    try:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None, lambda: knowledge_service.rebuild_index(db, uid, progress_cb=_progress))
        db.commit()
        set_progress(uid, status="done", detail=f"重建完成：{result['docs']} 篇文档，{result['chunks']} 个块")
        return ok({
            "docs": result["docs"],
            "chunks": result["chunks"],
            "detail": result["detail"],
            "message": f"重建完成：{result['docs']} 篇文档，{result['chunks']} 个块",
        })
    except Exception as e:  # noqa: BLE001
        logger.exception("重建索引失败")
        set_progress(uid, status="error", detail=f"重建索引失败: {e}")
        return fail(500, f"重建索引失败: {e}")


@router.get("/progress")
def progress(user: User = Depends(get_current_user)) -> dict:
    """查询当前用户入库/重建进度（前端轮询：status/phase/done/total/detail）"""
    return ok(get_progress(user.id))


@router.post("/test")
def retrieval_test(body: TestIn, db: Session = Depends(get_db),
                   user: User = Depends(get_current_user)) -> dict:
    """检索效果测试：批量问题输出 top-5 命中情况"""
    report = []
    for q in body.queries:
        results = knowledge_service.retrieve(db, user.id, q, top_k=5)
        report.append({
            "query": q,
            "top5": [
                {
                    "doc_title": r["doc_title"],
                    "section": r["section"],
                    "doc_type": r["doc_type"],
                    "page": r.get("page"),
                    "score": r["score"],
                }
                for r in results
            ],
        })
    return ok(report)
