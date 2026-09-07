"""
工具注册表：TOOLS = {name: {"desc", "run"}}
规划器与 API 通过 TOOLS 调用；新增工具在此登记即可。
"""
from app.tools import explain_doc, gen_checklist, gen_sql, gen_steps, query_db

TOOLS: dict[str, dict] = {
    "query_db": {
        "desc": "只读 SQL 仿真执行（本地 mock，不直连生产库）。参数：sql, max_rows=5",
        "run": query_db.run,
    },
    "gen_sql": {
        "desc": "自然语言需求 → Oracle 只读 SQL（RAG 表结构注入 + 只读校验）。参数：requirement",
        "run": gen_sql.run,
    },
    "gen_steps": {
        "desc": "排障步骤生成（结构化步骤列表）。参数：problem",
        "run": gen_steps.run,
    },
    "gen_checklist": {
        "desc": "巡检清单生成。参数：topic（可选，默认日常巡检）",
        "run": gen_checklist.run,
    },
    "explain_doc": {
        "desc": "基于知识库命中的引用式解释（带来源）。参数：question, top_k=3",
        "run": explain_doc.run,
    },
}
