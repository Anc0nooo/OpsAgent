"""
工具调用模块（app/tools/）
③工具调用：方案生成 + 只读查询。全部工具通过 registry.TOOLS 注册暴露。
- query_db      只读 SQL 仿真执行（本地 mock，绝不直连生产库）
- gen_sql       自然语言 → Oracle 只读 SQL（RAG 表结构注入 + guard 校验）
- gen_steps     排障步骤生成（结构化）
- gen_checklist 巡检清单生成
- explain_doc   基于 RAG 命中内容的引用式解释
"""
from app.tools.registry import TOOLS  # noqa: F401
