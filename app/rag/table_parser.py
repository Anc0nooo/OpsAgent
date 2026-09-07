"""
RAG 知识库 - 表结构导入解析器（FR-RAG-09）
支持两种粘贴格式，解析为结构化"表结构"知识：
1. DESC 输出（PL/SQL Developer / SQL*Plus 风格）；
2. 建表 DDL（CREATE TABLE ... + COMMENT ON COLUMN）。
解析结果生成规范化文本入库，供 gen_sql/query_db 生成 SQL 前核对表字段，避免臆造字段名。
"""
import re
from dataclasses import dataclass, field


@dataclass
class Column:
    """字段信息"""
    name: str
    type: str = ""
    nullable: bool = True
    default: str = ""
    comment: str = ""


@dataclass
class TableSchema:
    """表结构"""
    name: str
    columns: list[Column] = field(default_factory=list)

    def to_text(self) -> str:
        """生成入库文本（结构化 markdown，便于检索"按表名查字段"）"""
        lines = [f"# 表结构：{self.name}", "", "| 字段 | 类型 | 可空 | 默认值 | 说明 |", "|------|------|------|--------|------|"]
        for c in self.columns:
            lines.append(
                f"| {c.name} | {c.type} | {'是' if c.nullable else '否'} | {c.default} | {c.comment} |"
            )
        return "\n".join(lines)


# ----------------------------------------------------------------------
# DDL 解析
# ----------------------------------------------------------------------
# 字段行：列名 类型 [DEFAULT xx] [NOT NULL|NULL] [ENABLE]
_DDL_COL_RE = re.compile(
    r"^[\"`]?(\w+)[\"`]?\s+([A-Za-z0-9_]+(?:\s*\([^)]*\))?)"
    r"(?:\s+DEFAULT\s+([^\n]+?))?"
    r"(\s+NOT\s+NULL)?[^,]*,?\s*$",
    re.IGNORECASE,
)
_COMMENT_COL_RE = re.compile(
    r"COMMENT\s+ON\s+COLUMN\s+[\"`]?[\w\.]+[\"`]?\.[\"`]?(\w+)[\"`]?\s+IS\s+'([^']*)'",
    re.IGNORECASE,
)


def parse_ddl(content: str) -> list[TableSchema]:
    """
    从建表 DDL 中解析出全部表结构。
    按 CREATE TABLE 分段，段内取第一个 ( 到最后一个 ) 作为表体，
    避免字段类型中的括号（如 VARCHAR2(20)）导致括号配对提前截断。
    """
    schemas: list[TableSchema] = []
    # 每个表一段（前瞻切分，保留 CREATE TABLE 关键字）
    parts = re.split(r"(?=CREATE\s+TABLE)", content, flags=re.IGNORECASE)
    comments = {c.group(1).lower(): c.group(2) for c in _COMMENT_COL_RE.finditer(content)}

    for part in parts:
        head = re.match(r"\s*CREATE\s+TABLE\s+[\"`]?([\w\.]+)[\"`]?", part, re.IGNORECASE)
        if not head:
            continue
        table_name = head.group(1).split(".")[-1]
        p1 = part.find("(")
        p2 = part.rfind(")")
        if p1 == -1 or p2 <= p1:
            continue
        body = part[p1 + 1:p2]
        cols: list[Column] = []
        # 按逗号分段（兼容单行/多行 DDL），先保护类型括号内的逗号（如 NUMBER(10,2)）
        protected = re.sub(r"\(([^)]*)\)", lambda m: "(" + m.group(1).replace(",", "\x00") + ")", body)
        segments = [s.replace("\x00", ",").strip().rstrip(",") for s in protected.split(",")]
        for seg in segments:
            seg = seg.strip()
            if not seg:
                continue
            if seg.upper().startswith(("CONSTRAINT", "PRIMARY KEY", "FOREIGN KEY", "UNIQUE", "CHECK")):
                continue
            cm = _DDL_COL_RE.match(seg)
            if cm:
                cols.append(Column(
                    name=cm.group(1),
                    type=cm.group(2).replace(" ", ""),
                    nullable=not bool(cm.group(4)),
                    default=(cm.group(3) or "").strip().strip("'"),
                ))
        # 关联列注释
        for col in cols:
            col.comment = comments.get(col.name.lower(), "")
        if cols:
            schemas.append(TableSchema(name=table_name, columns=cols))
    return schemas


# ----------------------------------------------------------------------
# DESC 输出解析
# ----------------------------------------------------------------------
# 分隔线（PL/SQL Developer / SQL*Plus 的 ----- 行，多列以空格分隔）
_SEP_RE = re.compile(r"^[-\s]+$")


def parse_desc(content: str, table_name: str) -> TableSchema:
    """
    解析 DESC 输出为单表结构。
    兼容两种表头：
    - PL/SQL Developer：Name Type Nullable Default Comments
    - SQL*Plus：名称 是否为空? 类型（或 Name Null? Type）
    """
    schema = TableSchema(name=table_name)
    lines = [l for l in content.replace("\r\n", "\n").split("\n") if l.strip()]
    for line in lines:
        stripped = line.strip()
        # 分隔线：仅含 - 与空格
        if _SEP_RE.match(stripped) and "--" in stripped:
            continue
        # 表头行跳过（Name/名称 开头的表头特征）
        head = stripped.lower()
        if ("name" in head or "名称" in head) and ("type" in head or "类型" in head or "null" in head or "是否" in head):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        col_name = parts[0]
        # 兼容 SQL*Plus 与 PL/SQL Developer 两种风格
        nullable = True
        type_idx = 1
        if parts[1].upper() == "NOT" or parts[1] in ("是否为空?", "非空"):
            # SQL*Plus：第二列是 NOT NULL 标记，第三列才是类型
            nullable = False
            type_idx = 2
        elif parts[1].upper() in ("NULL", "Y", "YES"):
            # 第二列直接是可空标记
            nullable = True
            type_idx = 2
        elif len(parts) >= 3 and parts[2].upper() in ("Y", "N"):
            # PL/SQL Developer：类型后有 Nullable 标记列（Y=可空 N=不可空）
            nullable = parts[2].upper() != "N"
            type_idx = 1
        col_type = parts[type_idx] if type_idx < len(parts) else ""
        # 类型可能被空格拆开，如 NUMBER (10,2) / VARCHAR2 (20)
        if type_idx + 1 < len(parts) and parts[type_idx + 1].startswith("("):
            col_type += parts[type_idx + 1]
        schema.columns.append(Column(name=col_name, type=col_type, nullable=nullable))
    return schema


# ----------------------------------------------------------------------
# 统一入口
# ----------------------------------------------------------------------
def parse_table_schema(content: str, table_name: str = "") -> list[TableSchema]:
    """
    智能解析：内容含 CREATE TABLE 视为 DDL，否则视为 DESC 输出（需提供 table_name）。
    DDL 可含多表；DESC 单表。
    """
    if re.search(r"CREATE\s+TABLE", content, re.IGNORECASE):
        schemas = parse_ddl(content)
        if schemas:
            return schemas
    if not table_name:
        raise ValueError("DESC 输入需提供表名（table_name）")
    return [parse_desc(content, table_name)]
