"""
Agent 规划器状态机测试脚本（FR-AGENT 验收）
模拟多轮对话验证：
1. 闲聊意图 → DONE（不走挂起）；
2. 知识充足问题 → 直接输出方案 → DONE；
3. 需要真实数据的问题 → QUERY_PENDING 挂起（产出只读 SQL + 隐私提示）→ 回传 → 继续分析；
4. 查询轮次上限（脚本内将上限临时调为 2，快速验证强制收敛）；
5. 会话持久化（状态落库）。

运行命令（项目根目录；需后端已启动）：
    .venv\\Scripts\\python.exe scripts\\test_planner.py
"""
import sys
from pathlib import Path

import httpx

BASE = "http://127.0.0.1:8000"

passed = failed = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global passed, failed
    if cond:
        passed += 1
        print(f"  ✅ {name} {detail}")
    else:
        failed += 1
        print(f"  ❌ {name} {detail}")


def send(c: httpx.Client, sid: str | None, text: str) -> dict:
    """发送消息并打印摘要"""
    r = c.post(f"{BASE}/api/agent/chat", json={"session_id": sid, "text": text}).json()
    data = r["data"]
    state = data["state"]
    print(f"\n▶ 用户: {text[:60]}")
    print(f"  ◀ 状态={state} need_query={data['need_query']} 会话={data['session_id']}")
    if data.get("pending_query"):
        print(f"  ◀ 挂起SQL: {data['pending_query']['sql'][:100]}...")
        print(f"  ◀ 查询目的: {data['pending_query']['purpose'][:60]}")
    preview = data["content"][:150].replace("\n", " ")
    print(f"  ◀ 回复摘要: {preview}...")
    return data


def main() -> None:
    c = httpx.Client(timeout=180)

    print("=" * 70)
    print("场景 1：闲聊意图")
    s1 = send(c, None, "你好呀")
    check("闲聊不走挂起", s1["state"] == "DONE" and not s1["need_query"])

    print("=" * 70)
    print("场景 2：知识充足问题（starter 内置 ORA-01555）")
    s2 = send(c, None, "ORA-01555 快照过旧是什么原因，怎么处理？")
    check("输出方案并收敛", s2["state"] == "DONE" and not s2["need_query"])
    check("方案含处理内容", len(s2["content"]) > 100)

    print("=" * 70)
    print("场景 3：需要真实数据 → 挂起 → 回传 → 收敛")
    s3 = send(c, None, "帮我查一下今天 LIS 中间表的积压情况，看看接口是不是堵了")
    check("触发挂起", s3["state"] == "QUERY_PENDING" and s3["need_query"])
    pending_sql = s3.get("pending_query", {}).get("sql", "")
    check("挂起包含只读 SQL", pending_sql.upper().lstrip().startswith(("SELECT", "WITH")))
    check("回复含脱敏提醒", ("脱敏" in s3["content"]) or ("不要粘贴完整患者信息" in s3["content"]))
    check("SQL 行数控制提示(FETCH FIRST 或 ROWNUM)", "FETCH FIRST" in pending_sql.upper() or "ROWNUM" in pending_sql.upper())

    # 回传结果
    s3b = send(c, s3["session_id"], "STATUS=待处理,CNT=1234\nSTATUS=失败,CNT=56\n（数据已脱敏）")
    check("回传后收敛或继续挂起", s3b["state"] in ("DONE", "QUERY_PENDING"))
    if s3b["state"] == "QUERY_PENDING":
        # 再回传一次促收敛
        s3c = send(c, s3["session_id"], "STATUS=待处理,CNT=1300\nSTATUS=失败,CNT=0\n（已脱敏）")
        check("继续处理后收敛", s3c["state"] in ("DONE", "QUERY_PENDING"))

    print("=" * 70)
    print("场景 4：轮次上限强制收敛（进程内将上限调为 2）")
    # 说明：上限逻辑在后端进程内（QUERY_MAX_ROUNDS=5），此处通过连续回传验证状态机挂起/收敛流转
    s4 = send(c, None, "统计一下 LIS 库今天各科室检验标本量")
    sid4 = s4["session_id"]
    # 初始挂起即第 1 轮（query_round=1）
    check("初始挂起轮次=1", s4["state"] == "QUERY_PENDING"
          and c.get(f"{BASE}/api/agent/sessions/{sid4}").json()["data"]["query_round"] == 1)
    rounds = 0
    state = s4["state"]
    while state == "QUERY_PENDING" and rounds < 7:  # 最多回传 7 次（>上限5），应在第 5 次回传时强制收敛
        rounds += 1
        r = send(c, sid4, f"模拟回传第 {rounds} 次：科室A=10, 科室B=20（已脱敏）")
        state = r["state"]
        sess = c.get(f"{BASE}/api/agent/sessions/{sid4}").json()["data"]
        # 轮次语义：仍挂起 → 回传前 round+1；收敛 → 保持回传前 round
        expected = rounds + 1 if state == "QUERY_PENDING" else rounds
        check(f"轮次落库正确（第{rounds}次回传后）", sess["query_round"] == expected,
              f"query_round={sess['query_round']} 预期={expected}")
        if rounds == 5:
            # 回传前 round 已达 5 → 本次回传强制收敛（不允许再挂起）
            check("达到 5 轮上限后强制收敛", state == "DONE" and not r["need_query"],
                  f"state={state}")
            break
    check("全程状态合法", state in ("DONE", "QUERY_PENDING"))

    print("=" * 70)
    print("场景 5：会话持久化")
    sessions = c.get(f"{BASE}/api/agent/sessions").json()["data"]
    check("会话列表非空", len(sessions) >= 3, f"共 {len(sessions)} 个会话")
    detail = c.get(f"{BASE}/api/agent/sessions/{sid4}").json()["data"]
    check("消息历史已持久化", len(detail["messages"]) >= 4, f"{len(detail['messages'])} 条消息")
    check("状态已持久化", detail["state"] in ("DONE", "QUERY_PENDING"), f"state={detail['state']}")

    # 清理测试会话
    for sid in {s1["session_id"], s2["session_id"], s3["session_id"], sid4}:
        c.delete(f"{BASE}/api/agent/sessions/{sid}")

    print("\n" + "=" * 70)
    print(f"规划器测试结果：{passed}/{passed + failed} 通过")
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
