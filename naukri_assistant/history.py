"""Persist all submission attempts, including uncertain ones, to avoid retries."""

import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path


class History:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.db = sqlite3.connect(path, timeout=2)
        self.db.execute("CREATE TABLE IF NOT EXISTS attempts (job_id TEXT PRIMARY KEY, day TEXT NOT NULL, status TEXT NOT NULL, detail TEXT NOT NULL)")
        self.db.execute("CREATE TABLE IF NOT EXISTS run_lock (id INTEGER PRIMARY KEY CHECK (id=1))")
        self.db.commit()

    @contextmanager
    def exclusive(self):
        # OS-backed SQLite lock: released automatically even if a process crashes.
        lock = sqlite3.connect(str(Path(self.db.execute("PRAGMA database_list").fetchone()[2]).with_suffix(".lock.sqlite")), timeout=1)
        try:
            lock.execute("BEGIN EXCLUSIVE")
            yield
        finally:
            lock.rollback()
            lock.close()

    def seen(self, job_id: str) -> bool:
        return self.db.execute("SELECT 1 FROM attempts WHERE job_id=?", (job_id,)).fetchone() is not None

    def today(self) -> int:
        return self.db.execute("SELECT COUNT(*) FROM attempts WHERE day=?", (datetime.now().astimezone().date().isoformat(),)).fetchone()[0]

    def reserve(self, job_id: str):
        with self.db:
            self.db.execute("INSERT INTO attempts VALUES (?, ?, 'pending', '')", (job_id, datetime.now().astimezone().date().isoformat()))

    def finish(self, job_id: str, status: str, detail: str = ""):
        with self.db:
            self.db.execute("UPDATE attempts SET status=?, detail=? WHERE job_id=?", (status, detail, job_id))

    def rows(self):
        return self.db.execute("SELECT job_id, day, status, detail FROM attempts ORDER BY rowid DESC").fetchall()

    def close(self):
        self.db.close()
