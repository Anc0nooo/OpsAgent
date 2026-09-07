"""
RAG 知识库 - API 路由（多用户：所有接口需登录，按 user_id 隔离数据）
接口清单：
- POST /api/knowledge/upload        上传文档（txt/md/pdf/docx，支持增量追加）
- POST /api/knowledge/text          粘贴文本入库（表结构/SQL脚本/操作文档等）
- POST /api/knowledge/table_schema  批量导入表结构（DESC 输出或 DDL）
- GET  /api/knowledge/docs          文档列表（当前用户）
- DELETE /api/knowledge/docs/{id}   删除文档（校验归属）
- POST /api/knowledge/search        混合检索（BM25+向量+重排，返回 top-5 与来源）
- POST /api/knowledge/test          检索效果测试（批量问题输出命中与 top-5）
"""
import logging

import chromadb
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
import pymupdf  # PyMuPDF
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user
from app.db.engine import get_db
from app.db.models import User
from app.models.common import fail, ok
from app.rag.service import ALLOWED_TYPES, knowledge_service

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
    file: UploadFile = File(...),
    doc_type: str = Form("guide"),
    title: str = Form(""),
    append_doc_id: int | None = Form(None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    """上传文档入库（txt/md/sql/pdf/docx），支持增量追加"""
    if doc_type not in ALLOWED_TYPES:
        return fail(400, f"不支持的文档类型: {doc_type}，可选: {ALLOWED_TYPES}")
    data = await file.read()
    try:
        if file.filename and file.filename.lower().endswith(".docx"):
            text = read_docx_text(data)
        else:
            text = read_file_text(file.filename or "", data)
    except ValueError as e:
        return fail(400, str(e))

    doc_id: int | None = None
    try:
        doc_id = knowledge_service.add_document(
            db, user.id,
            title=title or file.filename or "未命名文档",
            text=text,
            doc_type=doc_type,
            source=f"upload:{file.filename}",
            doc_id=append_doc_id,
            append=append_doc_id is not None,
        )
        db.commit()
        return ok({"doc_id": doc_id, "message": "入库成功"})
    except chromadb.errors.InternalError:
        # Chroma HNSW 索引损坏：本次写入已回滚，自动修复后提示重新上传
        logger.exception("上传触发 Chroma 索引损坏")
        try:
            knowledge_service.repair_index(db, user.id)
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
    """解析聊天记录预览（不入库），返回消息数/参与人/时间范围/前10条"""
    from app.rag.chat_parser import build_preview, filter_messages, parse_text
    try:
        msgs, fmt = parse_text(body.text, body.source)
        if not msgs:
            return fail(400, "未能识别聊天记录格式，请确认是微信或飞书导出的 txt 文本")
        msgs = filter_messages(msgs, body.filter_system, body.filter_media)
        preview = build_preview(msgs)
        preview["format"] = fmt
        preview["split_mode"] = body.split_mode
        return ok(preview)
    except Exception as e:  # noqa: BLE001
        logger.exception("聊天记录解析失败")
        return fail(500, f"解析失败: {e}")


@router.post("/chat/import")
def chat_import(body: ChatParseIn, db: Session = Depends(get_db),
                user: User = Depends(get_current_user)) -> dict:
    """聊天记录导入知识库（类型固定 other，复用文本入库流程：切块→向量化→存 Chroma）"""
    from app.rag.chat_parser import filter_messages, parse_text, split_to_docs
    try:
        msgs, fmt = parse_text(body.text, body.source)
        if not msgs:
            return fail(400, "未能识别聊天记录格式，请确认是微信或飞书导出的 txt 文本")
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
def remove_doc(doc_id: int, db: Session = Depends(get_db),
               user: User = Depends(get_current_user)) -> dict:
    """删除文档（同步清理向量与索引；校验归属）"""
    deleted = knowledge_service.delete_document(db, user.id, doc_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"文档不存在或无权访问: {doc_id}")
    db.commit()
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


@router.post("/search")
def search(body: SearchIn, db: Session = Depends(get_db),
           user: User = Depends(get_current_user)) -> dict:
    """混合检索：BM25 + 向量 + gte-rerank，返回 top_k（默认5）"""
    try:
        results = knowledge_service.retrieve(db, user.id, body.query,
                                             doc_type=body.doc_type, top_k=body.top_k)
        return ok(results)
    except Exception as e:  # noqa: BLE001
        logger.exception("检索失败")
        return fail(500, f"检索失败: {e}")


@router.post("/reindex")
def reindex(db: Session = Depends(get_db),
            user: User = Depends(get_current_user)) -> dict:
    """重建索引：按当前分块策略对当前用户全部文档重新切分 + 重新向量化（耗时操作）。"""
    try:
        result = knowledge_service.rebuild_index(db, user.id)
        db.commit()
        return ok({
            "docs": result["docs"],
            "chunks": result["chunks"],
            "detail": result["detail"],
            "message": f"重建完成：{result['docs']} 篇文档，{result['chunks']} 个块",
        })
    except Exception as e:  # noqa: BLE001
        logger.exception("重建索引失败")
        return fail(500, f"重建索引失败: {e}")


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
                    "score": r["score"],
                }
                for r in results
            ],
        })
    return ok(report)
