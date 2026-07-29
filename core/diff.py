"""SQLite-backed seen-state, delivery audit, and new-posting diffing.

``seen_jobs`` contains only postings from a seeded run or a digest that SMTP
accepted. ``notification_runs`` and ``notification_items`` preserve the exact
digest and its delivery status. ``shadow_jobs`` retains broader recall
candidates without putting them in the recipient's daily email.

Delivery finalization and seen-state updates share one SQLite transaction. A
failed delivery therefore stays retryable instead of disappearing from the
next run.
"""

from __future__ import annotations

import logging
import sqlite3
import uuid
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Iterable

from .models import Posting

if TYPE_CHECKING:
    from .notify import Digest

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

CREATE TABLE IF NOT EXISTS notification_runs (
    run_id       TEXT PRIMARY KEY,
    started_at   TEXT NOT NULL,
    completed_at TEXT,
    status       TEXT NOT NULL CHECK (
        status IN ('pending', 'sent', 'failed', 'seeded')
    ),
    subject      TEXT NOT NULL,
    text_body    TEXT NOT NULL,
    html_body    TEXT NOT NULL,
    match_count  INTEGER NOT NULL,
    shadow_count INTEGER NOT NULL DEFAULT 0,
    summary      TEXT NOT NULL,
    error        TEXT
);

CREATE TABLE IF NOT EXISTS notification_items (
    run_id       TEXT NOT NULL,
    firm         TEXT NOT NULL,
    job_id       TEXT NOT NULL,
    title        TEXT,
    location     TEXT,
    url          TEXT,
    posted_date  TEXT,
    ats           TEXT,
    entry_score  INTEGER NOT NULL DEFAULT 0,
    match_reason TEXT,
    PRIMARY KEY (run_id, firm, job_id),
    FOREIGN KEY (run_id) REFERENCES notification_runs(run_id)
);

CREATE TABLE IF NOT EXISTS shadow_jobs (
    firm         TEXT NOT NULL,
    job_id       TEXT NOT NULL,
    title        TEXT,
    location     TEXT,
    url          TEXT,
    posted_date  TEXT,
    ats          TEXT,
    match_reason TEXT,
    first_seen   TEXT NOT NULL,
    last_seen    TEXT NOT NULL,
    PRIMARY KEY (firm, job_id)
);

CREATE INDEX IF NOT EXISTS idx_notification_runs_started_at
    ON notification_runs(started_at DESC);
CREATE INDEX IF NOT EXISTS idx_shadow_jobs_last_seen
    ON shadow_jobs(last_seen DESC);
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(frozen=True)
class NotificationRun:
    run_id: str
    started_at: str
    completed_at: str | None
    status: str
    subject: str
    match_count: int
    shadow_count: int
    summary: str
    error: str | None


def _dedupe(postings: Iterable[Posting]) -> list[Posting]:
    unique: dict[tuple[str, str], Posting] = {}
    for posting in postings:
        unique.setdefault(posting.key(), posting)
    return list(unique.values())


class DiffStore:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = str(db_path)
        self._conn = sqlite3.connect(self.db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
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
        for p in _dedupe(postings):
            if not self.is_seen(p.firm, p.job_id):
                unseen.append(p)
        return unseen

    def mark_seen(self, postings: Iterable[Posting], now: str | None = None) -> int:
        """Persist postings as seen outside a notification transaction."""
        now = now or _now_iso()
        postings = _dedupe(postings)
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

    def begin_notification(
        self,
        digest: Digest,
        summary: str,
        postings: Iterable[Posting],
        *,
        shadow_postings: Iterable[Posting] = (),
        match_reasons: dict[tuple[str, str], str] | None = None,
        shadow_reasons: dict[tuple[str, str], str] | None = None,
        score_fn: Callable[[Posting], int] | None = None,
        now: str | None = None,
    ) -> str:
        """Record a pending digest and the broader shadow candidates.

        This happens before SMTP. If the process crashes mid-send, the pending
        row documents the attempt while the included jobs remain retryable.
        """
        run_id = str(uuid.uuid4())
        now = now or _now_iso()
        postings = _dedupe(postings)
        shadow_postings = _dedupe(shadow_postings)
        match_reasons = match_reasons or {}
        shadow_reasons = shadow_reasons or {}
        score_fn = score_fn or (lambda _posting: 0)
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO notification_runs
                    (run_id, started_at, status, subject, text_body, html_body,
                     match_count, shadow_count, summary)
                VALUES (?, ?, 'pending', ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    now,
                    digest.subject,
                    digest.text_body,
                    digest.html_body,
                    digest.match_count,
                    len(shadow_postings),
                    summary,
                ),
            )
            self._conn.executemany(
                """
                INSERT INTO notification_items
                    (run_id, firm, job_id, title, location, url, posted_date,
                     ats, entry_score, match_reason)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        run_id,
                        p.firm,
                        p.job_id,
                        p.title,
                        p.location,
                        p.url,
                        p.posted_date,
                        p.ats,
                        int(score_fn(p)),
                        match_reasons.get(p.key()),
                    )
                    for p in postings
                ],
            )
            self._conn.executemany(
                """
                INSERT INTO shadow_jobs
                    (firm, job_id, title, location, url, posted_date, ats,
                     match_reason, first_seen, last_seen)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(firm, job_id) DO UPDATE SET
                    title = excluded.title,
                    location = excluded.location,
                    url = excluded.url,
                    posted_date = excluded.posted_date,
                    ats = excluded.ats,
                    match_reason = excluded.match_reason,
                    last_seen = excluded.last_seen
                """,
                [
                    (
                        p.firm,
                        p.job_id,
                        p.title,
                        p.location,
                        p.url,
                        p.posted_date,
                        p.ats,
                        shadow_reasons.get(p.key()),
                        now,
                        now,
                    )
                    for p in shadow_postings
                ],
            )
        return run_id

    def finish_notification(
        self,
        run_id: str,
        postings: Iterable[Posting],
        *,
        status: str = "sent",
        now: str | None = None,
    ) -> None:
        """Atomically mark a digest complete and its postings seen."""
        if status not in {"sent", "seeded"}:
            raise ValueError("finish_notification status must be 'sent' or 'seeded'")
        now = now or _now_iso()
        postings = _dedupe(postings)
        rows = [
            (p.firm, p.job_id, p.title, p.location, p.url, p.posted_date, p.ats, now)
            for p in postings
        ]
        with self._conn:
            updated = self._conn.execute(
                """
                UPDATE notification_runs
                SET status = ?, completed_at = ?
                WHERE run_id = ? AND status = 'pending'
                """,
                (status, now, run_id),
            )
            if updated.rowcount != 1:
                raise RuntimeError(
                    f"notification run {run_id!r} is missing or is not pending"
                )
            if rows:
                self._conn.executemany(
                    """
                    INSERT OR IGNORE INTO seen_jobs
                        (firm, job_id, title, location, url, posted_date, ats, first_seen)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    rows,
                )

    def fail_notification(
        self, run_id: str, error: str, now: str | None = None
    ) -> None:
        """Record a failed delivery without touching seen-state."""
        with self._conn:
            self._conn.execute(
                """
                UPDATE notification_runs
                SET status = 'failed', completed_at = ?, error = ?
                WHERE run_id = ? AND status = 'pending'
                """,
                (now or _now_iso(), error, run_id),
            )

    def list_notification_runs(self, limit: int = 20) -> list[NotificationRun]:
        with closing(self._conn.cursor()) as cur:
            cur.execute(
                """
                SELECT run_id, started_at, completed_at, status, subject,
                       match_count, shadow_count, summary, error
                FROM notification_runs
                ORDER BY started_at DESC
                LIMIT ?
                """,
                (max(1, int(limit)),),
            )
            return [NotificationRun(**dict(row)) for row in cur.fetchall()]

    def notification_items(self, run_id: str) -> list[sqlite3.Row]:
        with closing(self._conn.cursor()) as cur:
            cur.execute(
                """
                SELECT firm, job_id, title, location, url, posted_date, ats,
                       entry_score, match_reason
                FROM notification_items
                WHERE run_id = ?
                ORDER BY firm, title
                """,
                (run_id,),
            )
            return cur.fetchall()

    def shadow_count(self) -> int:
        with closing(self._conn.cursor()) as cur:
            cur.execute("SELECT COUNT(*) FROM shadow_jobs")
            return int(cur.fetchone()[0])

    def list_unlinked_seen(self, limit: int = 20) -> list[sqlite3.Row]:
        """Return legacy seen rows that predate exact notification auditing."""
        with closing(self._conn.cursor()) as cur:
            cur.execute(
                """
                SELECT s.firm, s.job_id, s.title, s.location, s.url, s.first_seen
                FROM seen_jobs AS s
                WHERE NOT EXISTS (
                    SELECT 1
                    FROM notification_items AS i
                    WHERE i.firm = s.firm AND i.job_id = s.job_id
                )
                ORDER BY s.first_seen DESC, s.firm, s.title
                LIMIT ?
                """,
                (max(1, int(limit)),),
            )
            return cur.fetchall()

    def count(self) -> int:
        with closing(self._conn.cursor()) as cur:
            cur.execute("SELECT COUNT(*) FROM seen_jobs")
            return int(cur.fetchone()[0])
