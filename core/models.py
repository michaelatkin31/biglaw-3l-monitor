"""Shared data structures.

`Posting` is the single normalized shape every fetcher produces, regardless of
the underlying ATS. Keeping it small and flat makes the filter/diff/notify
stages trivially testable and makes a future read-only web UI over the same
SQLite DB straightforward.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class Posting:
    """A single normalized job posting."""

    firm: str
    job_id: str
    title: str
    location: str
    url: str
    ats: str
    # posted_date is stored as-provided by the ATS. Greenhouse/Lever give ISO
    # dates; Workday gives a relative string ("Posted 5 Days Ago"). It is
    # informational only -- (firm, job_id) is the identity key, never the date.
    posted_date: Optional[str] = None

    def key(self) -> tuple[str, str]:
        return (self.firm, self.job_id)


@dataclass
class FirmResult:
    """Per-firm outcome for the run summary (robustness requirement)."""

    firm: str
    ats: str
    ok: bool
    fetched: int = 0
    matched: int = 0
    error: Optional[str] = None


@dataclass
class RunSummary:
    """Aggregate outcome of one full run."""

    firms_attempted: int = 0
    firms_succeeded: int = 0
    firms_failed: int = 0
    postings_seen: int = 0
    matches: int = 0
    new_matches: int = 0
    per_firm: list[FirmResult] = field(default_factory=list)

    def add(self, r: FirmResult) -> None:
        self.per_firm.append(r)
        self.firms_attempted += 1
        if r.ok:
            self.firms_succeeded += 1
        else:
            self.firms_failed += 1
        self.postings_seen += r.fetched
        self.matches += r.matched

    def as_line(self) -> str:
        return (
            f"firms: {self.firms_succeeded}/{self.firms_attempted} ok "
            f"({self.firms_failed} failed) | postings seen: {self.postings_seen} "
            f"| matches: {self.matches} | new: {self.new_matches}"
        )
