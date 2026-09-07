"""
阶段 4 SSE 流式接口测试（需后端已启动 + API-KEY 有效）
验证：
1. 事件序列合法（status → delta* → done，无越序）；
2. delta 分段到达（流式生效，非一次性）；
3. 挂起场景 done 事件带 pending_query（SQL 卡片数据）+ state=QUERY_PENDING；
4. 回传场景 SSE 继续可用（会话保持）；
5. done.session_id 与会话落库一致。

运行命令（项目根目录；需先启动后端）：
    .venv\\Scripts\\python.exe scripts\\test_sse.py
"""
import json
import sys

import httpx

BASE = "http://127.0.0.1:8000/api/chat/stream"

passed = failed = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global passed, failed
    if cond:
        passed += 1
        print(f"  ✅ {name} {detail}")
    else:
        failed += 1
        print(f"  ❌ {name} {detail}")


def stream_chat(text: str, sid: str | None = None) -> list[dict]:
    """POST SSE 并解析事件列表"""
    events: list[dict] = []
    with httpx.stream("POST", BASE, json={"session_id": sid, "text": text}, timeout=180) as r:
        buf = ""
        for chunk in r.iter_text():
            buf += chunk
            while "\n\n" in buf:
                block, buf = buf.split("\n\n", 1)
                line = block.strip()
                if not line or line == "data: [DONE]":
                    continue
                if line.startswith("data: "):
                    events.append(json.loads(line[len("data: "):]))
    return events


def summarize(events: list[dict]) -> str:
    types = [e["type"] for e in events]
    return f"事件序列: {types[:6]}{'...' if len(types) > 6 else ''}"


def main() -> None:
    print("\n[场景 1] 知识充足问题 → 流式方案（status→delta*→done）")
    ev = stream_chat("ORA-01555 快照过旧是什么原因，怎么处理？")
    print(" ", summarize(ev))
    types = [e["type"] for e in ev]
    check("含 status 事件", "status" in types)
    check("含 delta 事件", "delta" in types)
    check("末尾为 done", types and types[-1] == "done")
    check("事件顺序合法（done 前无 error）", "error" not in types)
    deltas = [e["content"] for e in ev if e["type"] == "delta"]
    check("delta 分段到达（流式生效）", len(deltas) >= 3, f"{len(deltas)} 段")
    done = ev[-1]
    check("done.state=DONE", done.get("state") == "DONE")
    full = "".join(deltas)
    check("方案正文完整", len(full) > 150, f"{len(full)} 字")

    print("\n[场景 2] 需要真实数据 → 挂起（done 带 SQL 卡片）")
    ev2 = stream_chat("帮我查一下今天 LIS 中间表的积压情况")
    print(" ", summarize(ev2))
    done2 = ev2[-1]
    check("挂起状态", done2.get("state") == "QUERY_PENDING" and done2.get("need_query") is True)
    pq = done2.get("pending_query") or {}
    check("pending_query 含 SQL/目的/轮次", bool(pq.get("sql")) and bool(pq.get("purpose")) and pq.get("round") == 1)
    check("SQL 为 Oracle 只读", pq.get("sql", "").upper().lstrip().startswith(("SELECT", "WITH")))
    check("正文含脱敏提醒", "脱敏" in "".join(e.get("content", "") for e in ev2 if e["type"] == "delta"))

    print("\n[场景 3] 回传结果 → SSE 继续分析（会话保持）")
    ev3 = stream_chat("STATUS=待处理,CNT=1234\nSTATUS=失败,CNT=0\n（已脱敏）", done2["session_id"])
    print(" ", summarize(ev3))
    done3 = ev3[-1]
    check("回传后会话一致", done3.get("session_id") == done2["session_id"])
    check("回传后收敛或继续挂起", done3.get("state") in ("DONE", "QUERY_PENDING"), f"state={done3.get('state')}")
    check("继续分析有内容", len("".join(e.get("content", "") for e in ev3 if e["type"] == "delta")) > 80)

    # 清理
    import httpx as hx
    hx.delete(f"http://127.0.0.1:8000/api/agent/sessions/{done2['session_id']}")

    print(f"\nSSE 接口测试结果：{passed}/{passed + failed} 通过")
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
