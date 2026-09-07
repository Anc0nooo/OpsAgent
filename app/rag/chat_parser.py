"""
聊天记录解析器（微信 / 飞书 / 通用 txt 导出格式）

支持三种常见格式：
- 微信（WeChatMsg / 留痕导出）：
    张三 2024-01-01 10:00:00
    你好

    李四 2024-01-01 10:01:00
    你好
- 飞书：
    [2024-01-01 10:00:00] 张三: 你好
- 通用：
    2024-01-01 10:00:00 张三: 你好

自动识别：按各正则匹配到的消息数取最多者。
"""
import re
from dataclasses import dataclass
from datetime import datetime

# 系统消息关键词（撤回/加入/退出/邀请等）
SYSTEM_KEYWORDS = (
    "撤回", "加入", "退出", "群聊", "邀请", "被移出", "已成为好友",
    "修改群名", "你已", "以上是打招呼", "拍一拍", "收到红包", "领了红包",
)
# 表情/图片/语音/文件等占位符：整条内容形如 [xxx]
MEDIA_RE = re.compile(r"^\[.*\]$")

# 微信头行：发送人 日期 时间（内容可跨行，直到下一个头行）
WECHAT_RE = re.compile(r"^(.+?)\s+(\d{4}-\d{2}-\d{2}\s+\d{1,2}:\d{2}(?::\d{2})?)\s*$")
# 飞书：[日期 时间] 发送人: 内容（兼容中英文冒号）
FEISHU_RE = re.compile(r"^\[(\d{4}-\d{2}-\d{2}\s+\d{1,2}:\d{2}(?::\d{2})?)\]\s*(.+?)[:：]\s?(.*)$")
# 通用：日期 时间 发送人: 内容
GENERAL_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}\s+\d{1,2}:\d{2}(?::\d{2})?)\s+(.+?)[:：]\s?(.*)$")


@dataclass
class Message:
    """单条聊天消息"""
    time: datetime
    sender: str
    content: str


@dataclass
class ChatDoc:
    """拆分后的一个文档"""
    title: str
    text: str


def _parse_dt(s: str) -> datetime | None:
    """解析日期时间（兼容有无秒）"""
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(s.strip(), fmt)
        except ValueError:
            continue
    return None


def _is_system(content: str) -> bool:
    c = content.strip()
    if not c:
        return True
    return any(k in c for k in SYSTEM_KEYWORDS)


def _is_media(content: str) -> bool:
    c = content.strip()
    return bool(MEDIA_RE.match(c))


# ----------------------------------------------------------------------
# 各格式解析
# ----------------------------------------------------------------------
def _parse_wechat(text: str) -> list[Message]:
    """微信格式：发送人 日期 时间\\n内容（内容可跨行，直到下一个头行）"""
    msgs: list[Message] = []
    lines = text.splitlines()
    cur_sender = ""
    cur_dt: datetime | None = None
    cur_buf: list[str] = []

    def _flush() -> None:
        nonlocal cur_sender, cur_dt, cur_buf
        if cur_dt and cur_sender:
            content = "\n".join(cur_buf).strip()
            if content:
                msgs.append(Message(cur_dt, cur_sender, content))

    for line in lines:
        m = WECHAT_RE.match(line.strip())
        if m:
            _flush()
            cur_sender = m.group(1).strip()
            cur_dt = _parse_dt(m.group(2))
            cur_buf = []
        elif cur_dt is not None:
            cur_buf.append(line)
    _flush()
    return msgs


def _parse_feishu(text: str) -> list[Message]:
    """飞书格式：[日期 时间] 发送人: 内容（一行一条）"""
    msgs: list[Message] = []
    for line in text.splitlines():
        m = FEISHU_RE.match(line.strip())
        if not m:
            continue
        dt = _parse_dt(m.group(1))
        if not dt:
            continue
        msgs.append(Message(dt, m.group(2).strip(), m.group(3).strip()))
    return msgs


def _parse_general(text: str) -> list[Message]:
    """通用格式：日期 时间 发送人: 内容（一行一条）"""
    msgs: list[Message] = []
    for line in text.splitlines():
        m = GENERAL_RE.match(line.strip())
        if not m:
            continue
        dt = _parse_dt(m.group(1))
        if not dt:
            continue
        msgs.append(Message(dt, m.group(2).strip(), m.group(3).strip()))
    return msgs


_PARSERS = {
    "wechat": _parse_wechat,
    "feishu": _parse_feishu,
    "general": _parse_general,
}


def parse_text(text: str, source: str = "auto") -> tuple[list[Message], str]:
    """解析聊天记录文本。

    Args:
        text: 聊天记录原文
        source: wechat / feishu / auto（auto 按匹配数自动判断）

    Returns:
        (消息列表, 识别到的格式名)。无匹配返回 ([], "")。
    """
    if source in _PARSERS:
        return _PARSERS[source](text), source

    # auto：三种都试，取匹配数最多者
    best_name = ""
    best_msgs: list[Message] = []
    for name, fn in _PARSERS.items():
        ms = fn(text)
        if len(ms) > len(best_msgs):
            best_msgs = ms
            best_name = name
    return best_msgs, best_name


# ----------------------------------------------------------------------
# 过滤 / 拆分 / 预览
# ----------------------------------------------------------------------
def filter_messages(msgs: list[Message], filter_system: bool, filter_media: bool) -> list[Message]:
    """过滤系统消息、表情/图片占位符、空内容"""
    out: list[Message] = []
    for m in msgs:
        c = m.content.strip()
        if not c:
            continue
        if filter_system and _is_system(c):
            continue
        if filter_media and _is_media(c):
            continue
        out.append(m)
    return out


def _format_msgs(msgs: list[Message]) -> str:
    """格式化为 [时间] 发送人: 内容（按时间顺序）"""
    lines = []
    for m in msgs:
        t = m.time.strftime("%Y-%m-%d %H:%M:%S")
        lines.append(f"[{t}] {m.sender}: {m.content}")
    return "\n".join(lines)


def _unique_participants(msgs: list[Message]) -> list[str]:
    """按首次出现顺序去重"""
    seen: list[str] = []
    for m in msgs:
        if m.sender not in seen:
            seen.append(m.sender)
    return seen


def split_to_docs(msgs: list[Message], mode: str) -> list[ChatDoc]:
    """按拆分方式生成文档列表。

    mode: merge（合并为一个）/ day（按天）/ person（按人）
    """
    if not msgs:
        return []
    msgs_sorted = sorted(msgs, key=lambda m: m.time)

    if mode == "day":
        by_day: dict[str, list[Message]] = {}
        for m in msgs_sorted:
            by_day.setdefault(m.time.strftime("%Y-%m-%d"), []).append(m)
        return [ChatDoc(f"聊天记录-{day}", _format_msgs(ms)) for day, ms in by_day.items()]

    if mode == "person":
        by_person: dict[str, list[Message]] = {}
        for m in msgs_sorted:
            by_person.setdefault(m.sender, []).append(m)
        return [ChatDoc(f"聊天记录-{p}", _format_msgs(ms)) for p, ms in by_person.items()]

    # merge：合并为一个文档
    text = _format_msgs(msgs_sorted)
    parts = _unique_participants(msgs_sorted)
    p_str = "/".join(parts[:3]) + ("等" if len(parts) > 3 else "")
    start = msgs_sorted[0].time.strftime("%Y%m%d")
    end = msgs_sorted[-1].time.strftime("%Y%m%d")
    title = (
        f"聊天记录-{p_str}-{start}至{end}" if start != end else f"聊天记录-{p_str}-{start}"
    )
    return [ChatDoc(title, text)]


def build_preview(msgs: list[Message]) -> dict:
    """生成解析预览"""
    if not msgs:
        return {"count": 0, "participants": [], "time_range": "", "preview": []}
    participants = _unique_participants(msgs)
    times = [m.time for m in msgs]
    return {
        "count": len(msgs),
        "participants": participants,
        "time_range": (
            f"{min(times).strftime('%Y-%m-%d %H:%M')}"
            f" ~ {max(times).strftime('%Y-%m-%d %H:%M')}"
        ),
        "preview": [
            {
                "time": m.time.strftime("%Y-%m-%d %H:%M:%S"),
                "sender": m.sender,
                "content": m.content[:100],
            }
            for m in msgs[:10]
        ],
    }
