import pymysql
conn = pymysql.connect(host='192.168.3.164', port=3306, user='root', password='123456', charset='utf8mb4')
cursor = conn.cursor()

cursor.execute("SHOW DATABASES")
all_dbs = [r[0] for r in cursor.fetchall()]
sys_dbs = ['information_schema', 'mysql', 'performance_schema', 'sys', 'gitea']
user_dbs = [d for d in all_dbs if d not in sys_dbs]

print('=== 164服务器业务数据库 (' + str(len(user_dbs)) + '个) ===')
total = 0
for db in sorted(user_dbs):
    cursor.execute("SELECT SUM(data_length + index_length) FROM information_schema.tables WHERE table_schema = %s", (db,))
    size = cursor.fetchone()[0] or 0
    mb = round(size / 1024 / 1024, 2)
    total += mb
    cursor.execute("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = %s", (db,))
    cnt = cursor.fetchone()[0]
    print('  %-30s %3d表  %8s MB' % (db, cnt, mb))

print('\n业务库合计: %.0f MB' % total)
cursor.close()
conn.close()
