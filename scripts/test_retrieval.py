"""
RAG 检索效果测试脚本（FR-RAG-07）
抽样 10 个运维问题，调用混合检索，输出每个问题的 top-5 命中与来源。

运行命令（在项目根目录）：
    .venv\\Scripts\\python.exe scripts\\test_retrieval.py

前置：.env 已配置 DASHSCOPE_API_KEY（检索依赖 embedding 与 rerank）。
"""
import sys
from pathlib import Path

# 保证可从项目根目录导入 app 包
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config.settings import settings  # noqa: E402
from app.rag.service import knowledge_service  # noqa: E402

# 抽样测试问题（覆盖 ORA 错误码、表名强关键词、语义类问题）
TEST_QUERIES = [
    "ORA-00054 资源忙怎么处理？",
    "ORA-01555 快照过旧的原因",
    "共享池内存不足报什么错",
    "表空间不足无法扩展怎么解决",
    "输血执行率怎么统计？",
    "今日危急值统计 SQL",
    "SQL 突然变慢怎么排查？",
    "LIS 接口一直报错怎么查",
    "账户被锁定 ORA-28000",
    "Oracle 分页查询怎么写？",
]


def main() -> None:
    # 初始化（建库/索引/starter，已初始化过则幂等）
    knowledge_service.initialize()

    from app.rag.db import all_chunks, list_docs

    docs = list_docs()
    chunks = all_chunks()
    print(f"\n知识库现状：文档 {len(docs)} 篇，知识块 {len(chunks)} 个")
    print(f"检索配置：BM25权重={settings.BM25_WEIGHT} 向量权重={settings.VECTOR_WEIGHT} 候选数={settings.RETRIEVAL_TOP_N}")
    print("=" * 80)

    hit_total = 0
    for q in TEST_QUERIES:
        results = knowledge_service.retrieve(q, top_k=5)
        print(f"\n问题：{q}")
        if not results:
            print("  （无命中）")
            continue
        for i, r in enumerate(results, 1):
            title = r["doc_title"] or "(无标题)"
            section = r["section"] or "-"
            print(f"  {i}. [{r['score']:.4f}] {title} | {section} | {r['doc_type']}")
        # 命中判定：问题分词（中文 bigram/英文整词）与 top5 标题+章节求交集，存在交集即命中
        from app.rag.bm25 import tokenize
        key_tokens = set(tokenize(q.replace("？", "")))
        hit = any(
            key_tokens & set(tokenize((r["doc_title"] or "") + (r["section"] or "")))
            for r in results
        )
        hit_total += 1 if hit else 0
        print(f"  命中判定：{'✅ 命中' if hit else '❌ 未命中'}")

    print("\n" + "=" * 80)
    print(f"测试结果：{hit_total}/{len(TEST_QUERIES)} 命中（命中率 {hit_total / len(TEST_QUERIES) * 100:.0f}%，目标 ≥ 70%）")


if __name__ == "__main__":
    main()
