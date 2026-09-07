"""
工具 - query_db：只读 SQL 仿真执行器
阶段 3 行为：本地 mock 执行（解析列名 → 生成占位数据行），绝不直连生产库、绝不真实执行。
用途：本地联调"生成 SQL → 校验 → 执行 → 回传"闭环链路；后续如需真实库，由用户显式提供只读连接串并替换 _simulate 实现。
返回：{"columns": [...], "rows": [[...], ...], "elapsed_ms": n, "simulated": True}
"""
import re
import time

from app.core.sql_guard import sql_guard

# 匹配 SELECT 列清单（FROM 之前），含别名与表达式
_RE_SELECT_LIST = re.compile(
    r"^\s*(?:SELECT|WITH[\s\S]*?\))\s+(.+?)\s+FROM\s", re.IGNORECASE | re.DOTALL
)
_RE_FETCH_FIRST = re.compile(r"FETCH\s+FIRST\s+(\d+)\s+ROWS?\s+ONLY", re.IGNORECASE)


def _parse_columns(sql: str) -> list[str]:
    """解析 SELECT 列名（别名优先，统一大写，兼容函数列/表前缀），失败时返回占位列名"""
    m = _RE_SELECT_LIST.search(sql)
    cols: list[str] = []
    if m:
        raw = m.group(1)
        # 简易逗号分割（忽略括号内逗号，如 NVL(a,b)）
        depth, cur = 0, []
        segments: list[str] = []
        for ch in raw:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            if ch == "," and depth == 0:
                segments.append("".join(cur))
                cur = []
            else:
                cur.append(ch)
        if cur:
            segments.append("".join(cur))
        for seg in segments:
            seg = seg.strip()
            # 优先取 AS 别名
            am = re.search(r"\bAS\s+([A-Za-z_]\w*)\s*$", seg, re.IGNORECASE)
            if am:
                cols.append(am.group(1).upper())
                continue
            fm2 = re.match(r"^[A-Za-z_]\w*\s*\(", seg)
            if fm2:
                # 函数列：取第一个参数的尾部标识符（如 TO_CHAR(t.T,'YYYY') → T）
                first_arg = re.split(r"[,)]", seg[fm2.end():], maxsplit=1)[0]
                idents = re.findall(r"[A-Za-z_]\w*", first_arg)
            else:
                # 普通列/裸别名：取最后一个标识符（兼容 表.列）
                idents = re.findall(r"[A-Za-z_]\w*", seg)
            if idents:
                cols.append(idents[-1].upper())
            else:
                cols.append(f"COL_{len(cols) + 1}")
    if not cols:
        cols = ["COL_1", "COL_2"]
    return cols


def _mock_value(col: str, row_idx: int) -> object:
    """按列名启发式生成占位值（仿真数据，非真实结果）"""
    c = col.upper()
    if "CNT" in c or "COUNT" in c or "NUM" in c or "TOTAL" in c:
        return (row_idx + 1) * 13
    if "TIME" in c or "DATE" in c:
        return f"2026-08-31 0{row_idx + 1}:12:00"
    if "STATUS" in c or "STATE" in c or "FLAG" in c:
        return ["正常", "待处理", "失败"][row_idx % 3]
    if "NAME" in c or "DEPT" in c or "ITEM" in c:
        return f"{c}_{row_idx + 1}（脱敏）"
    return f"值{row_idx + 1}"


def run(sql: str, max_rows: int = 5) -> dict:
    """
    执行只读查询（仿真）。
    流程：guard 强校验 → 解析列名 → mock 数据行（受 FETCH FIRST/ROWNUM 与 max_rows 约束）。
    """
    guard = sql_guard.validate(sql)
    if not guard.ok:
        return {"ok": False, "error": f"只读校验未通过：{guard.reason}"}

    cols = _parse_columns(sql)
    fm = _RE_FETCH_FIRST.search(sql)
    rm = re.search(r"ROWNUM\s*<=\s*(\d+)", sql, re.IGNORECASE)
    limit = min(int(fm.group(1)) if fm else (int(rm.group(1)) if rm else max_rows), max_rows)

    t0 = time.perf_counter()
    time.sleep(0.01)  # 模拟网络/执行延迟
    rows = [[_mock_value(c, i) for c in cols] for i in range(limit)]
    elapsed = int((time.perf_counter() - t0) * 1000)

    return {
        "ok": True,
        "columns": cols,
        "rows": rows,
        "elapsed_ms": elapsed,
        "simulated": True,  # 明示仿真数据
        "guard": guard.reason,
    }
