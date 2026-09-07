"""
RAG 知识库 - 数据访问层（MySQL + SQLAlchemy，多用户隔离）
存储知识文档元数据（knowledge_docs）与切分块（knowledge_chunks）。
向量本身存 Chroma（按 user_id 隔离 collection），本库只存文本与元数据。

所有函数接受 db: Session 参数（由 API 层 get_db 依赖注入），
文档相关操作同时校验 user_id 防止越权。
"""
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import KnowledgeChunk, KnowledgeDoc


# ----------------------------------------------------------------------
# knowledge_docs 操作
# ----------------------------------------------------------------------
def insert_doc(db: Session, user_id: int, title: str, doc_type: str, source: str) -> int:
    """新增文档记录，返回 doc_id"""
    doc = KnowledgeDoc(user_id=user_id, title=title, doc_type=doc_type, source=source)
    db.add(doc)
    db.flush()
    return int(doc.id)


def update_doc_meta(
    db: Session,
    doc_id: int,
    user_id: int,
    chunk_count: int | None = None,
    title: str | None = None,
    doc_type: str | None = None,
) -> bool:
    """更新文档元数据（块数量/标题/类型）；校验 user_id 防越权"""
    doc = db.query(KnowledgeDoc).filter(
        KnowledgeDoc.id == doc_id, KnowledgeDoc.user_id == user_id
    ).first()
    if doc is None:
        return False
    if chunk_count is not None:
        doc.chunk_count = chunk_count
    if title is not None:
        doc.title = title
    if doc_type is not None:
        doc.doc_type = doc_type
    db.flush()
    return True


def get_doc_chunks(db: Session, doc_id: int) -> list[dict[str, Any]]:
    """按 seq 顺序返回某文档全部块（含 id/section/text），供在线查看与编辑"""
    rows = db.query(KnowledgeChunk).filter(
        KnowledgeChunk.doc_id == doc_id
    ).order_by(KnowledgeChunk.seq).all()
    return [{"id": r.id, "seq": r.seq, "section": r.section, "text": r.text} for r in rows]


def update_doc_type_only(db: Session, doc_id: int, user_id: int, doc_type: str) -> int:
    """轻量改类型：仅更新 doc 表与 chunk 表的 doc_type（不碰向量/文本/切分），返回受影响 chunk 行数"""
    doc = db.query(KnowledgeDoc).filter(
        KnowledgeDoc.id == doc_id, KnowledgeDoc.user_id == user_id
    ).first()
    if doc is None:
        return 0
    doc.doc_type = doc_type
    updated = db.query(KnowledgeChunk).filter(
        KnowledgeChunk.doc_id == doc_id
    ).update({KnowledgeChunk.doc_type: doc_type}, synchronize_session=False)
    db.flush()
    return int(updated)


def get_doc(db: Session, doc_id: int, user_id: int | None = None) -> dict[str, Any] | None:
    """按 id 查文档；传 user_id 时校验归属（防越权）"""
    q = db.query(KnowledgeDoc).filter(KnowledgeDoc.id == doc_id)
    if user_id is not None:
        q = q.filter(KnowledgeDoc.user_id == user_id)
    doc = q.first()
    if doc is None:
        return None
    return {
        "id": doc.id, "user_id": doc.user_id, "title": doc.title,
        "doc_type": doc.doc_type, "source": doc.source, "chunk_count": doc.chunk_count,
        "created_at": str(doc.created_at) if doc.created_at else "",
        "updated_at": str(doc.updated_at) if doc.updated_at else "",
    }


def list_docs(db: Session, user_id: int) -> list[dict[str, Any]]:
    """当前用户的文档列表（按更新时间倒序）"""
    rows = db.query(KnowledgeDoc).filter(
        KnowledgeDoc.user_id == user_id
    ).order_by(KnowledgeDoc.updated_at.desc()).all()
    return [{
        "id": r.id, "user_id": r.user_id, "title": r.title,
        "doc_type": r.doc_type, "source": r.source, "chunk_count": r.chunk_count,
        "created_at": str(r.created_at) if r.created_at else "",
        "updated_at": str(r.updated_at) if r.updated_at else "",
    } for r in rows]


def delete_doc(db: Session, doc_id: int, user_id: int) -> bool:
    """删除文档记录及其全部块记录（校验 user_id）；返回是否删除成功"""
    doc = db.query(KnowledgeDoc).filter(
        KnowledgeDoc.id == doc_id, KnowledgeDoc.user_id == user_id
    ).first()
    if doc is None:
        return False
    db.query(KnowledgeChunk).filter(KnowledgeChunk.doc_id == doc_id).delete()
    db.delete(doc)
    db.flush()
    return True


def get_doc_user_id(db: Session, doc_id: int) -> int | None:
    """查文档归属的用户 ID（用于 Chroma collection 选择）"""
    doc = db.query(KnowledgeDoc).filter(KnowledgeDoc.id == doc_id).first()
    return doc.user_id if doc else None


# ----------------------------------------------------------------------
# knowledge_chunks 操作
# ----------------------------------------------------------------------
def insert_chunks(
    db: Session, doc_id: int, doc_type: str, chunks: list[dict], start_seq: int = 0,
) -> list[str]:
    """
    批量写入块记录。
    chunks: [{"text":..., "section":...}, ...]
    start_seq: 起始序号（增量追加时接续已有块序号，避免覆盖）
    返回块 id 列表（与输入顺序一致）
    """
    ids: list[str] = []
    for i, ch in enumerate(chunks):
        seq = start_seq + i
        cid = f"{doc_id}_{seq}"
        chunk = KnowledgeChunk(
            id=cid, doc_id=doc_id, seq=seq,
            section=ch.get("section", ""), text=ch["text"], doc_type=doc_type,
        )
        db.add(chunk)
        ids.append(cid)
    db.flush()
    return ids


def delete_chunks_by_doc(db: Session, doc_id: int) -> list[str]:
    """删除某文档全部块，返回被删除的块 id（用于同步清理 Chroma）"""
    rows = db.query(KnowledgeChunk).filter(
        KnowledgeChunk.doc_id == doc_id
    ).all()
    ids = [r.id for r in rows]
    db.query(KnowledgeChunk).filter(KnowledgeChunk.doc_id == doc_id).delete()
    db.flush()
    return ids


def delete_chunks_by_ids(db: Session, ids: list[str]) -> None:
    """按 id 批量删除块（增量追加入库失败回滚用）"""
    if ids:
        db.query(KnowledgeChunk).filter(KnowledgeChunk.id.in_(ids)).delete(synchronize_session=False)
        db.flush()


def all_chunks(db: Session, user_id: int) -> list[dict[str, Any]]:
    """当前用户的全量块（启动时构建 BM25 索引用）"""
    rows = db.query(KnowledgeChunk).join(
        KnowledgeDoc, KnowledgeChunk.doc_id == KnowledgeDoc.id
    ).filter(
        KnowledgeDoc.user_id == user_id
    ).order_by(KnowledgeChunk.doc_id, KnowledgeChunk.seq).all()
    return [{
        "id": r.id, "doc_id": r.doc_id, "seq": r.seq,
        "section": r.section, "text": r.text, "doc_type": r.doc_type,
    } for r in rows]


def migrate_doc_types(db: Session, user_id: int, legacy_map: dict[str, str]) -> int:
    """迁移旧 doc_type 到新 code（仅当前用户的文档），返回受影响行数。"""
    total = 0
    for old, new in legacy_map.items():
        docs = db.query(KnowledgeDoc).filter(
            KnowledgeDoc.user_id == user_id, KnowledgeDoc.doc_type == old
        ).all()
        for d in docs:
            d.doc_type = new
            total += 1
        chunks = db.query(KnowledgeChunk).filter(
            KnowledgeChunk.doc_type == old,
            KnowledgeChunk.doc_id.in_([d.id for d in docs]) if docs else False,
        ).update({KnowledgeChunk.doc_type: new}, synchronize_session=False)
        total += int(chunks)
    db.flush()
    return total
