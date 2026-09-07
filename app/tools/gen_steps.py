"""
工具 - gen_steps：排障步骤生成（结构化）
返回：{"steps": [{"title", "detail", "command", "risk"}, ...]}
"""
from app.agent.context import build_rag_context
from app.core.llm import llm_client
from app.tools import prompts


def run(problem: str) -> dict:
    """生成结构化排障步骤"""
    rag_context = build_rag_context(problem)
    prompt = prompts.GEN_STEPS_PROMPT.format(problem=problem, rag_context=rag_context)
    out = llm_client.structured_output([{"role": "user", "content": prompt}])
    steps = out.get("steps") if isinstance(out, dict) else None
    return {"steps": steps or []}
