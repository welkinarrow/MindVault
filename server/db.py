"""数据库访问。表结构见 server/schema.sql。

每次操作打开一个新连接、用完即关。SQLite 连接很便宜，这样做可以避免
多线程共用连接的问题，也不需要处理连接断开后的重连。
"""

import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

BLOCK_COLUMNS = "block_id, title, content, level, scope, status, author"
BLOCK_REVISION = "(SELECT COALESCE(MAX(id), 0) FROM block_history WHERE block_id = blocks.block_id)"
LINEAGE_COLUMNS = f"{BLOCK_COLUMNS}, sources, stale_since, {BLOCK_REVISION} AS revision"
SCOPE_CHECK = re.compile(r"\s*CHECK\s*\(\s*scope\s+IN\s*\([^)]*\)\s*\)", re.IGNORECASE)


def _derived(conn: sqlite3.Connection, block_id: str) -> list[str]:
    """Walk blocks.sources downstream from block_id (handles chains and cycles)."""
    found: list[str] = []
    frontier = [block_id]
    while frontier:
        current = frontier.pop()
        for (child,) in conn.execute(
            "SELECT b.block_id FROM blocks b, json_each(b.sources) j WHERE j.value = ?", (current,)
        ).fetchall():
            if child != block_id and child not in found:
                found.append(child)
                frontier.append(child)
    return found


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, db_path: Path):
        if not Path(db_path).exists():
            raise FileNotFoundError(f"找不到数据库 {db_path}，请先运行 python3 scripts/reset.py")
        self.db_path = Path(db_path)
        self._upgrade()

    def _upgrade(self) -> None:
        """Bring a database built before the Demo Day schema up to date (scopes, sources, agent_taint)."""
        self._drop_scope_check()
        with self._conn() as c:
            c.execute("BEGIN IMMEDIATE")
            cols = {r["name"] for r in c.execute("PRAGMA table_info(blocks)")}
            if "sources" not in cols:
                c.execute("ALTER TABLE blocks ADD COLUMN sources TEXT")
            if "stale_since" not in cols:
                c.execute("ALTER TABLE blocks ADD COLUMN stale_since TEXT")
            c.execute(
                "CREATE TABLE IF NOT EXISTS block_history ("
                " id INTEGER PRIMARY KEY AUTOINCREMENT,"
                " block_id TEXT NOT NULL REFERENCES blocks (block_id),"
                " old_title TEXT NOT NULL, old_content TEXT NOT NULL, old_level INTEGER NOT NULL,"
                " corrected_by TEXT NOT NULL, reason TEXT NOT NULL, corrected_at TEXT NOT NULL, flagged_ids TEXT)"
            )
            if "flagged_ids" not in {r["name"] for r in c.execute("PRAGMA table_info(block_history)")}:
                c.execute("ALTER TABLE block_history ADD COLUMN flagged_ids TEXT")
            c.execute(
                "CREATE TABLE IF NOT EXISTS agent_taint ("
                " agent_id TEXT PRIMARY KEY,"
                " max_level_read INTEGER NOT NULL DEFAULT 0 CHECK (max_level_read BETWEEN 0 AND 3),"
                " read_block_ids TEXT NOT NULL DEFAULT '[]',"
                " updated_at TEXT NOT NULL)"
            )

    def _drop_scope_check(self) -> None:
        """Old databases hardcode the scope list in a CHECK; rebuild blocks without it, keeping every row."""
        conn = sqlite3.connect(self.db_path, timeout=10, isolation_level=None)
        try:
            row = conn.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'blocks'").fetchone()
            if not row or not SCOPE_CHECK.search(row[0]):
                return
            new_sql = SCOPE_CHECK.sub("", row[0]).replace("CREATE TABLE blocks", "CREATE TABLE blocks_new", 1)
            conn.execute("PRAGMA foreign_keys = OFF")  # ratings references blocks; must be set outside a transaction
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(new_sql)
            conn.execute("INSERT INTO blocks_new SELECT * FROM blocks")
            conn.execute("DROP TABLE blocks")
            conn.execute("ALTER TABLE blocks_new RENAME TO blocks")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_blocks_author ON blocks (author, created_at)")
            conn.execute("COMMIT")
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

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

    def all_blocks(self, *, with_lineage: bool = False) -> list[dict]:
        """全部 block（含已隔离的，由 policy.can_read 负责过滤）。"""
        with self._conn() as c:
            return [dict(r) for r in c.execute(
                f"SELECT {LINEAGE_COLUMNS if with_lineage else BLOCK_COLUMNS} FROM blocks ORDER BY block_id"
            )]

    def get_block(self, block_id: str, *, with_lineage: bool = False) -> Optional[dict]:
        with self._conn() as c:
            row = c.execute(f"SELECT {LINEAGE_COLUMNS if with_lineage else BLOCK_COLUMNS} FROM blocks WHERE block_id = ?",
                            (block_id,)).fetchone()
        return dict(row) if row else None

    def get_block_with_sources(self, block_id: str) -> dict[str, dict]:
        """Read a block and its immediate sources from one SQLite snapshot."""
        with self._conn() as c:
            rows = c.execute(
                f"SELECT {LINEAGE_COLUMNS} FROM blocks WHERE block_id = ? OR block_id IN ("
                "SELECT value FROM json_each((SELECT sources FROM blocks WHERE block_id = ?)))",
                (block_id, block_id),
            )
            return {row["block_id"]: dict(row) for row in rows}

    def get_sources(self, block_id: str) -> list[str]:
        """Blocks the author had read before writing block_id. Seed blocks have none."""
        with self._conn() as c:
            row = c.execute("SELECT sources FROM blocks WHERE block_id = ?", (block_id,)).fetchone()
        return json.loads(row["sources"]) if row and row["sources"] else []

    def derived_from(self, block_id: str) -> list[str]:
        """Every block built on block_id, directly or through other blocks."""
        with self._conn() as c:
            return _derived(c, block_id)

    def stale_ids(self) -> set[str]:
        """Blocks built on something that has since been corrected."""
        with self._conn() as c:
            return {r[0] for r in c.execute("SELECT block_id FROM blocks WHERE stale_since IS NOT NULL")}

    # ------------------------------------------------------------ corrections
    def apply_correction(self, *, block_id: str, content: str, level: int, corrected_by: str,
                         reason: str, review_action: str, review_reason: str,
                         source_versions: Optional[dict[str, int]] = None,
                         expected_revision: Optional[int] = None) -> Optional[list[str]]:
        """Replace a block's content (old version goes to block_history), clear its own stale flag,
        and flag every block derived from it, directly or through other blocks. Returns the flagged IDs.
        Returns None if the proposed lineage changed before the correction could be saved."""
        conn = sqlite3.connect(self.db_path, timeout=10, isolation_level=None)
        try:
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("BEGIN IMMEDIATE")
            now = now_iso()
            old = conn.execute(f"SELECT title, content, level, {BLOCK_REVISION} FROM blocks WHERE block_id = ?",
                               (block_id,)).fetchone()
            if old is None:
                raise KeyError(block_id)
            if expected_revision is not None and old[3] != expected_revision:
                conn.execute("ROLLBACK")
                return None
            flagged = _derived(conn, block_id)
            if source_versions is not None:
                for source, version in source_versions.items():
                    row = conn.execute(f"SELECT {BLOCK_REVISION}, stale_since FROM blocks WHERE block_id = ?",
                                       (source,)).fetchone()
                    if (row is None or tuple(row) != (version, None)
                            or source == block_id or source in flagged):
                        conn.execute("ROLLBACK")
                        return None
            conn.execute(
                "INSERT INTO block_history (block_id, old_title, old_content, old_level, corrected_by, reason, "
                "corrected_at, flagged_ids) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (block_id, old[0], old[1], old[2], corrected_by, reason, now, json.dumps(flagged)),
            )
            conn.execute(
                "UPDATE blocks SET content = ?, level = ?, status = 'unverified', review_action = ?, "
                "review_reason = ?, stale_since = NULL, sources = COALESCE(?, sources) WHERE block_id = ?",
                (content, max(level, old[2]), review_action, review_reason,
                 json.dumps(list(source_versions)) if source_versions is not None else None, block_id),
            )
            if flagged:
                conn.executemany("UPDATE blocks SET stale_since = ? WHERE block_id = ?",
                                 [(now, b) for b in flagged])
            conn.execute("COMMIT")
            return flagged
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    # ------------------------------------------------------------ taint
    def get_taint(self, agent_id: str) -> tuple[int, list[str]]:
        """(highest level read, block_ids read in first-read order) since the last reset."""
        with self._conn() as c:
            row = c.execute(
                "SELECT max_level_read, read_block_ids FROM agent_taint WHERE agent_id = ?", (agent_id,)
            ).fetchone()
        return (row["max_level_read"], json.loads(row["read_block_ids"])) if row else (0, [])

    def add_taint(self, agent_id: str, block_id: str, level: int) -> None:
        """Record a successful read. Level only goes up; a block is listed once."""
        conn = sqlite3.connect(self.db_path, timeout=10, isolation_level=None)
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT max_level_read, read_block_ids FROM agent_taint WHERE agent_id = ?", (agent_id,)
            ).fetchone()
            current, ids = (row[0], json.loads(row[1])) if row else (0, [])
            if block_id not in ids:
                ids.append(block_id)
            conn.execute(
                "INSERT INTO agent_taint (agent_id, max_level_read, read_block_ids, updated_at) "
                "VALUES (?, ?, ?, ?) ON CONFLICT (agent_id) DO UPDATE SET "
                "max_level_read = excluded.max_level_read, read_block_ids = excluded.read_block_ids, "
                "updated_at = excluded.updated_at",
                (agent_id, max(current, level), json.dumps(ids), now_iso()),
            )
            conn.execute("COMMIT")
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    def reset_taint(self, agent_id: str) -> None:
        """Admin/orchestrator only: the agent starts a task with a clean context."""
        with self._conn() as c:
            c.execute("DELETE FROM agent_taint WHERE agent_id = ?", (agent_id,))

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
        sources: Optional[list[str]] = None,
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
                "review_action, review_reason, created_at, sources) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (block_id, title, content, level, scope, status, author,
                 review_action, review_reason, now_iso(), json.dumps(sources or [])),
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
