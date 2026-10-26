-- 数据库表结构，见 docs/interfaces.md 第 7 节。
-- reset.py 和 MCP 服务共用此文件；修改前先更新 interfaces.md。

CREATE TABLE blocks (
    block_id      TEXT PRIMARY KEY,
    title         TEXT NOT NULL,
    content       TEXT NOT NULL,
    level         INTEGER NOT NULL CHECK (level BETWEEN 0 AND 3),
    scope         TEXT NOT NULL CHECK (scope IN ('public', 'finance', 'support', 'release', 'rnd', 'mgmt')),
    status        TEXT NOT NULL CHECK (status IN ('verified', 'unverified', 'quarantined')),
    author        TEXT NOT NULL,          -- 种子数据为 'seed'，写入为 agent_id
    review_action TEXT CHECK (review_action IS NULL OR review_action IN ('pass', 'quarantine')),
    review_reason TEXT,
    created_at    TEXT NOT NULL           -- ISO 8601，UTC
);

CREATE TABLE ratings (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT NOT NULL,
    agent_id    TEXT NOT NULL,
    block_id    TEXT NOT NULL REFERENCES blocks (block_id),
    accuracy    INTEGER NOT NULL CHECK (accuracy BETWEEN 1 AND 5),
    reason      TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    UNIQUE (session_id, block_id)
);

CREATE INDEX idx_blocks_author ON blocks (author, created_at);
