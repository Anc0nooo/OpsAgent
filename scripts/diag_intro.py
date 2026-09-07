"""
快速诊断"介绍下自己"链路：
1. 直测 LLM 流式客户端（绕过业务逻辑）
2. 直测意图识别
3. 全链路 SSE 探测
"""
import asyncio
import json
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.llm import llm_client  # noqa: E402
from app.agent import prompts  # noqa: E402


async def test_llm_stream() -> None:
    """直测 LLM 流式客户端"""
    print("===== 1. LLM 流式客户端直测 =====")
    t0 = time.perf_counter()
    chunks = 0
    text_parts: list[str] = []
    try:
        async for delta in llm_client.chat_stream_async([{"role": "user", "content": "请用一句话介绍你自己"}]):
            chunks += 1
            text_parts.append(delta)
            if chunks <= 3:
                print(f"  +{time.perf_counter() - t0:.2f}s chunk#{chunks}: {delta!r}")
    except Exception as e:  # noqa: BLE001
        print(f"  ❌ 异常: {type(e).__name__}: {e}")
        return
    print(f"  ✅ {chunks} 个增量块，共 {len(''.join(text_parts))} 字，耗时 {time.perf_counter() - t0:.2f}s")


def test_intent() -> None:
    """直测意图识别（"介绍下自己"应判 chat）"""
    print("\n===== 2. 意图识别直测 =====")
    for text in ("介绍下自己", "你是谁", "你好"):
        try:
            out = llm_client.structured_output([{"role": "user", "content": prompts.INTENT_PROMPT.format(text=text[:500])}])
            print(f"  {text!r} → {out}")
        except Exception as e:  # noqa: BLE001
            print(f"  {text!r} → ❌ {type(e).__name__}: {e}")


async def test_sse() -> None:
    """全链路 SSE 探测（新会话发"介绍下自己"）"""
    print("\n===== 3. 全链路 SSE 探测 =====")
    t0 = time.perf_counter()
    events = 0
    buf = ""
    with httpx.stream(
        "POST", "http://127.0.0.1:8000/api/chat/stream",
        json={"text": "介绍下自己"},
        timeout=90,
    ) as r:
        print(f"HTTP {r.status_code}")
        for chunk in r.iter_raw():
            buf += chunk.decode("utf-8", errors="replace")
            while "\n\n" in buf:
                block, buf = buf.split("\n\n", 1)
                block = block.strip()
                if block.startswith("data: ") and block != "data: [DONE]":
                    ev = json.loads(block[6:])
                    events += 1
                    if events <= 5 or ev.get("type") in ("done", "error"):
                        print(f"  +{time.perf_counter() - t0:6.2f}s {ev.get('type'):7s} "
                              f"{(ev.get('text') or (ev.get('content') or '')[:40] or ev.get('message') or '')!r}")
    print(f"  共 {events} 个事件")


async def main() -> None:
    await test_llm_stream()
    await asyncio.to_thread(test_intent)
    await test_sse()


if __name__ == "__main__":
    asyncio.run(main())
