"""
结果输出 - 格式化器
1. build_summary_sections：把方案 markdown 解析为四段总结结构（结论/原因分析/处理步骤/风险提示），
   供前端渲染四段卡片；解析不出标题时整体归入结论段（不丢内容）。
2. build_query_card：挂起查询 → 查询卡片结构（SQL + 目的 + 脱敏提示）。
3. attach_sources：在方案末尾追加 RAG 命中来源引用列表。
"""
import re
from typing import Any

# 四段标题关键词（按优先级匹配 markdown 标题行 / 加粗行）
SECTION_PATTERNS: dict[str, list[str]] = {
    "conclusion": ["结论", "总结", "概要", "概述"],
    "analysis": ["原因", "分析", "背景", "排查思路"],
    "steps": ["处理步骤", "处理", "步骤", "操作", "解决方案", "解决", "措施"],
    "risks": ["风险", "回滚", "注意", "验证", "提示"],
}

_RE_HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+(.+)$")          # # 标题
_RE_BOLD = re.compile(r"^\s{0,3}\*\*(.+?)\*\*\s*:?\s*$")        # **加粗行**
_RE_NUMBER = re.compile(r"^\s{0,3}\d+[.、)）]\s+")               # 有序列表


def _match_section(title: str) -> str | None:
    """标题 → 四段之一（conclusion/analysis/steps/risks）"""
    t = title.strip().lower().rstrip(":：")
    for key, kws in SECTION_PATTERNS.items():
        for kw in kws:
            if kw in t:
                return key
    return None


def build_summary_sections(content: str) -> dict[str, str | None]:
    """
    方案 markdown → 四段结构。
    返回 {"conclusion": str|None, "analysis": ..., "steps": ..., "risks": ..., "raw": 全文}
    """
    sections: dict[str, list[str]] = {k: [] for k in SECTION_PATTERNS}
    current: str | None = "conclusion"  # 开头无标题的默认归入结论
    lines = content.split("\n")

    for line in lines:
        m = _RE_HEADING.match(line) or _RE_BOLD.match(line)
        if m:
            title = m.group(2) if _RE_HEADING.match(line) else m.group(1)
            key = _match_section(title)
            if key:
                current = key
                continue
        if current:
            sections[current].append(line)

    result: dict[str, str | None] = {}
    for key in ("conclusion", "analysis", "steps", "risks"):
        text = "\n".join(sections[key]).strip()
        result[key] = text or None
    # 四段全空（理论上不会发生）→ 整体归入结论
    if all(result[k] is None for k in SECTION_PATTERNS):
        result["conclusion"] = content.strip() or None
    result["raw"] = content
    return result


def build_query_card(pending: dict[str, Any]) -> dict[str, Any]:
    """挂起查询 → 查询卡片结构（含脱敏提示与回传上限）"""
    return {
        "sql": pending.get("sql", ""),
        "purpose": pending.get("purpose", ""),
        "round": pending.get("round", 1),
        "max_rounds": 5,
        "notice": "请人工执行（只读），结果脱敏后回传，不要粘贴完整患者信息，建议 ≤30 行",
    }


def attach_sources(content: str, sources: list[dict]) -> str:
    """方案末尾追加引用来源（去重保序）；无来源时原样返回

    sources: [{"doc_id": int, "doc_title": str}]，前端据此渲染可点击链接；
    正文里仅追加纯文本标题，保证导出 md/sql 时来源可见。
    """
    if not sources:
        return content
    titles = list(dict.fromkeys(s.get("doc_title", "") for s in sources if s.get("doc_title")))
    if not titles:
        return content
    lines = "\n".join(f"- {t}" for t in titles)
    return f"{content.rstrip()}\n\n---\n\n**引用来源**\n\n{lines}"
