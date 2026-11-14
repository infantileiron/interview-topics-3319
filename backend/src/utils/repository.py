import json, sqlite3, threading
from contextlib import contextmanager
from dataclasses import asdict
from fastapi import HTTPException
from src.config.settings import DATABASE_URL
TABLES = ("datasets", "lexicons", "studies", "runs", "reviews", "reports", "users", "audit", "revision_batches")
_LOCK = threading.RLock()
def is_sqlite(): return DATABASE_URL.startswith("sqlite:///")
def connect():
    if is_sqlite():
        conn = sqlite3.connect(DATABASE_URL.removeprefix("sqlite:///"), timeout=30)
        return conn, "?"
    import psycopg
    return psycopg.connect(DATABASE_URL), "%s"
def init_db():
    conn,_ = connect()
    try:
        if not is_sqlite():
            conn.execute("SELECT pg_advisory_xact_lock(2305)")
        for table in TABLES:
            conn.execute(f"CREATE TABLE IF NOT EXISTS {table} (id TEXT PRIMARY KEY, payload TEXT NOT NULL)")
        conn.commit()
    finally: conn.close()
def all_rows(table):
    assert table in TABLES
    conn,_ = connect()
    try: return [json.loads(row[0]) for row in conn.execute(f"SELECT payload FROM {table} ORDER BY id").fetchall()]
    finally: conn.close()
def get(table, id):
    assert table in TABLES
    conn,q = connect()
    try:
        row = conn.execute(f"SELECT payload FROM {table} WHERE id={q}", (id,)).fetchone()
        if row is None: raise HTTPException(404, "记录不存在")
        return json.loads(row[0])
    finally: conn.close()
def put(table, item, insert_only=False):
    assert table in TABLES
    if not isinstance(item, dict): item = asdict(item)
    conn,q = connect()
    clause = "DO NOTHING" if insert_only else "DO UPDATE SET payload=excluded.payload"
    try:
        with _LOCK:
            conn.execute(f"INSERT INTO {table} (id,payload) VALUES ({q},{q}) ON CONFLICT(id) {clause}", (item["id"], json.dumps(item, ensure_ascii=False, allow_nan=False)))
            conn.commit()
        return item
    finally: conn.close()
@contextmanager
def transaction():
    """串行化写事务：Postgres 用事务级 advisory lock，SQLite 用 BEGIN IMMEDIATE。

    事务内通过 tx_all/tx_get/tx_put 访问同一连接，提交或回滚一致，避免整批
    修订只落一半。连接关闭即释放锁。
    """
    conn,q = connect()
    try:
        with _LOCK:
            if is_sqlite():
                conn.execute("BEGIN IMMEDIATE")
            else:
                conn.execute("BEGIN")
                conn.execute("SELECT pg_advisory_xact_lock(23051114)")
            yield _Tx(conn, q)
            conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
class _Tx:
    def __init__(self, conn, q): self.conn, self.q = conn, q
    def all(self, table):
        assert table in TABLES
        return [json.loads(r[0]) for r in self.conn.execute(f"SELECT payload FROM {table} ORDER BY id").fetchall()]
    def get(self, table, id):
        assert table in TABLES
        row = self.conn.execute(f"SELECT payload FROM {table} WHERE id={self.q}", (id,)).fetchone()
        if row is None: raise HTTPException(404, "记录不存在")
        return json.loads(row[0])
    def find(self, table, id):
        assert table in TABLES
        row = self.conn.execute(f"SELECT payload FROM {table} WHERE id={self.q}", (id,)).fetchone()
        return json.loads(row[0]) if row else None
    def put(self, table, item, insert_only=False):
        assert table in TABLES
        if not isinstance(item, dict): item = asdict(item)
        clause = "DO NOTHING" if insert_only else "DO UPDATE SET payload=excluded.payload"
        self.conn.execute(f"INSERT INTO {table} (id,payload) VALUES ({self.q},{self.q}) ON CONFLICT(id) {clause}",
                          (item["id"], json.dumps(item, ensure_ascii=False, allow_nan=False)))
        return item
