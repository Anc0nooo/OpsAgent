"""
Agent 规划器 - 上下文管理（多用户：检索按 user_id 隔离）
职责：
- 历史消息裁剪（HISTORY_MAX_TURNS 上限）；
- RAG 检索结果格式化为注入文本：
  普通知识先按块召回，再做"同文档相邻块合并"（避免只命中一段、内容不完整）；
  表结构按块注入（每张表独立）；
- 注入截断一律在句号/换行等语义边界处裁断，绝不从句子中间硬切。
"""
import re

from sqlalchemy.orm import Session

from app.agent.store import get_history
from app.config.settings import settings
from app.rag.service import TYPE_SCHEMA, knowledge_service

# RAG 注入条数配置
RAG_TOP_K_GENERAL = 6   # 普通知识块召条数（送重排；块级，之后再合并相邻块）
RAG_TOP_K_SCHEMA = 3    # 表结构条数（SQL 生成必须核对字段，防臆造）
# 注入时的单片段字符预算（合并片段=相邻多块拼接，预算要放得下完整片段；
# 超出预算时在句末/换行边界裁断，保证不切断句子）
SEGMENT_MAX_CHARS = 2600
SCHEMA_HIT_MAX_CHARS = 1200

# 句末/换行边界（用于截断时回退到最近的完整句子）
_BOUNDARY_RE = re.compile(r"[。！？；\n.!?]")


def _trim_at_boundary(text: str, limit: int) -> str:
    """超过 limit 时，在 limit 以内最后一个句末/换行边界处截断，避免硬切断句子。"""
    if len(text) <= limit:
        return text
    window = text[:limit]
    m = None
    for m in re.finditer(_BOUNDARY_RE, window):
        pass  # 取最后一个边界
    if m is not None and m.start() > limit * 0.5:
        return window[: m.end()].rstrip() + "…（截断）"
    return window.rstrip() + "…（截断）"


def build_history(db: Session, session_id: str, limit: int | None = None) -> str:
    """裁剪后的对话历史（文本形式，注入 prompt）"""
    turns = limit or settings.HISTORY_MAX_TURNS
    msgs = get_history(db, session_id, limit_turns=turns)
    if not msgs:
        return "（无历史）"
    lines = []
    for m in msgs:
        tag = "用户" if m["role"] == "user" else "助手"
        # 助手回答常含表字段列表，截断会丢关键上文；放长到 800
        # 用户问题通常较短，保留 400 已足够
        cap = 800 if m["role"] == "assistant" else 400
        content = m["content"][:cap] + ("…（截断）" if len(m["content"]) > cap else "")
        lines.append(f"{tag}: {content}")
    return "\n".join(lines)


def _format_hit(hit: dict, max_len: int, index: int) -> str:
    """检索片段 → 注入文本块（超预算在句边界裁断，不硬切句子）"""
    text = _trim_at_boundary(hit.get("text", ""), max_len)
    merged = "，合并相邻片段" if hit.get("is_merged") else ""
    return (
        f"[{index}] 来源：{hit.get('doc_title', '')}｜{hit.get('section', '')}"
        f"（类型：{hit.get('doc_type', '')}，相关度 {hit.get('score', '')}{merged}）\n{text}"
    )


def _format_block(hits: list[dict], max_len: int) -> str:
    return "\n\n".join(_format_hit(h, max_len, i) for i, h in enumerate(hits, 1))


def _retrieve_context(db: Session, user_id: int, query: str) -> tuple[list[dict], list[dict]]:
    """
    检索 + 聚合（当前用户知识库）：
    - 普通知识：块级召回 top_k → 同文档相邻块合并为更大片段（解决内容不完整）；
    - 表结构：块级召回（每张表独立，不做相邻合并）。
    返回 (知识片段列表, 表结构块列表)。
    """
    general_hits = knowledge_service.retrieve(db, user_id, query, top_k=RAG_TOP_K_GENERAL)
    segments = knowledge_service.expand_neighbors(db, general_hits, user_id)
    schema_hits = knowledge_service.retrieve(db, user_id, query, doc_type=TYPE_SCHEMA, top_k=RAG_TOP_K_SCHEMA)
    return segments, schema_hits


def _build_rag_text(segments: list[dict], schema_hits: list[dict]) -> str:
    """组装 rag_context 文本（带命中统计头）"""
    g_count, s_count = len(segments), len(schema_hits)
    if g_count == 0 and s_count == 0:
        header = "【知识库检索统计】0 条命中（无相关知识，无相关表结构）"
    else:
        header = f"【知识库检索统计】{g_count + s_count} 条命中（知识 {g_count} 条 + 表结构 {s_count} 条）"

    parts = [header]
    if segments:
        parts.append("◆ 相关知识（已按同文档相邻片段合并，保证上下文完整）\n" + _format_block(segments, SEGMENT_MAX_CHARS))
    if schema_hits:
        parts.append("◆ 相关表结构（生成 SQL 时只能使用以下表/字段）\n" + _format_block(schema_hits, SCHEMA_HIT_MAX_CHARS))
    return "\n\n".join(parts)


def _collect_sources(segments: list[dict], schema_hits: list[dict]) -> list[dict]:
    """从合并片段与表结构块去重收集来源文档（保序）"""
    seen: set[int] = set()
    sources: list[dict] = []
    for h in [*segments, *schema_hits]:
        did = h.get("doc_id")
        if did is None or did in seen:
            continue
        seen.add(did)
        sources.append({"doc_id": did, "doc_title": h.get("doc_title", "")})
    return sources


def build_rag_context(db: Session, user_id: int, query: str) -> str:
    """
    双路检索注入：普通知识（块召回→相邻合并）+ 表结构（块召回）。
    文本首行带【知识库检索统计】头，方便 LLM 感知命中量级。
    """
    segments, schema_hits = _retrieve_context(db, user_id, query)
    return _build_rag_text(segments, schema_hits)


def build_rag_context_detailed(db: Session, user_id: int, query: str) -> tuple[str, list[dict]]:
    """同 build_rag_context，额外返回命中来源（含 doc_id/doc_title，去重保序，供引用标注与文档链接）"""
    segments, schema_hits = _retrieve_context(db, user_id, query)
    text = _build_rag_text(segments, schema_hits)
    sources = _collect_sources(segments, schema_hits)
    return text, sources
