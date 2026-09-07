# 临时脚本：验证配置保存 → 状态返回链路（模拟侧边栏状态读取）
import time

import httpx

BASE = "http://127.0.0.1:8000/api"
user = f"cfg{str(int(time.time()))[-6:]}"

with httpx.Client(timeout=15) as c:
    # 注册 + 登录
    c.post(f"{BASE}/auth/register", json={"username": user, "password": "test123456"})
    token = c.post(f"{BASE}/auth/login", json={"username": user, "password": "test123456"}).json()["data"]["token"]
    h = {"Authorization": f"Bearer {token}"}

    # 1. 初始状态：未配置
    st = c.get(f"{BASE}/settings/status", headers=h).json()["data"]
    print("初始状态: dashscope_configured =", st["dashscope_configured"], "| has_api_key =", st["has_api_key"])
    assert st["dashscope_configured"] is False, "新用户应显示未配置"

    # 2. 保存 API Key（前端 saveConfig 实际发送的字段）
    r = c.post(f"{BASE}/settings/keys", headers=h, json={"api_key": "sk-test_demo_1234567890abcdef"})
    assert r.json()["code"] == 0, r.text
    print("保存 Key: code =", r.json()["code"])

    # 3. 再次查状态：应为已配置 + 掩码
    st = c.get(f"{BASE}/settings/status", headers=h).json()["data"]
    print("保存后状态: dashscope_configured =", st["dashscope_configured"], "| has_api_key =", st["has_api_key"], "| masked =", st.get("api_key_masked"))
    assert st["dashscope_configured"] is True, "保存后应显示已配置"
    assert "****" in st.get("api_key_masked", ""), "应返回掩码"

    # 4. 登出（无 token）后查状态 → 401
    r = c.get(f"{BASE}/settings/status")
    assert r.status_code == 401, f"expect 401, got {r.status_code}"
    print("未登录查状态: 401 OK")

print("CONFIG STATUS CHAIN PASSED")
