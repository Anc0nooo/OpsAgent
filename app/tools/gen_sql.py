"""
工具 - gen_sql：自然语言需求 → Oracle 只读 SQL
流程：RAG 双路检索（表结构 + 知识）→ LLM 结构化生成 → guard 校验（不过反馈重试一次）→ 返回
返回：{"sql", "purpose", "notes", "guard_ok", "guard_reason"}
"""
import logging

from app.agent.context import build_rag_context
from app.core.llm import llm_client
from app.core.sql_guard import sql_guard
from app.tools import prompts

logger = logging.getLogger(__name__)


def run(requirement: str, max_retries: int = 1) -> dict:
    """生成只读 SQL（含校验与重试）"""
    # RAG：表结构优先注入（防臆造字段）
    rag_context = build_rag_context(requirement)
    # 拆分注入：context.py 的双段文本直接整体注入即可
    prompt = prompts.GEN_SQL_PROMPT.format(requirement=requirement, schema_context=rag_context, knowledge="（见上方表结构与知识）")
    messages = [{"role": "system", "content": "你是医院信息科 Oracle 运维专家。"}, {"role": "user", "content": prompt}]
    out = llm_client.structured_output(messages)

    for attempt in range(max_retries + 1):
        sql = str(out.get("sql", "")).strip()
        guard = sql_guard.validate(sql)
        if guard.ok:
            return {
                "sql": sql,
                "purpose": out.get("purpose", ""),
                "notes": out.get("notes", ""),
                "guard_ok": True,
                "guard_reason": guard.reason,
            }
        logger.warning("gen_sql 校验未通过（第 %d 次）：%s", attempt + 1, guard.reason)
        if attempt < max_retries:
            out = llm_client.structured_output([*messages, {"role": "user", "content": prompts.GEN_SQL_RETRY.format(reason=guard.reason, sql=sql)}])

    return {
        "sql": sql,
        "purpose": out.get("purpose", ""),
        "notes": out.get("notes", ""),
        "guard_ok": False,
        "guard_reason": guard.reason,
    }
