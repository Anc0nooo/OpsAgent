"""
阶段 1 API 冒烟测试（需后端已启动且配置 API-KEY）：
1. 检索接口（验证 rerank 生效：score 为相关性分数）；
2. 表结构导入（DDL → 表结构文档）；
3. 文本入库 + 增量追加（块数累加）+ 删除清理。

运行命令（项目根目录，另开终端先启动后端）：
    .venv\\Scripts\\python.exe scripts\\test_stage1_api.py
"""
import sys
from pathlib import Path

import httpx

BASE = "http://127.0.0.1:8000/api/knowledge"

passed = failed = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global passed, failed
    if cond:
        passed += 1
        print(f"  ✅ {name} {detail}")
    else:
        failed += 1
        print(f"  ❌ {name} {detail}")


def main() -> None:
    c = httpx.Client(timeout=60)

    print("\n[1. 检索接口（rerank 生效性）]")
    r = c.post(f"{BASE}/search", json={"query": "ORA-01555 快照过旧", "top_k": 3}).json()
    results = r.get("data", [])
    check("检索返回 top3", len(results) == 3)
    check("首条为 ORA-01555 文档", bool(results) and "ORA-01555" in results[0]["doc_title"],
          f"top1={results[0]['doc_title'] if results else '无'}")
    # rerank 分数特征：相关性分数（快照过旧应 >0.5，融合分很少到 0.9+）
    check("rerank 生效（top1 分数 > 0.5）", bool(results) and results[0]["score"] > 0.5,
          f"score={results[0]['score'] if results else '无'}")

    print("\n[2. 表结构导入（DDL）]")
    ddl = "CREATE TABLE LIS_LIFEALERT (ID VARCHAR2(20) NOT NULL, PAT_DEPT VARCHAR2(50), REPORT_TIME DATE, ITEM_NAME VARCHAR2(100));"
    r = c.post(f"{BASE}/table_schema", json={"content": ddl}).json()
    doc_ids = r.get("data", {}).get("doc_ids", [])
    check("导入成功", r.get("code") == 0 and len(doc_ids) == 1, str(r.get("message")))

    # 验证按表名可检索到字段
    r = c.post(f"{BASE}/search", json={"query": "LIS_LIFEALERT 表结构字段", "doc_type": "表结构", "top_k": 3}).json()
    hit = any("LIS_LIFEALERT" in (x["doc_title"] or "") and "PAT_DEPT" in x["text"] for x in r.get("data", []))
    check("按表名检索到字段", hit)

    print("\n[3. 文本入库 + 增量追加 + 删除]")
    r = c.post(f"{BASE}/text", json={"title": "冒烟测试文档", "text": "这是第一段测试内容。", "doc_type": "操作文档"}).json()
    did = r["data"]["doc_id"]
    check("文本入库", r.get("code") == 0)
    c.post(f"{BASE}/text", json={"title": "冒烟测试文档", "text": "追加的第二段内容。", "doc_type": "操作文档", "append_doc_id": did})
    docs = c.get(f"{BASE}/docs").json()["data"]
    doc = next(d for d in docs if d["id"] == did)
    check("增量追加块数累加", doc["chunk_count"] == 2, f"chunk_count={doc['chunk_count']}")
    r = c.delete(f"{BASE}/docs/{did}").json()
    check("删除文档", r.get("code") == 0)
    after = c.get(f"{BASE}/docs").json()["data"]
    check("删除后无残留", all(d["id"] != did for d in after))

    print(f"\n冒烟测试结果：{passed}/{passed + failed} 通过")
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
