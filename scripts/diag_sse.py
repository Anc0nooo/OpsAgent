"""
SSE 实时性诊断：对指定地址发起流式对话请求，记录每个事件到达的时间戳。
用法：python scripts/diag_sse.py [地址...] [--prompt 问题文本]
不传地址默认 http://127.0.0.1:8000
"""
import json
import sys
import time

import httpx

PROMPT = "ORA-01555 快照过旧是什么原因？"


def parse_args(argv: list[str]) -> tuple[list[str], str]:
    """解析地址列表与 --prompt 参数"""
    bases, prompt_parts, collecting = [], [], False
    for a in argv:
        if a == "--prompt":
            collecting = True
            continue
        if collecting:
            prompt_parts.append(a)
        else:
            bases.append(a)
    prompt = " ".join(prompt_parts).strip()
    return (bases or ["http://127.0.0.1:8000"]), (prompt or PROMPT)


def probe(base: str, prompt: str) -> None:
    print(f"\n===== 探测 {base} =====")
    print(f"问题: {prompt}")
    t0 = time.perf_counter()
    events = 0
    delta_events = 0
    buf = ""
    try:
        with httpx.stream(
            "POST",
            f"{base}/api/chat/stream",
            json={"text": prompt},
            timeout=120,
            headers={"Accept": "text/event-stream"},
        ) as r:
            print(f"HTTP {r.status_code} | Content-Type: {r.headers.get('content-type')}")
            print(f"首字节到达: {time.perf_counter() - t0:.2f}s")
            for chunk in r.iter_raw():
                buf += chunk.decode("utf-8", errors="replace")
                while "\n\n" in buf:
                    block, buf = buf.split("\n\n", 1)
                    block = block.strip()
                    if block.startswith("data: ") and block != "data: [DONE]":
                        try:
                            ev = json.loads(block[6:])
                        except json.JSONDecodeError:
                            continue
                        events += 1
                        etype = ev.get("type")
                        clen = len(ev.get("content") or "")
                        if etype == "delta":
                            delta_events += 1
                        if events <= 8 or etype in ("done", "error"):
                            print(f"  +{time.perf_counter() - t0:6.2f}s  {etype:7s} content_len={clen}")
    except Exception as e:  # noqa: BLE001
        print(f"  异常: {e}")
    print(f"共 {events} 个事件（其中 delta {delta_events} 个），总耗时 {time.perf_counter() - t0:.2f}s")
    if delta_events > 1:
        print("  → 打字机流式正常（多个增量块）")
    elif delta_events == 1:
        print("  → 单块输出（挂起卡片或非流式路径）")


if __name__ == "__main__":
    bases, prompt = parse_args(sys.argv[1:])
    for base in bases:
        probe(base, prompt)
