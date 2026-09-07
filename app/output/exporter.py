"""
结果输出 - 导出器（md / csv / .sql）
- markdown：含元信息头部 + 方案正文 + 来源，供存档；
- csv：带 UTF-8 BOM（Excel 直接打开不乱码）；
- sql：注释头（目的/脱敏提醒/方言锁定）+ 单条只读 SQL。
"""
import csv
import io
from datetime import datetime
from typing import Any

from app.config.settings import settings


def export_markdown(content: str, meta: dict[str, Any] | None = None) -> str:
    """方案导出为 md（头部元信息 + 正文）"""
    meta = meta or {}
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    title = meta.get("title") or "OpsAgent 排查方案"
    head = [
        f"# {title}",
        "",
        f"> 导出时间：{ts}  ",
        f"> 会话：{meta.get('session_id', '-')}  ",
        f"> 模型：{meta.get('model', settings.CHAT_MODEL)} · 方言：{settings.SQL_DIALECT.upper()}（只读）",
        "",
        "---",
        "",
    ]
    return "\n".join(head) + content.strip() + "\n"


def export_csv(columns: list[str], rows: list[list[Any]]) -> str:
    """查询结果导出 csv（UTF-8 BOM，Excel 兼容）"""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(columns)
    for r in rows:
        writer.writerow(r)
    return "\ufeff" + buf.getvalue()


def export_sql(pending: dict[str, Any]) -> str:
    """挂起查询导出 .sql（注释头 + SQL；Oracle 方言锁定声明）"""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    purpose = pending.get("purpose", "").replace("\n", " ")
    sql = pending.get("sql", "").strip().rstrip(";")
    lines = [
        "-- OpsAgent 只读查询导出",
        f"-- 导出时间：{ts}",
        f"-- 查询目的：{purpose}",
        f"-- 第 {pending.get('round', 1)} 轮 · 方言：Oracle（只读 SELECT，禁止任何写操作）",
        "-- 回传提醒：结果请脱敏后粘贴回对话，不要包含完整患者信息（建议 ≤30 行）",
        "",
        f"{sql};",
        "",
    ]
    return "\n".join(lines)
