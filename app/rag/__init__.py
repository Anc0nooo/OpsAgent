"""
①RAG 知识库模块
- 文档管理与解析切分、向量化入库（Chroma）
- BM25 + 向量混合检索、gte-rerank 重排
- starter 知识包、表结构/字段字典导入
"""
from app.rag.service import knowledge_service  # noqa: F401 全局服务单例
