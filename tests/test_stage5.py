"""
阶段 5 单元测试：结果输出模块（formatter + exporter，离线无需 API-KEY）
运行命令（项目根目录）：
    .venv\\Scripts\\python.exe tests\\test_stage5.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.output.exporter import export_csv, export_markdown, export_sql  # noqa: E402
from app.output.formatter import attach_sources, build_query_card, build_summary_sections  # noqa: E402

passed = failed = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global passed, failed
    if cond:
        passed += 1
        print(f"  ✅ {name} {detail}")
    else:
        failed += 1
        print(f"  ❌ {name} {detail}")


print("\n== formatter：四段总结解析 ==")
MD = """## 结论
问题定位为 undo 表空间不足。

## 原因分析
长时间查询导致 undo 保留不足，ORA-01555 出现。

### 处理步骤
1. 扩容 undo 表空间；
2. 优化慢查询。

**风险与回滚**
扩容需注意磁盘余量；回滚方案见知识库。
"""
s = build_summary_sections(MD)
check("结论段解析", s["conclusion"] is not None and "undo 表空间不足" in s["conclusion"])
check("原因段解析", s["analysis"] is not None and "ORA-01555" in s["analysis"])
check("步骤段解析（### 级标题）", s["steps"] is not None and "扩容 undo" in s["steps"])
check("风险段解析（**加粗** 标题）", s["risks"] is not None and "磁盘余量" in s["risks"])
check("raw 保留全文", s["raw"] == MD)

s2 = build_summary_sections("没有任何标题的普通文本方案。")
check("无标题文本整体归入结论", s2["conclusion"] and "普通文本" in s2["conclusion"]
      and s2["analysis"] is None and s2["steps"] is None and s2["risks"] is None)

s3 = build_summary_sections("")
check("空文本解析安全", s3["conclusion"] is None)

print("\n== formatter：查询卡片 ==")
card = build_query_card({"sql": "SELECT 1 FROM dual", "purpose": "测试", "round": 2})
check("卡片字段完整", card["sql"] == "SELECT 1 FROM dual" and card["round"] == 2 and card["max_rounds"] == 5)
check("含脱敏提示", "脱敏" in card["notice"] and "患者" in card["notice"])

print("\n== formatter：来源附加 ==")
out = attach_sources("方案正文。", ["Oracle 错误码手册", "Oracle 错误码手册", "巡检指南"])
check("来源去重保序", out.count("Oracle 错误码手册") == 1 and out.index("巡检指南") > out.index("Oracle 错误码手册"))
check("无来源原样返回", attach_sources("方案正文。", []) == "方案正文。")

print("\n== exporter：md 导出 ==")
md = export_markdown("## 结论\n测试正文", {"title": "测试会话", "session_id": "abc123", "model": "qwen-plus"})
check("md 含标题与元信息", "# 测试会话" in md and "abc123" in md and "qwen-plus" in md)
check("md 含正文", "测试正文" in md and md.endswith("\n"))

print("\n== exporter：csv 导出 ==")
csv_text = export_csv(["科室", "数量"], [["急诊", "123"], ["门诊", "45"]])
check("csv 含 BOM", csv_text.startswith("\ufeff"))
check("csv 行内容正确", "科室,数量" in csv_text and "急诊,123" in csv_text and "门诊,45" in csv_text)

print("\n== exporter：sql 导出 ==")
sql_text = export_sql({"sql": "SELECT * FROM t WHERE ROWNUM<=30;", "purpose": "查询积压", "round": 1})
check("sql 注释头完整", "-- OpsAgent 只读查询导出" in sql_text and "查询积压" in sql_text)
check("sql 含脱敏提醒", "脱敏" in sql_text and "患者" in sql_text)
check("sql 尾分号统一（去重加）", sql_text.rstrip().endswith(";")
      and "ROWNUM<=30;" in sql_text and not "ROWNUM<=30;;" in sql_text)

print(f"\n阶段 5 单测结果：{passed}/{passed + failed} 通过")
sys.exit(0 if failed == 0 else 1)
