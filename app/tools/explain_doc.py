"""
工具 - explain_doc：基于 RAG 命中内容的引用式解释（带来源标注，不编造）
返回：{"content": markdown 文本, "sources": [文档标题, ...]}
"""
from app.agent.context import build_rag_context
from app.core.llm import llm_client
from app.rag.service import knowledge_service
from app.tools import prompts


def run(question: str, top_k: int = 3) -> dict:
    """检索并解释（引用来源）"""
    hits = knowledge_service.retrieve(question, top_k=top_k)
    rag_context = build_rag_context(question)
    prompt = prompts.EXPLAIN_DOC_PROMPT.format(question=question, rag_context=rag_context)
    content = llm_client.chat([{"role": "user", "content": prompt}])
    sources = list({h["doc_title"] for h in hits})
    return {"content": content, "sources": sources}
