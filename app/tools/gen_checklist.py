"""
工具 - gen_checklist：巡检清单生成
返回：{"items": [{"item", "method", "standard"}, ...]}
"""
from app.agent.context import build_rag_context
from app.core.llm import llm_client
from app.tools import prompts


def run(topic: str = "Oracle 数据库与医院接口日常巡检") -> dict:
    """生成巡检清单（可指定主题，如 表空间/接口积压/备份）"""
    rag_context = build_rag_context(f"巡检 {topic}")
    prompt = prompts.GEN_CHECKLIST_PROMPT.format(topic=topic, rag_context=rag_context)
    out = llm_client.structured_output([{"role": "user", "content": prompt}])
    items = out.get("items") if isinstance(out, dict) else None
    return {"items": items or []}
