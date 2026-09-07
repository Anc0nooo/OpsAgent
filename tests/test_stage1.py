"""
阶段 1 本地单测（不依赖 API-KEY，可离线回归）：
- 切分器：SQL 代码块完整不拆散、章节划分、长文本切分；
- BM25：ORA 错误码强关键词、中文检索；
- 表结构解析：DDL（含多表/注释）、DESC 输出（PL/SQL Developer 与 SQL*Plus 风格）。

运行命令（项目根目录）：
    .venv\\Scripts\\python.exe tests\\test_stage1.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.rag.bm25 import BM25Index  # noqa: E402
from app.rag.splitter import split_document  # noqa: E402
from app.rag.table_parser import parse_ddl, parse_desc  # noqa: E402

passed = failed = 0


def check(name: str, cond: bool) -> None:
    global passed, failed
    if cond:
        passed += 1
        print(f"  ✅ {name}")
    else:
        failed += 1
        print(f"  ❌ {name}")


def test_splitter() -> None:
    print("\n[切分器]")
    text = (
        "# 排查步骤\n\n这是说明段落，第一行。\n这是第二行。\n\n"
        "```sql\nSELECT a FROM t WHERE x = 1;\nSELECT b FROM u WHERE y = 2;\n```\n\n"
        "```bash\nlsnrctl status\n```\n\n## 处理结论\n\n处理完成。"
    )
    chunks = split_document(text)
    check("块数量正确（标题段落+SQL块+bash块+结论）", len(chunks) == 4)
    sql_chunk = next((c for c in chunks if "SELECT a" in c["text"]), None)
    check("SQL 块完整不拆散（两条 SELECT 同块）", sql_chunk is not None and "SELECT b FROM u" in sql_chunk["text"])
    check("章节归属正确（SQL 属排查步骤）", sql_chunk is not None and sql_chunk["section"] == "排查步骤")
    bash_chunk = next((c for c in chunks if "lsnrctl" in c["text"]), None)
    check("命令行代码块完整", bash_chunk is not None and bash_chunk["text"].count("```") == 2)
    concl = next((c for c in chunks if "处理完成" in c["text"]), None)
    check("章节切换正确（结论属'处理结论'）", concl is not None and concl["section"] == "处理结论")


def test_bm25() -> None:
    print("\n[BM25 索引]")
    idx = BM25Index()
    idx.build([
        ("1", "ORA-01555 快照过旧，undo 表空间不足导致"),
        ("2", "输血执行率统计口径，按科室分组统计"),
        ("3", "表空间使用率巡检 SQL"),
    ])
    r1 = idx.search("ORA-01555", 3)
    check("错误码强关键词命中", len(r1) > 0 and r1[0][0] == "1")
    r2 = idx.search("输血执行率", 3)
    check("中文检索命中", len(r2) > 0 and r2[0][0] == "2")
    r3 = idx.search("表空间", 3)
    check("多文档含关键词时命中排序", len(r3) >= 1 and r3[0][0] in ("1", "3"))
    idx.add("4", "危急值统计视图示例")
    r4 = idx.search("危急值", 3)
    check("增量添加后可检索", len(r4) > 0 and r4[0][0] == "4")
    idx.remove("4")
    check("删除后不再命中", idx.search("危急值", 3) == [])


def test_ddl() -> None:
    print("\n[DDL 解析]")
    ddl = (
        "CREATE TABLE LIS_LIFEALERT (\n"
        "  ID VARCHAR2(20) NOT NULL,\n"
        "  PAT_DEPT VARCHAR2(50),\n"
        "  REPORT_TIME DATE,\n"
        "  RESULT_VAL NUMBER(10,2) DEFAULT 0,\n"
        "  CONSTRAINT PK_LIFE PRIMARY KEY (ID)\n"
        ");\n"
        "COMMENT ON COLUMN LIS_LIFEALERT.PAT_DEPT IS '患者科室';\n"
        "CREATE TABLE LAS_SAP_BARCODEREG (\n"
        "  BARCODE VARCHAR2(30) NOT NULL,\n"
        "  CREATE_TIME DATE\n"
        ");"
    )
    schemas = parse_ddl(ddl)
    check("多表解析数量", len(schemas) == 2)
    t1 = schemas[0]
    check("表名解析", t1.name == "LIS_LIFEALERT")
    check("字段数量（约束行不误判）", len(t1.columns) == 4)
    id_col = t1.columns[0]
    check("NOT NULL 解析", id_col.name == "ID" and id_col.nullable is False and id_col.type == "VARCHAR2(20)")
    num_col = next(c for c in t1.columns if c.name == "RESULT_VAL")
    check("带括号类型与默认值", num_col.type == "NUMBER(10,2)" and num_col.default == "0")
    dept_col = next(c for c in t1.columns if c.name == "PAT_DEPT")
    check("列注释关联", dept_col.comment == "患者科室")
    check("第二张表解析", schemas[1].name == "LAS_SAP_BARCODEREG" and len(schemas[1].columns) == 2)

    # 单行 DDL（无换行）也应正确解析
    one_line = "CREATE TABLE T_ONE_LINE (COL_A VARCHAR2(10) NOT NULL, COL_B NUMBER(10,2));"
    s3 = parse_ddl(one_line)
    check("单行 DDL 解析", len(s3) == 1 and len(s3[0].columns) == 2
          and s3[0].columns[1].type == "NUMBER(10,2)")


def test_desc() -> None:
    print("\n[DESC 解析]")
    # PL/SQL Developer 风格
    desc1 = (
        "Name       Type            Nullable Default Comments\n"
        "---------- --------------- -------- ------- --------\n"
        "PAT_ID     VARCHAR2(20)    Y\n"
        "PAT_NAME   VARCHAR2(50)    N\n"
        "AGE        NUMBER(3)       Y"
    )
    s1 = parse_desc(desc1, "T_PATIENT")
    check("PL/SQL Developer 表头跳过", len(s1.columns) == 3)
    check("字段与类型", s1.columns[0].name == "PAT_ID" and s1.columns[0].type == "VARCHAR2(20)")
    check("可空解析（Y/N）", s1.columns[1].nullable is False)

    # SQL*Plus 风格
    desc2 = (
        " 名称                                      是否为空?    类型\n"
        " ----------------------------------------- -------- ----------------------------\n"
        " ORDER_ID                                  NOT NULL VARCHAR2(20)\n"
        " STATUS                                             VARCHAR2(10)"
    )
    s2 = parse_desc(desc2, "T_ORDER")
    check("SQL*Plus 表头跳过", len(s2.columns) == 2)
    check("NOT NULL 中文风格解析", s2.columns[0].name == "ORDER_ID" and s2.columns[0].nullable is False)
    check("默认可空", s2.columns[1].nullable is True)


if __name__ == "__main__":
    test_splitter()
    test_bm25()
    test_ddl()
    test_desc()
    total = passed + failed
    print(f"\n单测结果：{passed}/{total} 通过")
    sys.exit(0 if failed == 0 else 1)
