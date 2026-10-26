"""数据库访问。表结构见 server/schema.sql。

每次操作打开一个新连接、用完即关。SQLite 连接很便宜，这样做可以避免
多线程共用连接的问题，也不需要处理连接断开后的重连。
"""

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

BLOCK_COLUMNS = ("block_id", "title", "content", "level", "scope", "status", "author")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, db_path: Path):
        if not Path(db_path).exists():
            raise FileNotFoundError(f"找不到数据库 {db_path}，请先运行 python3 scripts/reset.py")
        self.db_path = Path(db_path)

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")  # SQLite 默认不检查外键
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # ------------------------------------------------------------ 读取

    def all_blocks(self) -> list[dict]:
        """全部 block（含已隔离的，由 policy.can_read 负责过滤）。"""
        cols = ", ".join(BLOCK_COLUMNS)
        with self._conn() as c:
            return [dict(r) for r in c.execute(f"SELECT {cols} FROM blocks ORDER BY block_id")]

    def get_block(self, block_id: str) -> Optional[dict]:
        cols = ", ".join(BLOCK_COLUMNS)
        with self._conn() as c:
            row = c.execute(f"SELECT {cols} FROM blocks WHERE block_id = ?", (block_id,)).fetchone()
        return dict(row) if row else None

    # ------------------------------------------------------------ 写入

    def insert_written_block(
        self,
        *,
        title: str,
        content: str,
        level: int,
        scope: str,
        status: str,
        author: str,
        review_action: str,
        review_reason: str,
    ) -> str:
        """写入一个 agent 产生的 block，返回新 ID（w-0001、w-0002……）。

        BEGIN IMMEDIATE 先锁住数据库再取最大编号，保证并发写入时 ID 不重复。
        """
        conn = sqlite3.connect(self.db_path, timeout=10, isolation_level=None)
        try:
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT MAX(CAST(SUBSTR(block_id, 3) AS INTEGER)) FROM blocks WHERE block_id LIKE 'w-%'"
            ).fetchone()
            block_id = f"w-{(row[0] or 0) + 1:04d}"
            conn.execute(
                "INSERT INTO blocks (block_id, title, content, level, scope, status, author, "
                "review_action, review_reason, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (block_id, title, content, level, scope, status, author,
                 review_action, review_reason, now_iso()),
            )
            conn.execute("COMMIT")
            return block_id
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    def insert_rating(
        self, *, session_id: str, agent_id: str, block_id: str, accuracy: int, reason: str
    ) -> Optional[str]:
        """写入一条评分。成功返回 None；同一会话重复评分返回 "ALREADY_RATED"。

        其他约束错误（分数越界、block 不存在）直接抛出：调用方应先校验参数。
        """
        try:
            with self._conn() as c:
                c.execute(
                    "INSERT INTO ratings (session_id, agent_id, block_id, accuracy, reason, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (session_id, agent_id, block_id, accuracy, reason, now_iso()),
                )
        except sqlite3.IntegrityError as e:
            if "UNIQUE" in str(e):
                return "ALREADY_RATED"
            raise
        return None
