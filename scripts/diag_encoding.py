"""验证 SQLite 中文编码是否正常"""
import sqlite3
import os

db_path = os.path.join("data", "opsagent.db")
print(f"DB path: {db_path}, exists: {os.path.exists(db_path)}")

conn = sqlite3.connect(db_path)
# Python sqlite3 在 Windows 上默认会自动检测编码，但显式设置更保险
conn.execute("PRAGMA encoding = 'UTF-8'")

rows = conn.execute(
    "SELECT id, title, doc_type, source, chunk_count FROM knowledge_doc ORDER BY id DESC LIMIT 10"
).fetchall()

print(f"\n知识库文档（共 {len(rows)} 条）:")
for r in rows:
    print(f"  id={r[0]} | title={r[1]} | doc_type={r[2]} | source={r[3]} | chunks={r[4]}")

if not rows:
    print("  (空)")

# 也看一条 chunk 确认正文编码
chunk = conn.execute(
    "SELECT id, text FROM knowledge_chunk LIMIT 1"
).fetchone()
if chunk:
    print(f"\n知识块示例 id={chunk[0]}: {chunk[1][:100]}...")

conn.close()
