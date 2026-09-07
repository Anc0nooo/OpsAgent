# 临时脚本：创建 opsagent 数据库（utf8mb4）
import pymysql

conn = pymysql.connect(host='localhost', port=3306, user='root', password='123456', charset='utf8mb4')
with conn.cursor() as cur:
    cur.execute("CREATE DATABASE IF NOT EXISTS opsagent DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
    cur.execute("SHOW DATABASES LIKE 'opsagent'")
    print('数据库:', cur.fetchall())
conn.commit()
conn.close()
print('OK')
