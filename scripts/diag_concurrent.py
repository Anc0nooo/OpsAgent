"""并发验证：流式对话进行期间，健康检查不被事件循环阻塞"""
import threading
import time

import httpx

result = {}


def slow_stream() -> None:
    """后台发起一个流式对话（模拟用户提问）"""
    try:
        with httpx.stream(
            "POST", "http://127.0.0.1:8000/api/chat/stream",
            json={"text": "什么是Oracle表空间？简要说明"},
            timeout=90,
        ) as r:
            for _ in r.iter_raw():
                pass
        result["slow"] = "done"
    except Exception as e:  # noqa: BLE001
        result["slow"] = str(e)


t = threading.Thread(target=slow_stream)
t.start()
time.sleep(2.5)  # 等流式请求进入 LLM 调用阶段

c = httpx.Client(timeout=5)
for i in range(3):
    t0 = time.perf_counter()
    c.get("http://127.0.0.1:8000/api/health")
    print(f"health #{i + 1}: {time.perf_counter() - t0:.3f}s（流式进行中）")

t.join()
print("slow_request:", result.get("slow"))
