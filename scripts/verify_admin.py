# 临时脚本：验证管理员功能（角色分配 / 用户管理 / 日志记录 / 权限隔离）
import sys
import time

# 确保项目根在 sys.path 中
sys.path.insert(0, r"f:\个人项目\医疗运维Agent智能体")

import httpx

BASE = "http://127.0.0.1:8000/api"
suffix = str(int(time.time()))[-6:]
admin_user = f"admin{suffix}"
normal_user = f"normal{suffix}"

with httpx.Client(timeout=15) as c:
    # 1. 注册两个新用户（均已不是首个用户，均为 user）
    r = c.post(f"{BASE}/auth/register", json={"username": admin_user, "password": "test123456"})
    assert r.status_code == 200, r.text
    assert r.json()["data"]["role"] == "user", "非首个用户应为 user"
    r = c.post(f"{BASE}/auth/register", json={"username": normal_user, "password": "test123456"})
    assert r.status_code == 200
    assert r.json()["data"]["role"] == "user"
    print(f"1. 新注册用户均为 user 角色 ✓")

    # 2. 通过已有管理员（迁移自动设的第一个用户）来操作
    #    查出已有 ancon 用户，用它登录测试
    from app.db.engine import SessionLocal
    from app.db.models import User
    with SessionLocal() as db:
        ancon = db.query(User).filter(User.role == "ancon").order_by(User.id).first()
        existing_admin = ancon.username if ancon else None
        existing_admin_id = ancon.id if ancon else None

    assert existing_admin, "数据库中应有 ancon 用户（迁移设置）"
    print(f"2. 已有管理员: {existing_admin} (id={existing_admin_id}) ✓")

    # 3. 用已有管理员登录
    #    密码未知，用直接方式：直接查库重置密码
    from app.auth.password import hash_password
    with SessionLocal() as db:
        u = db.query(User).filter(User.id == existing_admin_id).first()
        u.password_hash = hash_password("adminpass123")
        db.commit()
    r = c.post(f"{BASE}/auth/login", json={"username": existing_admin, "password": "adminpass123"})
    assert r.status_code == 200, r.text
    admin_token = r.json()["data"]["token"]
    assert r.json()["data"]["role"] == "ancon"
    print(f"3. 管理员登录 role = ancon ✓")

    # 4. 新用户登录
    r = c.post(f"{BASE}/auth/login", json={"username": normal_user, "password": "test123456"})
    normal_token = r.json()["data"]["token"]
    assert r.json()["data"]["role"] == "user"
    print("4. 新用户登录 role = user ✓")

    ha = {"Authorization": f"Bearer {admin_token}"}
    hn = {"Authorization": f"Bearer {normal_token}"}

    # 5. /me 返回 role
    r = c.get(f"{BASE}/auth/me", headers=ha)
    assert r.json()["data"]["role"] == "ancon"
    r = c.get(f"{BASE}/auth/me", headers=hn)
    assert r.json()["data"]["role"] == "user"
    print("5. /me 返回 role ✓")

    # 6. 普通用户访问 /api/admin/users → 403
    r = c.get(f"{BASE}/admin/users", headers=hn)
    assert r.status_code == 403, f"expect 403, got {r.status_code}"
    print("6. 普通用户访问 admin 接口 → 403 ✓")

    # 7. 管理员访问用户列表
    r = c.get(f"{BASE}/admin/users", headers=ha)
    assert r.status_code == 200, r.text
    users = r.json()["data"]["items"]
    assert len(users) >= 3
    print(f"7. admin 访问用户列表（{len(users)} 个用户）✓")

    # 8. admin 修改新用户的角色为 ancon
    normal_id = next(u["id"] for u in users if u["username"] == normal_user)
    r = c.patch(f"{BASE}/admin/users/{normal_id}/role", headers=ha, json={"role": "ancon"})
    assert r.status_code == 200, r.text
    r = c.get(f"{BASE}/admin/users", headers=ha)
    assert next(u["role"] for u in r.json()["data"]["items"] if u["id"] == normal_id) == "ancon"
    # 改回 user
    r = c.patch(f"{BASE}/admin/users/{normal_id}/role", headers=ha, json={"role": "user"})
    print("8. admin 修改角色 ✓")

    # 9. admin 禁用/启用 normal
    r = c.patch(f"{BASE}/admin/users/{normal_id}/status", headers=ha, json={"status": 0})
    assert r.status_code == 200, r.text
    # 被禁用用户登录 → 403
    r = c.post(f"{BASE}/auth/login", json={"username": normal_user, "password": "test123456"})
    assert r.status_code == 403
    # 重新启用
    r = c.patch(f"{BASE}/admin/users/{normal_id}/status", headers=ha, json={"status": 1})
    assert r.status_code == 200
    r = c.post(f"{BASE}/auth/login", json={"username": normal_user, "password": "test123456"})
    assert r.status_code == 200
    print("9. admin 禁用/启用用户 ✓")

    # 10. admin 重置密码
    r = c.post(f"{BASE}/admin/users/{normal_id}/reset-password", headers=ha, json={"password": "newpass123"})
    assert r.status_code == 200
    r = c.post(f"{BASE}/auth/login", json={"username": normal_user, "password": "newpass123"})
    assert r.status_code == 200
    print("10. admin 重置密码 ✓")

    # 11. 操作日志：admin 查看日志
    r = c.get(f"{BASE}/admin/logs", headers=ha)
    assert r.status_code == 200, r.text
    logs = r.json()["data"]["items"]
    actions = {l["action"] for l in logs}
    assert "login" in actions, f"应有 login 日志"
    assert "register" in actions, f"应有 register 日志"
    assert "update_status" in actions, f"应有 update_status 日志"
    assert "reset_password" in actions, f"应有 reset_password 日志"
    assert "update_role" in actions, f"应有 update_role 日志"
    print(f"11. 操作日志记录正常（{len(logs)} 条，含 login/register/update_status/reset_password/update_role）✓")

    # 12. 日志按用户筛选
    r = c.get(f"{BASE}/admin/logs/user/{normal_id}", headers=ha)
    assert r.status_code == 200
    assert all(l["user_id"] == normal_id for l in r.json()["data"]["items"])
    print("12. 日志按用户筛选 ✓")

    # 13. admin 不能删除自己
    r = c.delete(f"{BASE}/admin/users/{existing_admin_id}", headers=ha)
    assert r.status_code == 400, f"expect 400, got {r.status_code}"
    print("13. admin 不能删除自己 ✓")

    # 14. admin 删除 normal（级联清理）
    r = c.delete(f"{BASE}/admin/users/{normal_id}", headers=ha)
    assert r.status_code == 200, r.text
    r = c.get(f"{BASE}/admin/users", headers=ha)
    assert not any(u["id"] == normal_id for u in r.json()["data"]["items"])
    print("14. admin 删除用户（级联清理）✓")

    # 15. 日志导出 CSV
    r = c.get(f"{BASE}/admin/logs/export", headers=ha)
    assert r.status_code == 200
    assert "csv" in r.headers.get("content-type", "")
    assert len(r.content) > 50
    print("15. 日志导出 CSV ✓")

    # 16. 清理测试 admin 用户
    test_admin_id = next((u["id"] for u in users if u["username"] == admin_user), None)
    if test_admin_id:
        c.delete(f"{BASE}/admin/users/{test_admin_id}", headers=ha)

print("ALL PASSED")

