"""一键清空知识库（SQLite + Chroma）"""
import sqlite3
import os
import shutil

db = os.path.join("data", "opsagent.db")
conn = sqlite3.connect(db)
# 清空两张表
conn.execute("DELETE FROM knowledge_chunk")
conn.execute("DELETE FROM knowledge_doc")
conn.commit()
doc_count = conn.execute("SELECT COUNT(*) FROM knowledge_doc").fetchone()[0]
chunk_count = conn.execute("SELECT COUNT(*) FROM knowledge_chunk").fetchone()[0]
print(f"SQLite 已清空：doc={doc_count}, chunk={chunk_count}")
conn.close()

# 清空 Chroma 向量库
chroma_dir = os.path.join("data", "chroma")
if os.path.exists(chroma_dir):
    shutil.rmtree(chroma_dir)
os.makedirs(chroma_dir, exist_ok=True)
print("Chroma 向量库已重建（空）")

print("\n知识库清空完成，可以去前端 http://localhost:5173/knowledge 上传你的文档了")
