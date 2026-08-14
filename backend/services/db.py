"""MySQL 连接池 — 支持多数据库切换（tt 业务库 / audit_law 法规库）

依赖: pymysql + DBUtils，配置来自 config.Config
"""

import time
import threading

import pymysql
from pymysql.cursors import DictCursor
from dbutils.pooled_db import PooledDB
from config import Config

# 按数据库名缓存连接池，懒初始化
_pools: dict[str, PooledDB] = {}

# 保活线程是否已启动（幂等）
_keepalive_started = False


def _get_pool(database: str | None = None) -> PooledDB:
    """获取指定数据库的连接池（线程安全，首次访问自动创建）"""
    db = database or Config.MYSQL_DATABASE
    if db not in _pools:
        _pools[db] = PooledDB(
            creator=pymysql,
            maxconnections=20,
            mincached=2,
            maxcached=10,
            blocking=True,
            host=Config.MYSQL_HOST,
            port=Config.MYSQL_PORT,
            user=Config.MYSQL_USER,
            password=Config.MYSQL_PASSWORD,
            database=db,
            charset='utf8mb4',
            cursorclass=DictCursor,
        )
    return _pools[db]


def get_connection(database: str | None = None):
    """从连接池获取一个数据库连接（调用方负责 close）

    通常不直接使用，优先使用 query / execute 等封装函数。
    """
    return _get_pool(database).connection()


def _start_keepalive(interval: int = 30) -> None:
    """后台保活线程 — 防止防火墙/NAT 掐断闲置 MySQL 连接

    症状：闲置约 1 分钟后首次请求慢 ~1.4s（连接被网络中间设备掐断，重连耗时）。
    原理：每隔 interval 秒对每个连接池的"空闲连接"（_idle_cache）执行 ping，
    保持 TCP 活跃，避免被掐断；顺带提前重建已死连接。
    幂等：用全局标志保证只启动一次；全程防御式 try/except，绝不向调用方抛错。
    """
    global _keepalive_started
    if _keepalive_started:
        return
    _keepalive_started = True

    def _worker():
        while True:
            time.sleep(interval)
            for pool in list(_pools.values()):
                try:
                    cache = getattr(pool, "_idle_cache", None) or []
                    for conn in list(cache):
                        try:
                            conn.ping()
                        except Exception:
                            pass  # 连接已死，下次 checkout 时由池重建
                except Exception:
                    pass

    threading.Thread(target=_worker, name="mysql-keepalive", daemon=True).start()


# 模块加载即启动保活（幂等；仅当有池创建后才真正干活）
_start_keepalive()


def query(sql: str, params=None, database: str | None = None) -> list[dict]:
    """执行 SELECT 查询，返回全部结果行

    Args:
        sql: SQL 语句，使用 %s 作为参数占位符
        params: 参数元组或字典
        database: 数据库名，默认使用 Config.MYSQL_DATABASE

    Returns:
        list[dict] — 查询结果列表，空结果返回 []

    Example:
        >>> query("SELECT id, name FROM audit_projects WHERE status = %s", ('active',))
        [{'id': 'a1b2...', 'name': '市教育局2026采购审计'}, ...]
    """
    conn = _get_pool(database).connection()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()
    finally:
        conn.close()


def query_one(sql: str, params=None, database: str | None = None) -> dict | None:
    """执行 SELECT 查询，返回单行结果

    Returns:
        dict | None — 存在则返回字典，无匹配则返回 None

    Example:
        >>> query_one("SELECT * FROM audit_projects WHERE id = %s", (pid,))
        {'id': 'a1b2...', 'name': '...'}
    """
    conn = _get_pool(database).connection()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchone()
    finally:
        conn.close()


def execute(sql: str, params=None, database: str | None = None) -> int:
    """执行 INSERT/UPDATE/DELETE，自动提交，异常回滚

    Returns:
        int — 影响行数

    Example:
        >>> execute("UPDATE audit_projects SET status = %s WHERE id = %s", ('active', pid))
        1
    """
    conn = _get_pool(database).connection()
    try:
        with conn.cursor() as cur:
            affected = cur.execute(sql, params)
            conn.commit()
            # 记录写操作日志（防递归：日志表的写入不再记录）
            try:
                sql_str = (sql or "").strip().upper()
                if sql_str.startswith(("INSERT", "UPDATE", "DELETE")) and "audit_logs" not in (sql or "").lower():
                    from services.audit_logger import log_db_write
                    log_db_write(sql, params, affected)
            except Exception:
                pass
            return affected
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def insert(sql: str, params=None, database: str | None = None) -> int:
    """执行 INSERT，返回自增主键 ID

    Returns:
        int — 新插入行的自增 ID

    Example:
        >>> new_id = insert(
        ...     "INSERT INTO audit_projects (id, name) VALUES (%s, %s)",
        ...     (pid, '新项目')
        ... )
        42
    """
    conn = _get_pool(database).connection()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            conn.commit()
            # 记录写操作日志（防递归：日志表的写入不再记录）
            try:
                sql_str = (sql or "").strip().upper()
                if sql_str.startswith("INSERT") and "audit_logs" not in (sql or "").lower():
                    from services.audit_logger import log_db_write
                    log_db_write(sql, params, cur.rowcount)
            except Exception:
                pass
            return cur.lastrowid
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def health(database: str | None = None) -> bool:
    """检查数据库连接是否正常

    Example:
        >>> health()       # 检查默认库
        True
        >>> health('tt')   # 检查 tt 库
        True
    """
    try:
        row = query_one("SELECT 1 AS ok", database=database)
        return row is not None and row.get('ok') == 1
    except Exception:
        return False


# 表列名缓存（列结构极少变，首次查 information_schema 后复用）
_column_cache: dict[str, list[str]] = {}


def get_columns(table: str, database: str = "tt", use_cache: bool = True) -> list[str]:
    """取一张表的全部列名（information_schema，模块级缓存）

    供 execution_planner.precheck_expression 判断"违规表达式引用的字段在目标表存不存在"。
    information_schema 返回大写 key（COLUMN_NAME），此处大小写不敏感取值并统一小写。
    """
    if use_cache and table in _column_cache:
        return _column_cache[table]
    try:
        rows = query(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = %s AND table_name = %s ORDER BY ordinal_position",
            (database, table), database=database,
        )
        cols = []
        for r in rows:
            name = r.get("column_name") or r.get("COLUMN_NAME") or ""
            if name:
                cols.append(str(name).lower())
    except Exception:
        cols = []  # 查询失败返空，precheck 据此跳过字段校验（不误杀）
    if use_cache:
        _column_cache[table] = cols
    return cols
