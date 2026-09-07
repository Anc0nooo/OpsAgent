"""
阶段 3 工具在线集成测试（需后端知识库已初始化 + API-KEY 有效）
覆盖：gen_sql（RAG+LLM+guard）、query_db（仿真）、gen_steps、gen_checklist、explain_doc。

运行命令（项目根目录）：
    .venv\\Scripts\\python.exe scripts\\test_tools.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.rag.service import knowledge_service  # noqa: E402
from app.tools import TOOLS  # noqa: E402

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
    knowledge_service.initialize()  # 脚本进程内初始化知识库（Chroma/BM25）
    print("\n[1] gen_sql：生成只读 SQL（表结构注入 + guard）")
    r = TOOLS["gen_sql"]["run"]("统计 LIS_LIFEALERT 表今天各科室的危急值数量，只看前10个科室")
    print(json.dumps(r, ensure_ascii=False, indent=1)[:600])
    check("guard 通过", r["guard_ok"], f"({r['guard_reason']})")
    check("Oracle 分页特征", "FETCH FIRST" in r["sql"].upper() or "ROWNUM" in r["sql"].upper())
    check("无 MySQL 语法", all(k not in r["sql"].upper() for k in ["LIMIT", "IFNULL", "DATE_FORMAT", "`"]))

    print("\n[2] query_db：仿真执行 gen_sql 产出的 SQL")
    r2 = TOOLS["query_db"]["run"](r["sql"])
    check("仿真执行成功", r2["ok"] and r2["simulated"])
    print(f"  列: {r2['columns']} 行数: {len(r2['rows'])} 耗时: {r2['elapsed_ms']}ms")

    print("\n[3] gen_steps：排障步骤")
    r3 = TOOLS["gen_steps"]["run"]("LIS 系统中午开始检验申请单下发失败，LIS_LIFEALERT 表插入报错 ORA-01653")
    steps = r3["steps"]
    check("步骤数 3~6", 3 <= len(steps) <= 6, f"n={len(steps)}")
    check("步骤字段完整", all(s.get("title") and s.get("detail") for s in steps))

    print("\n[4] gen_checklist：巡检清单")
    r4 = TOOLS["gen_checklist"]["run"]("Oracle 表空间与接口积压日常巡检")
    items = r4["items"]
    check("清单项数 6~10", 6 <= len(items) <= 10, f"n={len(items)}")
    check("含判定标准", all(i.get("standard") for i in items))

    print("\n[5] explain_doc：引用式解释")
    r5 = TOOLS["explain_doc"]["run"]("ORA-01555 快照过旧是什么原因")
    check("解释非空", len(r5["content"]) > 100)
    check("引用了来源", len(r5["sources"]) > 0, f"来源={r5['sources'][:3]}")
    check("内容含关键词", "undo" in r5["content"].lower() or "回滚" in r5["content"])

    print(f"\n工具在线测试结果：{passed}/{passed + failed} 通过")
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
