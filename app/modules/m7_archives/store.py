from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS arch_vouchers (
    source_org TEXT NOT NULL,
    batch_no TEXT NOT NULL,
    voucher_no TEXT NOT NULL,
    apportionment_key TEXT NOT NULL,
    amount TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    version_no INTEGER NOT NULL,
    payload TEXT NOT NULL,
    stream TEXT NOT NULL,
    batch_seq INTEGER NOT NULL,
    received_at TEXT NOT NULL,
    PRIMARY KEY (source_org, batch_no, voucher_no)
);
CREATE INDEX IF NOT EXISTS idx_arch_vouchers_apportion ON arch_vouchers (apportionment_key);
CREATE TABLE IF NOT EXISTS arch_voucher_versions (
    source_org TEXT NOT NULL,
    batch_no TEXT NOT NULL,
    voucher_no TEXT NOT NULL,
    version_no INTEGER NOT NULL,
    apportionment_key TEXT NOT NULL,
    amount TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    payload TEXT NOT NULL,
    received_at TEXT NOT NULL,
    PRIMARY KEY (source_org, batch_no, voucher_no, version_no)
);
CREATE TABLE IF NOT EXISTS arch_attributions (
    apportionment_key TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    effective_amount TEXT,
    member_count INTEGER NOT NULL,
    observed_digest TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS arch_disputes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    apportionment_key TEXT NOT NULL,
    cause TEXT NOT NULL,
    status TEXT NOT NULL,
    detail TEXT NOT NULL,
    opened_at TEXT NOT NULL,
    closed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_arch_disputes_key ON arch_disputes (apportionment_key, status);
CREATE TABLE IF NOT EXISTS arch_adjudications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    apportionment_key TEXT NOT NULL,
    decided_amount TEXT NOT NULL,
    bound_digest TEXT NOT NULL,
    bound_versions TEXT NOT NULL,
    reason TEXT NOT NULL,
    status TEXT NOT NULL,
    decided_at TEXT NOT NULL,
    invalidated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_arch_adjudications_key ON arch_adjudications (apportionment_key, status);
CREATE TABLE IF NOT EXISTS arch_checkpoints (
    stream TEXT PRIMARY KEY,
    source_org TEXT NOT NULL,
    last_seq INTEGER NOT NULL,
    last_digest TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


class ArchiveStore:
    """异地档案合库存储：写操作单事务提交，读操作单快照，互不暴露半份汇总。"""

    def __init__(self, path: str):
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript(SCHEMA)
            db.execute("PRAGMA journal_mode=WAL")

    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=30000")
        return db

    @contextmanager
    def write(self):
        db = self.connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.execute("COMMIT")
        except BaseException:
            db.execute("ROLLBACK")
            raise
        finally:
            db.close()

    @contextmanager
    def read(self):
        db = self.connect()
        try:
            db.execute("BEGIN")
            yield db
            db.execute("COMMIT")
        except BaseException:
            db.execute("ROLLBACK")
            raise
        finally:
            db.close()
