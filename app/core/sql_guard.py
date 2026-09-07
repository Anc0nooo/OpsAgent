"""
只读 SQL 校验器 ReadOnlySQLGuard（阶段 3 强化版，Oracle 方言）

校验层次（任一失败即拒绝，explain() 返回可读原因）：
1. 空语句 / 多语句拦截；
2. 仅允许 SELECT / WITH 开头（拦 UPDATE/DELETE/INSERT/DROP/PL-SQL 块等）；
3. 剥离字符串字面量后做写操作关键字词边界匹配（防止 'please delete me' 之类数据误伤/绕过）；
4. 注释藏写语句拦截：-- 单行注释、/* */ 块注释内的写关键字同样视为违规（防绕过）；
5. Oracle 伪写/危险操作：FOR UPDATE / SELECT INTO / EXECUTE IMMEDIATE / DBMS_* / UTL_* / BEGIN..END 块；
6. MySQL 语法拦截：LIMIT / IFNULL / DATE_FORMAT / NOW() / GROUP_CONCAT / 反引号 / CONCAT 三参及以上。
"""


class GuardResult:
    """校验结果：ok + 可读原因"""

    def __init__(self, ok: bool, reason: str = "") -> None:
        self.ok = ok
        self.reason = reason


import re


class ReadOnlySQLGuard:
    """只读 SQL 校验器（白名单 + 多层黑名单，Oracle 方言）"""

    # 写操作关键字（词边界匹配，作用于剥离字符串后的正文与注释）
    WRITE_KEYWORDS = [
        "UPDATE", "DELETE", "INSERT", "DROP", "TRUNCATE", "ALTER", "MERGE",
        "GRANT", "REVOKE", "CREATE", "RENAME", "AUDIT", "NOAUDIT",
        "LOCK", "COMMIT", "ROLLBACK", "SAVEPOINT",
    ]
    # Oracle 伪写 / 危险操作（子串匹配）
    PSEUDO_WRITE_KEYWORDS = ["FOR UPDATE", "SELECT INTO", "EXECUTE IMMEDIATE", "DBMS_", "UTL_"]
    # PL/SQL 块特征（词边界）
    PLSQL_KEYWORDS = ["BEGIN", "DECLARE", "EXCEPTION"]
    # MySQL 特有语法（词边界 / 特征）
    MYSQL_KEYWORDS = ["LIMIT", "IFNULL", "DATE_FORMAT", "GROUP_CONCAT", "UNIX_TIMESTAMP"]
    MYSQL_FUNCTIONS = ["NOW(", "CURDATE(", "STR_TO_DATE("]

    # 正则预编译
    _RE_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
    _RE_LINE_COMMENT = re.compile(r"--.*$", re.MULTILINE)
    _RE_STRING = re.compile(r"'(?:[^']|'')*'")  # Oracle 字符串字面量（含 '' 转义）
    _RE_BACKTICK = re.compile(r"`")
    _RE_CONCAT_MULTI = re.compile(r"CONCAT\s*\([^()]*(?:\([^()]*\)[^()]*)*,[^()]*,[^()]*\)", re.IGNORECASE)

    # ----------------------------------------------------------------------
    def validate(self, sql: str) -> GuardResult:
        """入口：返回 GuardResult(ok, reason)"""
        if not sql or not sql.strip(" \t\n\r;"):
            return GuardResult(False, "SQL 为空")

        raw = sql.strip()
        # 1. 多语句拦截（分号分隔多条非空语句）
        parts = [p.strip() for p in raw.rstrip(";").split(";") if p.strip()]
        if len(parts) > 1:
            return GuardResult(False, f"禁止多语句 SQL（检测到 {len(parts)} 条语句）")
        s = parts[0] if parts else raw.rstrip(";")

        # 2. 必须以 SELECT / WITH 开头（忽略前导块注释与空白）
        probe = self._RE_BLOCK_COMMENT.sub(" ", s).strip()
        upper_probe = probe.upper()
        if not (upper_probe.startswith("SELECT") or upper_probe.startswith("WITH")):
            return GuardResult(False, "仅允许 SELECT / WITH 开头的只读查询（PL/SQL 块、DML 均被禁止）")

        # 3. 提取注释与字符串，分别校验
        comments = self._extract_comments(s)
        # 正文（去注释、去字符串字面量）：用于关键字匹配
        body = self._RE_LINE_COMMENT.sub(" ", self._RE_BLOCK_COMMENT.sub(" ", s))
        body_no_str = self._RE_STRING.sub("''", body)
        body_upper = body_no_str.upper()

        # 4. 注释内藏写语句拦截
        for c in comments:
            c_upper = c.upper()
            for kw in self.WRITE_KEYWORDS:
                if re.search(rf"\b{kw}\b", c_upper):
                    return GuardResult(False, f"注释中检测到写操作关键字 {kw}（疑似绕过校验）")
            for kw in self.PSEUDO_WRITE_KEYWORDS:
                if kw in c_upper:
                    return GuardResult(False, f"注释中检测到伪写操作 {kw}（疑似绕过校验）")

        # 5. 正文写操作（词边界）
        for kw in self.WRITE_KEYWORDS:
            if re.search(rf"\b{kw}\b", body_upper):
                return GuardResult(False, f"检测到写操作关键字: {kw}")

        # 6. 伪写 / 危险调用
        for kw in self.PSEUDO_WRITE_KEYWORDS:
            if kw in body_upper:
                return GuardResult(False, f"检测到 Oracle 伪写/危险操作: {kw}")
        # SELECT col INTO var（INTO 出现在 SELECT 正文即伪写；INSERT..SELECT 已被写关键字拦截）
        if re.search(r"\bINTO\b", body_upper):
            return GuardResult(False, "检测到 INTO 子句（SELECT INTO 伪写操作）")
        for kw in self.PLSQL_KEYWORDS:
            if re.search(rf"\b{kw}\b", body_upper):
                return GuardResult(False, f"检测到 PL/SQL 块特征: {kw}")

        # 7. MySQL 语法拦截（Oracle 方言锁定）
        if self._RE_BACKTICK.search(body):
            return GuardResult(False, "检测到 MySQL 反引号标识符（Oracle 使用双引号）")
        for kw in self.MYSQL_KEYWORDS:
            if re.search(rf"\b{kw}\b", body_upper):
                return GuardResult(False, f"检测到 MySQL 语法 {kw}（请改用 Oracle 写法，如 FETCH FIRST / NVL / TO_CHAR）")
        for fn in self.MYSQL_FUNCTIONS:
            if fn.upper() in body_upper:
                oracle_hint = {"NOW(": "SYSDATE", "CURDATE(": "TRUNC(SYSDATE)", "STR_TO_DATE(": "TO_DATE"}.get(fn.upper(), "Oracle 函数")
                return GuardResult(False, f"检测到 MySQL 函数 {fn.rstrip('(')}()（请改用 {oracle_hint}）")
        if self._RE_CONCAT_MULTI.search(body):
            return GuardResult(False, "检测到 CONCAT 多参数用法（MySQL 风格；Oracle 的 CONCAT 仅支持两参，请用 || 拼接）")

        return GuardResult(True, "只读校验通过")

    # ----------------------------------------------------------------------
    def explain(self, sql: str) -> str:
        """可读校验报告（通过 / 拒绝原因）"""
        r = self.validate(sql)
        return f"✅ 通过：{r.reason}" if r.ok else f"❌ 拒绝：{r.reason}"

    # ----------------------------------------------------------------------
    @staticmethod
    def _extract_comments(sql: str) -> list[str]:
        """提取全部注释内容（块注释 + 行注释）"""
        comments: list[str] = []
        for m in ReadOnlySQLGuard._RE_BLOCK_COMMENT.finditer(sql):
            comments.append(m.group(0))
        # 行注释：先去掉块注释避免重复统计
        no_block = ReadOnlySQLGuard._RE_BLOCK_COMMENT.sub(" ", sql)
        for m in ReadOnlySQLGuard._RE_LINE_COMMENT.finditer(no_block):
            comments.append(m.group(0))
        return comments


# 全局单例
sql_guard = ReadOnlySQLGuard()
