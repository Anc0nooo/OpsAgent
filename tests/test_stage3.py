"""
阶段 3 单元测试：SQLGuard 强化 + 工具模块（离线，无需 API-KEY）
运行命令（项目根目录）：
    .venv\\Scripts\\python.exe tests\\test_stage3.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.sql_guard import sql_guard  # noqa: E402
from app.tools.query_db import _parse_columns, run as query_db_run  # noqa: E402
from app.tools.registry import TOOLS  # noqa: E402

passed = failed = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global passed, failed
    if cond:
        passed += 1
        print(f"  ✅ {name} {detail}")
    else:
        failed += 1
        print(f"  ❌ {name} {detail}")


OK, REJECT = True, False

print("\n== SQLGuard：合法只读通过 ==")
cases_ok = [
    ("简单查询", "SELECT 1 FROM dual"),
    ("标准业务查询", "SELECT ID, PAT_DEPT FROM LIS_LIFEALERT WHERE ROWNUM <= 10"),
    ("CTE 查询", "WITH t AS (SELECT ID FROM LIS_LIFEALERT) SELECT * FROM t"),
    ("FETCH FIRST 分页", "SELECT * FROM LIS_LIFEALERT ORDER BY ID FETCH FIRST 30 ROWS ONLY"),
    ("函数使用", "SELECT TO_CHAR(SYSDATE, 'YYYY-MM-DD'), NVL(PAT_DEPT, '未知') FROM LIS_LIFEALERT"),
    ("大小写混合", "select dept_id, count(*) from lis_lifealert group by dept_id"),
    ("双引号别名", 'SELECT ID AS "编号" FROM LIS_LIFEALERT'),
    ("字符串含写词（数据非命令）", "SELECT * FROM LIS_LIFEALERT WHERE NOTE = 'please delete me'"),
    ("普通行注释", "SELECT ID FROM LIS_LIFEALERT -- 查询编号\nWHERE ROWNUM <= 5"),
    ("普通块注释", "SELECT /* 查询标识 */ ID FROM LIS_LIFEALERT"),
]
for name, sql in cases_ok:
    r = sql_guard.validate(sql)
    check(name, r.ok == OK, f"({r.reason})")

print("\n== SQLGuard：写操作拦截 ==")
cases_reject = [
    ("UPDATE", "UPDATE LIS_LIFEALERT SET PAT_DEPT='x' WHERE ID=1"),
    ("DELETE", "DELETE FROM LIS_LIFEALERT WHERE ID=1"),
    ("INSERT", "INSERT INTO LIS_LIFEALERT(ID) VALUES('1')"),
    ("DROP", "DROP TABLE LIS_LIFEALERT"),
    ("TRUNCATE", "TRUNCATE TABLE LIS_LIFEALERT"),
    ("ALTER", "ALTER TABLE LIS_LIFEALERT ADD COL VARCHAR2(10)"),
    ("MERGE", "MERGE INTO LIS_LIFEALERT t USING tmp s ON (t.ID=s.ID) WHEN MATCHED THEN UPDATE SET t.PAT_DEPT='x'"),
    ("GRANT", "GRANT SELECT ON LIS_LIFEALERT TO user1"),
    ("小写混合绕过尝试", "update LIS_LIFEALERT set PAT_DEPT='x'"),
    ("非 SELECT 开头", "EXEC LIS_LIFEALERT_PROC"),
    ("空 SQL", "   ;  "),
    ("多语句", "SELECT 1 FROM dual; DELETE FROM LIS_LIFEALERT"),
]
for name, sql in cases_reject:
    r = sql_guard.validate(sql)
    check(name, r.ok == REJECT, f"({r.reason})")

print("\n== SQLGuard：伪写 / PL-SQL 拦截 ==")
cases_pseudo = [
    ("FOR UPDATE", "SELECT ID FROM LIS_LIFEALERT WHERE ID=1 FOR UPDATE"),
    ("SELECT INTO", "SELECT ID INTO v_id FROM LIS_LIFEALERT WHERE ROWNUM=1"),
    ("EXECUTE IMMEDIATE", "SELECT * FROM LIS_LIFEALERT; EXECUTE IMMEDIATE 'DROP TABLE x'"),
    ("DBMS_ 调用", "SELECT DBMS_RANDOM.VALUE FROM dual"),
    ("UTL_ 调用", "SELECT UTL_FILE.FOPEN('D','f','r') FROM dual"),
    ("PL/SQL BEGIN 块", "BEGIN DELETE FROM LIS_LIFEALERT; END;"),
    ("DECLARE 块", "DECLARE v_id VARCHAR2(20); BEGIN SELECT ID INTO v_id FROM LIS_LIFEALERT; END;"),
]
for name, sql in cases_pseudo:
    r = sql_guard.validate(sql)
    check(name, r.ok == REJECT, f"({r.reason})")

print("\n== SQLGuard：注释藏写 / MySQL 语法拦截 ==")
cases_bypass = [
    ("行注释藏写", "SELECT ID FROM LIS_LIFEALERT WHERE ROWNUM<=5 -- DELETE FROM secret_tab"),
    ("块注释藏写", "SELECT ID /* DROP TABLE LIS_LIFEALERT */ FROM LIS_LIFEALERT"),
    ("MySQL LIMIT", "SELECT * FROM LIS_LIFEALERT LIMIT 10"),
    ("MySQL IFNULL", "SELECT IFNULL(PAT_DEPT, 'x') FROM LIS_LIFEALERT"),
    ("MySQL DATE_FORMAT", "SELECT DATE_FORMAT(REPORT_TIME, '%Y-%m-%d') FROM LIS_LIFEALERT"),
    ("MySQL NOW()", "SELECT NOW() FROM dual"),
    ("MySQL GROUP_CONCAT", "SELECT GROUP_CONCAT(ID) FROM LIS_LIFEALERT"),
    ("MySQL 反引号", "SELECT `ID` FROM LIS_LIFEALERT"),
    ("CONCAT 三参", "SELECT CONCAT('a', PAT_DEPT, 'b') FROM LIS_LIFEALERT"),
]
for name, sql in cases_bypass:
    r = sql_guard.validate(sql)
    check(name, r.ok == REJECT, f"({r.reason})")

print("\n== SQLGuard：explain() 可读报告 ==")
rep = sql_guard.explain("SELECT 1 FROM dual; DELETE FROM LIS_LIFEALERT")
check("explain 拒绝报告可读", rep.startswith("❌") and "拒绝" in rep, f"({rep})")
rep2 = sql_guard.explain("SELECT ID FROM LIS_LIFEALERT WHERE ROWNUM<=1")
check("explain 通过报告可读", rep2.startswith("✅"))

print("\n== query_db：仿真执行（离线） ==")
r = query_db_run("SELECT PAT_DEPT AS dept, COUNT(*) AS cnt FROM LIS_LIFEALERT GROUP BY PAT_DEPT FETCH FIRST 3 ROWS ONLY")
check("校验+执行成功", r["ok"] and r["simulated"])
check("列名解析（别名）", r["columns"] == ["DEPT", "CNT"], f"columns={r['columns']}")
check("行数受 FETCH FIRST 约束", len(r["rows"]) == 3, f"rows={len(r['rows'])}")
check("耗时字段", isinstance(r["elapsed_ms"], int) and r["elapsed_ms"] >= 0)

r2 = query_db_run("UPDATE LIS_LIFEALERT SET PAT_DEPT='x'")
check("写 SQL 拒绝执行", not r2["ok"] and "只读校验" in r2["error"])

cols = _parse_columns("SELECT a.ID, NVL(b.NAME, 'x') AS name, TO_CHAR(t.T, 'YYYY') FROM t1 a JOIN t2 b ON a.ID=b.ID")
check("复杂列解析（函数/表前缀/别名）", cols == ["ID", "NAME", "T"], f"cols={cols}")
check("SELECT col INTO 已拦截", not sql_guard.validate("SELECT ID INTO v_id FROM LIS_LIFEALERT WHERE ROWNUM=1").ok)

print("\n== 工具注册表 ==")
check("5 个工具注册", set(TOOLS.keys()) == {"query_db", "gen_sql", "gen_steps", "gen_checklist", "explain_doc"}, f"keys={list(TOOLS.keys())}")
check("工具均有 desc 与 run", all(isinstance(t.get("desc"), str) and callable(t.get("run")) for t in TOOLS.values()))

print(f"\n阶段 3 单测结果：{passed}/{passed + failed} 通过")
sys.exit(0 if failed == 0 else 1)
