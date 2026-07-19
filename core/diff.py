"""SQLite-backed seen-state and new-posting diffing.

We persist the postings we have *matched and notified on* -- keyed by
(firm, job_id). A posting is "new" when it passes the filter and its
(firm, job_id) is not already in the table. Re-running the same day therefore
notifies nothing new (idempotent).

Design note: we intentionally record only matched postings (not every fetched
job). The table is "already-notified matches", which is exactly what gives
notification idempotency. A side effect: if a firm later edits a non-matching
title into a matching one, we will (correctly) treat it as new.

`select_unseen` is read-only; `mark_seen` is the only writer -- so --dry-run can
diff without mutating state.
"""

from __future__ import annotations

import logging
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from .models import Posting

log = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS seen_jobs (
    firm        TEXT NOT NULL,
    job_id      TEXT NOT NULL,
    title       TEXT,
    location    TEXT,
    url         TEXT,
    posted_date TEXT,
    ats         TEXT,
    first_seen  TEXT NOT NULL,
    PRIMARY KEY (firm, job_id)
);
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class DiffStore:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = str(db_path)
        self._conn = sqlite3.connect(self.db_path)
        self._conn.row_factory = sqlite3.Row
        with self._conn:
            self._conn.executescript(_SCHEMA)

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "DiffStore":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def is_seen(self, firm: str, job_id: str) -> bool:
        with closing(self._conn.cursor()) as cur:
            cur.execute(
                "SELECT 1 FROM seen_jobs WHERE firm = ? AND job_id = ? LIMIT 1",
                (firm, job_id),
            )
            return cur.fetchone() is not None

    def select_unseen(self, postings: Iterable[Posting]) -> list[Posting]:
        """Read-only: return postings whose (firm, job_id) is not yet stored."""
        unseen: list[Posting] = []
        for p in postings:
            if not self.is_seen(p.firm, p.job_id):
                unseen.append(p)
        return unseen

    def mark_seen(self, postings: Iterable[Posting], now: str | None = None) -> int:
        """Persist postings as seen. Returns number of rows inserted."""
        now = now or _now_iso()
        rows = [
            (p.firm, p.job_id, p.title, p.location, p.url, p.posted_date, p.ats, now)
            for p in postings
        ]
        if not rows:
            return 0
        with self._conn:
            self._conn.executemany(
                """
                INSERT OR IGNORE INTO seen_jobs
                    (firm, job_id, title, location, url, posted_date, ats, first_seen)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
        return len(rows)

    def count(self) -> int:
        with closing(self._conn.cursor()) as cur:
            cur.execute("SELECT COUNT(*) FROM seen_jobs")
            return int(cur.fetchone()[0])
