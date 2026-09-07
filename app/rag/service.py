"""
RAG 知识库 - 服务层（模块核心，多用户架构）

职责：
- 文档入库：解析 → 切分 → MySQL 存块 → Chroma 存向量 → BM25 索引同步
- 混合检索：BM25 关键词 + 向量相似度融合（归一加权）→ 去重 → gte-rerank 精排 top-5
- 表结构导入：DESC/DDL 解析为"表结构"知识

多用户隔离：
- Chroma collection 命名 f"user_{user_id}_knowledge"，每个用户独立向量空间
- BM25 索引按 user_id 缓存（首次访问时从 MySQL 全量构建）
- LLM 客户端按 user_id 缓存（embed/rerank 用用户自己的 API Key，见 core/llm.get_user_llm）
"""
import logging
import shutil
import time
from typing import Any

import chromadb
from sqlalchemy.orm import Session

from app.config.settings import settings
from app.core.llm import get_user_llm
from app.rag import db as rag_db
from app.rag.bm25 import BM25Index
from app.rag.splitter import split_document
from app.rag.table_parser import parse_table_schema

logger = logging.getLogger(__name__)

# 文档类型常量（4 类，存 code，前端显示中文映射）
TYPE_GUIDE = "guide"     # 操作指导类
TYPE_BUG = "bug"          # BUG修复类
TYPE_SCHEMA = "schema"    # 表结构类
TYPE_OTHER = "other"      # 其他
ALLOWED_TYPES = [TYPE_GUIDE, TYPE_BUG, TYPE_SCHEMA, TYPE_OTHER]


class KnowledgeService:
    """知识库服务（多用户隔离，全局单例；方法按 user_id 隔离数据）"""

    def __init__(self) -> None:
        self._chroma_client = None
        self._collections: dict[int, Any] = {}     # user_id -> Chroma collection（懒加载）
        self._bm25_map: dict[int, BM25Index] = {}  # user_id -> BM25 索引（懒加载）
        self._ready = False

    # ------------------------------------------------------------------
    # 初始化
    # ------------------------------------------------------------------
    def initialize(self) -> None:
        """启动时调用：仅初始化 Chroma 客户端（collection 与 BM25 按用户懒加载）"""
        settings.DATA_DIR.mkdir(parents=True, exist_ok=True)

        logger.info("ChromaDB 版本: %s（持久化目录: %s）", chromadb.__version__, settings.DATA_DIR / "chroma")
        logger.info(
            "RAG 分块配置: chunk_size=%d, overlap=%d, 相邻合并窗口=±%d, 混合召回 top_n=%d",
            settings.CHUNK_MAX_CHARS, settings.CHUNK_OVERLAP_CHARS,
            settings.RAG_NEIGHBOR_WINDOW, settings.RETRIEVAL_TOP_N,
        )
        # Chroma 本地持久化（单实例访问，勿多进程同时启动后端）
        self._chroma_client = chromadb.PersistentClient(path=str(settings.DATA_DIR / "chroma"))
        self._ready = True

    def _collection_name(self, user_id: int) -> str:
        """用户专属 collection 名"""
        return f"user_{user_id}_knowledge"

    def _get_collection(self, user_id: int):
        """获取/创建当前用户的 Chroma collection（懒加载 + 健康检查）"""
        if user_id in self._collections:
            return self._collections[user_id]
        col = self._chroma_client.get_or_create_collection(
            name=self._collection_name(user_id),
            metadata={"hnsw:space": "cosine"},  # 余弦相似度
        )
        # 健康检查：HNSW 索引损坏时自动修复重建
        if not self._chroma_health_check(col):
            self._repair_chroma_store(user_id)
            col = self._collections[user_id]
        self._collections[user_id] = col
        return col

    def _chroma_health_check(self, col) -> bool:
        """collection 健康检查：peek 强制加载 HNSW 段，损坏时抛 InternalError"""
        try:
            col.peek(limit=1)
            return True
        except Exception as e:  # noqa: BLE001
            logger.error("Chroma 索引健康检查失败（HNSW 损坏或版本不兼容）: %s", e)
            return False

    def _repair_chroma_store(self, user_id: int) -> None:
        """
        修复当前用户的 collection（损坏自动修复 / 运行时 InternalError 触发）：
        1. best-effort 留底 chroma.sqlite3；
        2. 删除损坏的 user_{id}_knowledge collection；
        3. 重建空 collection（向量回填由 repair_index / reembed_all 完成）。
        """
        chroma_dir = settings.DATA_DIR / "chroma"
        try:
            backup_file = settings.DATA_DIR / f"chroma_backup_{user_id}_{time.strftime('%Y%m%d_%H%M%S')}.sqlite3"
            shutil.copy2(chroma_dir / "chroma.sqlite3", backup_file)
            logger.warning("用户 %s Chroma 留底 -> %s", user_id, backup_file)
        except OSError as e:
            logger.warning("Chroma 留底失败（不阻断修复）: %s", e)
        try:
            self._chroma_client.delete_collection(self._collection_name(user_id))
            logger.warning("已删除损坏的 %s", self._collection_name(user_id))
        except Exception as e:  # noqa: BLE001
            logger.warning("删除损坏 collection 失败（继续重建）: %s", e)
        col = self._chroma_client.get_or_create_collection(
            name=self._collection_name(user_id),
            metadata={"hnsw:space": "cosine"},
        )
        self._collections[user_id] = col

    def repair_index(self, db: Session, user_id: int) -> None:
        """运行时索引修复（上传触发 InternalError 时由 API 层调用）"""
        logger.warning("运行时触发 Chroma 索引修复 user_id=%s", user_id)
        # 清除缓存，强制重建
        self._collections.pop(user_id, None)
        self._bm25_map.pop(user_id, None)
        try:
            self._chroma_client.delete_collection(self._collection_name(user_id))
        except Exception as e:  # noqa: BLE001
            logger.warning("删除 collection 失败（忽略，继续重建）: %s", e)
        col = self._chroma_client.get_or_create_collection(
            name=self._collection_name(user_id), metadata={"hnsw:space": "cosine"},
        )
        self._collections[user_id] = col
        # 从 MySQL 全量回填向量 + 重建 BM25
        self._reembed_all(db, user_id)
        self._get_bm25(db, user_id, rebuild=True)

    def _reembed_all(self, db: Session, user_id: int) -> None:
        """修复后从 MySQL 全量块重新向量化写入当前用户 collection（分批 embed + upsert）"""
        chunks = rag_db.all_chunks(db, user_id)
        if not chunks:
            logger.info("用户 %s Chroma 重建完成：无知识块，无需回填", user_id)
            return
        client = get_user_llm(db, user_id)
        if not client.configured:
            logger.warning(
                "用户 %s 未配置 API Key，跳过向量回填（%d 块待补齐；BM25 仍可用）",
                user_id, len(chunks),
            )
            return
        col = self._get_collection(user_id)
        doc_map = {d["id"]: d["title"] for d in rag_db.list_docs(db, user_id)}
        size = settings.EMBED_BATCH_SIZE
        total = len(chunks)
        done = 0
        for i in range(0, total, size):
            batch = chunks[i:i + size]
            vecs = self._embed_batch(db, user_id, [c["text"] for c in batch])
            col.upsert(
                ids=[c["id"] for c in batch],
                documents=[c["text"] for c in batch],
                embeddings=vecs,
                metadatas=[
                    {
                        "doc_id": c["doc_id"],
                        "doc_title": doc_map.get(c["doc_id"], ""),
                        "section": c.get("section", ""),
                        "doc_type": c["doc_type"],
                    }
                    for c in batch
                ],
            )
            done += len(batch)
            logger.info("用户 %s Chroma 向量回填进度: %d/%d", user_id, done, total)
        logger.info("用户 %s Chroma 重建完成，共回填 %d 个向量", user_id, total)

    def _get_bm25(self, db: Session, user_id: int, rebuild: bool = False) -> BM25Index:
        """获取当前用户的 BM25 索引（首次访问或 rebuild 时从 MySQL 全量构建）"""
        if not rebuild and user_id in self._bm25_map:
            return self._bm25_map[user_id]
        bm25 = BM25Index()
        chunks = rag_db.all_chunks(db, user_id)
        bm25.build([(c["id"], c["text"]) for c in chunks])
        self._bm25_map[user_id] = bm25
        logger.info("用户 %s BM25 索引构建完成，共 %s 个知识块", user_id, len(chunks))
        return bm25

    # ------------------------------------------------------------------
    # 向量化（分批，百炼单批上限；用当前用户的 API Key）
    # ------------------------------------------------------------------
    def _embed_batch(self, db: Session, user_id: int, texts: list[str]) -> list[list[float]]:
        client = get_user_llm(db, user_id)
        vecs: list[list[float]] = []
        for i in range(0, len(texts), settings.EMBED_BATCH_SIZE):
            batch = texts[i:i + settings.EMBED_BATCH_SIZE]
            vecs.extend(client.embed(batch))
        return vecs

    # ------------------------------------------------------------------
    # 文档入库
    # ------------------------------------------------------------------
    def add_document(
        self,
        db: Session,
        user_id: int,
        title: str,
        text: str,
        doc_type: str = TYPE_GUIDE,
        source: str = "",
        doc_id: int | None = None,
        append: bool = False,
    ) -> int:
        """
        新增文档（或向已有文档增量追加）。
        - append=False：新建文档；
        - append=True：doc_id 必填，向已有文档追加块（原有块保留）。
        返回 doc_id。
        """
        if doc_type not in ALLOWED_TYPES:
            raise ValueError(f"不支持的文档类型: {doc_type}，可选: {ALLOWED_TYPES}")

        # 1. 切分
        chunks = split_document(text, max_chars=settings.CHUNK_MAX_CHARS,
                                overlap=settings.CHUNK_OVERLAP_CHARS,
                                whole_threshold=settings.CHUNK_WHOLE_MAX_CHARS)
        if not chunks:
            raise ValueError("文档内容为空，无法入库")

        col = self._get_collection(user_id)

        # 2. 文档记录（append 时序号接续已有块，类型/标题沿用原文档）
        if append:
            if doc_id is None:
                raise ValueError("增量追加必须提供 doc_id")
            doc = rag_db.get_doc(db, doc_id, user_id)
            if doc is None:
                raise ValueError(f"文档不存在或无权访问: {doc_id}")
            start_seq = doc["chunk_count"] or 0
            title = doc["title"]
            doc_type = doc["doc_type"]
        else:
            doc_id = rag_db.insert_doc(db, user_id, title=title, doc_type=doc_type, source=source)
            start_seq = 0

        # 3. MySQL 写块 + 更新文档块数
        ids = rag_db.insert_chunks(db, doc_id, doc_type, chunks, start_seq=start_seq)
        rag_db.update_doc_meta(db, doc_id, user_id, chunk_count=start_seq + len(ids))

        # 4. Chroma 写向量（索引损坏 InternalError：回滚本次 MySQL 写入后抛出，由上层统一修复）
        texts = [c["text"] for c in chunks]
        vectors = self._embed_batch(db, user_id, texts)
        metadatas = [
            {"doc_id": doc_id, "doc_title": title, "section": c.get("section", ""), "doc_type": doc_type}
            for c in chunks
        ]
        try:
            col.upsert(ids=ids, documents=texts, embeddings=vectors, metadatas=metadatas)
        except chromadb.errors.InternalError:
            if append:
                rag_db.delete_chunks_by_ids(db, ids)
                rag_db.update_doc_meta(db, doc_id, user_id, chunk_count=start_seq)
            else:
                rag_db.delete_chunks_by_doc(db, doc_id)
                rag_db.delete_doc(db, doc_id, user_id)
            raise

        # 5. BM25 索引同步
        bm25 = self._get_bm25(db, user_id)
        for cid, t in zip(ids, texts):
            bm25.add(cid, t)

        logger.info("文档入库完成 user=%s doc_id=%s title=%s 块数=%s", user_id, doc_id, title, len(ids))
        return doc_id

    def delete_document(self, db: Session, user_id: int, doc_id: int) -> bool:
        """删除文档：MySQL 记录 + Chroma 向量 + BM25 索引同步清理（校验 user_id）"""
        col = self._get_collection(user_id)
        chunk_ids = rag_db.delete_chunks_by_doc(db, doc_id)
        deleted = rag_db.delete_doc(db, doc_id, user_id)
        if chunk_ids:
            try:
                col.delete(ids=chunk_ids)
            except Exception as e:  # noqa: BLE001
                logger.warning("Chroma 清理失败（不影响主流程）: %s", e)
        bm25 = self._get_bm25(db, user_id)
        for cid in chunk_ids:
            bm25.remove(cid)
        return deleted

    def import_table_schema(self, db: Session, user_id: int, content: str, table_name: str = "") -> list[int]:
        """
        批量导入表结构（DESC 输出或 DDL）。
        DDL 可含多表，每表生成一条"表结构"文档。
        返回 doc_id 列表。
        """
        schemas = parse_table_schema(content, table_name=table_name)
        if not schemas:
            raise ValueError("未解析到表结构，请检查粘贴内容格式")
        doc_ids = []
        for s in schemas:
            did = self.add_document(
                db, user_id,
                title=f"表结构-{s.name}",
                text=s.to_text(),
                doc_type=TYPE_SCHEMA,
                source="table_schema_import",
            )
            doc_ids.append(did)
        return doc_ids

    # ------------------------------------------------------------------
    # 查询接口（代理数据层，均校验 user_id）
    # ------------------------------------------------------------------
    def list_docs(self, db: Session, user_id: int) -> list[dict]:
        """当前用户的文档列表"""
        return rag_db.list_docs(db, user_id)

    def get_doc(self, db: Session, doc_id: int, user_id: int) -> dict | None:
        """文档详情（校验 user_id）"""
        return rag_db.get_doc(db, doc_id, user_id)

    def get_doc_full(self, db: Session, doc_id: int, user_id: int) -> dict | None:
        """文档详情 + 全文（按块 seq 顺序拼接），供在线查看/编辑"""
        doc = rag_db.get_doc(db, doc_id, user_id)
        if doc is None:
            return None
        chunks = rag_db.get_doc_chunks(db, doc_id)
        doc["text"] = "\n\n".join(c["text"] for c in chunks)
        return doc

    def update_document(self, db: Session, user_id: int, doc_id: int,
                        title: str, text: str, doc_type: str) -> bool:
        """
        编辑文档：保留 doc_id，删除旧块/向量/BM25，重新切分入库。
        title/text/doc_type 均更新；source 等其余元数据保留。
        """
        doc = rag_db.get_doc(db, doc_id, user_id)
        if doc is None:
            return False
        if doc_type not in ALLOWED_TYPES:
            raise ValueError(f"不支持的文档类型: {doc_type}，可选: {ALLOWED_TYPES}")
        chunks = split_document(text, max_chars=settings.CHUNK_MAX_CHARS,
                                overlap=settings.CHUNK_OVERLAP_CHARS,
                                whole_threshold=settings.CHUNK_WHOLE_MAX_CHARS)
        if not chunks:
            raise ValueError("文档内容为空，无法保存")

        col = self._get_collection(user_id)
        bm25 = self._get_bm25(db, user_id)

        # 1. 清旧：块记录 + 向量 + BM25
        old_ids = rag_db.delete_chunks_by_doc(db, doc_id)
        if old_ids:
            try:
                col.delete(ids=old_ids)
            except Exception as e:  # noqa: BLE001
                logger.warning("Chroma 旧向量清理失败（不影响保存）: %s", e)
        for cid in old_ids:
            bm25.remove(cid)

        # 2. 写新：块记录 + 向量 + BM25
        ids = rag_db.insert_chunks(db, doc_id, doc_type, chunks, start_seq=0)
        texts = [c["text"] for c in chunks]
        vectors = self._embed_batch(db, user_id, texts)
        metadatas = [
            {"doc_id": doc_id, "doc_title": title, "section": c.get("section", ""), "doc_type": doc_type}
            for c in chunks
        ]
        col.upsert(ids=ids, documents=texts, embeddings=vectors, metadatas=metadatas)
        for cid, t in zip(ids, texts):
            bm25.add(cid, t)

        # 3. 更新文档元数据（标题/类型/块数）
        rag_db.update_doc_meta(db, doc_id, user_id, chunk_count=len(ids), title=title, doc_type=doc_type)
        logger.info("文档更新完成 user=%s doc_id=%s title=%s 块数=%s", user_id, doc_id, title, len(ids))
        return True

    def change_doc_type(self, db: Session, user_id: int, doc_id: int, doc_type: str) -> bool:
        """轻量切换文档类型：更新 doc/chunk 表 + Chroma metadata，不重新切分/嵌入"""
        doc = rag_db.get_doc(db, doc_id, user_id)
        if doc is None:
            return False
        if doc_type not in ALLOWED_TYPES:
            raise ValueError(f"不支持的文档类型: {doc_type}，可选: {ALLOWED_TYPES}")
        col = self._get_collection(user_id)
        # 1. MySQL：doc 表 + chunk 表 doc_type
        rag_db.update_doc_type_only(db, doc_id, user_id, doc_type)
        # 2. Chroma：更新该文档各 chunk metadata 的 doc_type
        try:
            chunks = rag_db.get_doc_chunks(db, doc_id)
            if chunks:
                ids = [c["id"] for c in chunks]
                metadatas = [{"doc_type": doc_type} for _ in ids]
                col.update(ids=ids, metadatas=metadatas)
        except Exception as e:  # noqa: BLE001
            logger.warning("Chroma metadata 更新失败（不影响类型切换）: %s", e)
        logger.info("文档类型切换完成 user=%s doc_id=%s -> %s", user_id, doc_id, doc_type)
        return True

    # ------------------------------------------------------------------
    # 检索（混合召回 + 重排，按用户隔离）
    # ------------------------------------------------------------------
    @staticmethod
    def _minmax(scores: list[float]) -> list[float]:
        """min-max 归一到 [0,1]（全相等时返回全 1）"""
        if not scores:
            return []
        lo, hi = min(scores), max(scores)
        if hi - lo < 1e-9:
            return [1.0] * len(scores)
        return [(s - lo) / (hi - lo) for s in scores]

    def retrieve(self, db: Session, user_id: int, query: str,
                 doc_type: str | None = None, top_k: int = 5) -> list[dict]:
        """
        混合检索（当前用户 collection）：
        1. BM25 取 top-N + 向量取 top-N；
        2. 各自归一后加权融合（默认 0.5/0.5），去重排序取 top-N；
        3. gte-rerank 精排取 top_k（失败时降级用融合分）。
        返回: [{"text","score","doc_id","doc_title","section","doc_type"}]
        """
        n = settings.RETRIEVAL_TOP_N
        col = self._get_collection(user_id)
        bm25 = self._get_bm25(db, user_id)

        # 1a. BM25 关键词召回（表名/报错码强关键词由此保证）
        bm25_hits = bm25.search(query, top_n=n)

        # 1b. 向量召回
        where = {"doc_type": doc_type} if doc_type else None
        q_vec = self._embed_batch(db, user_id, [query])[0]
        col_count = col.count()
        res = col.query(query_embeddings=[q_vec], n_results=min(n, max(col_count, 1)), where=where)
        vec_hits: list[tuple[str, float]] = []
        if res["ids"] and res["ids"][0]:
            for cid, dist in zip(res["ids"][0], res["distances"][0]):
                vec_hits.append((cid, 1.0 / (1.0 + dist)))  # 距离转相似度

        # 2. 融合：归一加权，合并去重
        bm25_map = {cid: s for cid, s in zip([h[0] for h in bm25_hits], self._minmax([h[1] for h in bm25_hits]))}
        vec_map = {cid: s for cid, s in zip([h[0] for h in vec_hits], self._minmax([h[1] for h in vec_hits]))}
        all_ids = list(dict.fromkeys(list(bm25_map.keys()) + list(vec_map.keys())))
        fused: list[tuple[str, float]] = []
        for cid in all_ids:
            score = settings.BM25_WEIGHT * bm25_map.get(cid, 0.0) + settings.VECTOR_WEIGHT * vec_map.get(cid, 0.0)
            fused.append((cid, score))
        fused.sort(key=lambda x: x[1], reverse=True)
        candidates = fused[:n]

        if not candidates:
            return []

        # 3. 取块详情（仅当前用户的块）
        user_chunks = rag_db.all_chunks(db, user_id)
        detail = {c["id"]: c for c in user_chunks if c["id"] in {cid for cid, _ in candidates}}
        cand_texts = [detail[cid]["text"] for cid, _ in candidates if cid in detail]

        # 4. 重排精排（失败降级为融合分；用当前用户的 rerank 模型）
        try:
            client = get_user_llm(db, user_id)
            reranked = client.rerank(query, cand_texts, top_n=top_k)
            results = []
            for r in reranked:
                cid = candidates[r["index"]][0]
                c = detail[cid]
                results.append({
                    "id": cid,
                    "seq": c.get("seq", 0),
                    "text": c["text"],
                    "score": round(float(r["score"]), 4),
                    "doc_id": c["doc_id"],
                    "section": c["section"],
                    "doc_type": c["doc_type"],
                })
            # 补充文档名
            for r in results:
                d = rag_db.get_doc(db, r["doc_id"], user_id)
                r["doc_title"] = d["title"] if d else ""
            return results
        except Exception as e:  # noqa: BLE001
            logger.warning("重排失败，降级用融合分: %s", e)
            results = []
            for cid, score in candidates[:top_k]:
                if cid not in detail:
                    continue
                c = detail[cid]
                d = rag_db.get_doc(db, c["doc_id"], user_id)
                results.append({
                    "id": cid,
                    "seq": c.get("seq", 0),
                    "text": c["text"],
                    "score": round(score, 4),
                    "doc_id": c["doc_id"],
                    "doc_title": d["title"] if d else "",
                    "section": c["section"],
                    "doc_type": c["doc_type"],
                })
            return results

    # ------------------------------------------------------------------
    # 检索后聚合：同文档相邻块合并（解决"命中一段、上下文不完整"）
    # ------------------------------------------------------------------
    @staticmethod
    def _dedupe_join(texts: list[str], overlap: int) -> str:
        """拼接同文档相邻块时，去掉重叠重复（后块开头若与前块结尾一致则裁掉）。"""
        if not texts:
            return ""
        out = texts[0]
        for t in texts[1:]:
            cut = 0
            max_k = min(len(t), len(out), overlap + 64)
            for k in range(max_k, 12, -1):
                if out.endswith(t[:k]):
                    cut = k
                    break
            out += t[cut:]
        return out

    def expand_neighbors(self, db: Session, hits: list[dict], user_id: int,
                         window: int | None = None) -> list[dict]:
        """
        检索后聚合：把命中块所属文档的相邻块（seq ± window）一起取出，
        同一文档中连续的 seq 合并为一个更大的上下文片段，重叠部分去重。
        hits: retrieve() 返回的块级结果（需含 id/doc_id/seq/score 等字段）
        返回: 合并片段列表（按相关度倒序），字段与 retrieve 一致，另含 is_merged/chunk_count。
        """
        if not hits:
            return []
        if window is None:
            window = settings.RAG_NEIGHBOR_WINDOW

        doc_ids = {h["doc_id"] for h in hits}
        # 一次性取出每篇涉及文档的全部块（seq -> chunk）
        doc_chunks: dict[int, dict[int, dict]] = {}
        for did in doc_ids:
            doc_chunks[did] = {c["seq"]: c for c in rag_db.get_doc_chunks(db, did)}

        # 每篇文档：命中 seq 扩展窗口 → 连续区间分组
        segments: list[dict] = []
        for did in doc_ids:
            chunks_map = doc_chunks[did]
            hit_seqs = {int(h["seq"]) for h in hits if h["doc_id"] == did}
            expanded: set[int] = set()
            for s in hit_seqs:
                for k in range(s - window, s + window + 1):
                    if k in chunks_map:
                        expanded.add(k)

            groups: list[list[int]] = []
            for s in sorted(expanded):
                if groups and s == groups[-1][-1] + 1:
                    groups[-1].append(s)
                else:
                    groups.append([s])

            doc_hits = [h for h in hits if h["doc_id"] == did]
            for grp in groups:
                grp_set = set(grp)
                seg_hits = [h for h in doc_hits if int(h["seq"]) in grp_set]
                score = max((h["score"] for h in seg_hits), default=0.0)
                merged_text = self._dedupe_join(
                    [chunks_map[k]["text"] for k in grp], settings.CHUNK_OVERLAP_CHARS
                )
                section = next((chunks_map[k]["section"] for k in grp if chunks_map[k].get("section")), "")
                title = doc_hits[0]["doc_title"] if doc_hits else (rag_db.get_doc(db, did, user_id) or {}).get("title", "")
                segments.append({
                    "doc_id": did,
                    "doc_title": title,
                    "section": section,
                    "text": merged_text,
                    "score": round(score, 4),
                    "doc_type": doc_hits[0]["doc_type"] if doc_hits else (rag_db.get_doc(db, did, user_id) or {}).get("doc_type", ""),
                    "is_merged": len(grp) > 1,
                    "chunk_count": len(grp),
                })
        segments.sort(key=lambda x: x["score"], reverse=True)
        return segments

    # ------------------------------------------------------------------
    # 重建索引：按当前分块策略重新切分 + 重新向量化全部文档（当前用户）
    # ------------------------------------------------------------------
    def rebuild_index(self, db: Session, user_id: int) -> dict:
        """
        全量重建（当前用户）：重置 collection → 逐篇取全文按新策略重切 →
        重写 MySQL 块 → 重新向量化入库 → 重建 BM25。文档行（title/type/source）保留。
        返回 {"docs", "chunks", "detail": [{doc_id,title,chunks}]}。
        """
        docs = rag_db.list_docs(db, user_id)
        # 1. 重置当前用户 Chroma 集合（清空旧分块向量）
        try:
            self._chroma_client.delete_collection(self._collection_name(user_id))
            logger.warning("重建索引：已清空用户 %s 旧 Chroma 集合", user_id)
        except Exception as e:  # noqa: BLE001
            logger.warning("重置 Chroma 集合失败（继续重建）: %s", e)
        self._collections.pop(user_id, None)
        col = self._get_collection(user_id)

        total_chunks = 0
        detail: list[dict] = []
        for d in docs:
            did = d["id"]
            old_chunks = rag_db.get_doc_chunks(db, did)
            full_text = "\n\n".join(c["text"] for c in old_chunks).strip()
            if not full_text:
                continue
            # 2. 清旧块 → 按当前策略重切 → 写回 MySQL
            rag_db.delete_chunks_by_doc(db, did)
            new_chunks = split_document(
                full_text,
                max_chars=settings.CHUNK_MAX_CHARS,
                overlap=settings.CHUNK_OVERLAP_CHARS,
                whole_threshold=settings.CHUNK_WHOLE_MAX_CHARS,
            )
            if not new_chunks:
                continue
            ids = rag_db.insert_chunks(db, did, d["doc_type"], new_chunks, start_seq=0)
            rag_db.update_doc_meta(db, did, user_id, chunk_count=len(ids))

            # 3. 重新向量化写入 Chroma
            texts = [c["text"] for c in new_chunks]
            vectors = self._embed_batch(db, user_id, texts)
            col.upsert(
                ids=ids,
                documents=texts,
                embeddings=vectors,
                metadatas=[
                    {"doc_id": did, "doc_title": d["title"], "section": c.get("section", ""), "doc_type": d["doc_type"]}
                    for c in new_chunks
                ],
            )
            total_chunks += len(ids)
            detail.append({"doc_id": did, "title": d["title"], "chunks": len(ids)})
            logger.info("用户 %s 重建索引：《%s》→ %d 块", user_id, d["title"], len(ids))

        # 4. 重建 BM25
        self._bm25_map.pop(user_id, None)
        self._get_bm25(db, user_id, rebuild=True)
        logger.info("用户 %s 重建索引完成：%d 篇文档，%d 个块", user_id, len(docs), total_chunks)
        return {"docs": len(docs), "chunks": total_chunks, "detail": detail}


# 全局单例
knowledge_service = KnowledgeService()
