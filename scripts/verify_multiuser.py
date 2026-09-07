# 临时脚本：端到端验证多用户注册/登录/鉴权 + 头像接口
import base64
import io
import json
import time

import httpx

BASE = "http://127.0.0.1:8000/api"
suffix = str(int(time.time()))[-6:]
user_a, user_b = f"alice{suffix}", f"bob{suffix}"

with httpx.Client(timeout=15) as c:
    # 1. 注册用户 A / B
    r = c.post(f"{BASE}/auth/register", json={"username": user_a, "password": "test123456"})
    assert r.status_code == 200, r.text
    r = c.post(f"{BASE}/auth/register", json={"username": user_b, "password": "test123456"})
    assert r.status_code == 200, r.text
    # 重复注册应 409
    r = c.post(f"{BASE}/auth/register", json={"username": user_a, "password": "x1234567"})
    assert r.status_code == 409, f"expect 409, got {r.status_code}"
    print("1. 注册 OK（重复用户名返回 409）")

    # 2. 登录 A
    r = c.post(f"{BASE}/auth/login", json={"username": user_a, "password": "test123456"})
    assert r.status_code == 200, r.text
    token_a = r.json()["data"]["token"]
    # 错误密码应 401
    r = c.post(f"{BASE}/auth/login", json={"username": user_a, "password": "wrong12345"})
    assert r.status_code == 401, f"expect 401, got {r.status_code}"
    print("2. 登录 OK（错误密码返回 401）")

    # 3. 未登录访问业务接口应 401
    r = c.get(f"{BASE}/knowledge/docs")
    assert r.status_code == 401, f"expect 401, got {r.status_code}"
    print("3. 未登录访问业务接口返回 401 OK")

    # 4. /me 返回当前用户（头像为空）
    r = c.get(f"{BASE}/auth/me", headers={"Authorization": f"Bearer {token_a}"})
    assert r.status_code == 200, r.text
    me = r.json()["data"]
    assert me["username"] == user_a and me["avatar"] == "", me
    print(f"4. /me OK（{me['username']}，默认无头像）")

    # 5. 上传头像（1x1 红色 PNG）
    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
    )
    r = c.post(f"{BASE}/auth/avatar", headers={"Authorization": f"Bearer {token_a}"},
               files={"file": ("avatar.png", io.BytesIO(png), "image/png")})
    assert r.status_code == 200, r.text
    avatar_url = r.json()["data"]["avatar"]
    assert avatar_url.startswith("data:image/png;base64,"), avatar_url[:40]
    r = c.get(f"{BASE}/auth/me", headers={"Authorization": f"Bearer {token_a}"})
    assert r.json()["data"]["avatar"].startswith("data:image/png"), "avatar 未保存"
    print("5. 头像上传/回读 OK")

    # 6. 移除头像
    r = c.delete(f"{BASE}/auth/avatar", headers={"Authorization": f"Bearer {token_a}"})
    assert r.status_code == 200 and r.json()["data"]["avatar"] == "", r.text
    print("6. 头像移除 OK")

    # 7. 用户 A 建会话，用户 B 看不到（隔离验证）
    ha = {"Authorization": f"Bearer {token_a}"}
    hb = {"Authorization": f"Bearer {token_a}"}  # 后面换 B
    r = c.post(f"{BASE}/auth/login", json={"username": user_b, "password": "test123456"})
    token_b = r.json()["data"]["token"]
    hb = {"Authorization": f"Bearer {token_b}"}

    r = c.post(f"{BASE}/agent/chat", headers=ha, json={"text": "会话隔离测试消息"})
    assert r.status_code == 200, r.text
    # B 的会话列表应为空（未对话过）
    r = c.get(f"{BASE}/agent/sessions", headers=hb)
    sessions_b = r.json()["data"]
    assert isinstance(sessions_b, list) and len(sessions_b) == 0, sessions_b
    # A 的会话列表应有 1 条
    r = c.get(f"{BASE}/agent/sessions", headers=ha)
    assert len(r.json()["data"]) >= 1, r.text
    print("7. 会话按用户隔离 OK（A 的会话 B 看不到）")

    # 8. B 越权访问 A 的会话详情应 404
    sid_a = c.get(f"{BASE}/agent/sessions", headers=ha).json()["data"][0]["id"]
    r = c.get(f"{BASE}/agent/sessions/{sid_a}", headers=hb)
    assert r.status_code == 404, f"expect 404, got {r.status_code}"
    print("8. 越权访问他人会话返回 404 OK")

print("ALL PASSED")
