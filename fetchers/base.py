"""Fetcher interface.

A `Firm` is the parsed firms.yaml entry. Each concrete fetcher implements
`fetch(firm) -> list[Posting]`, doing its own network I/O via the shared
HttpClient and returning already-normalized Postings. Fetchers must NOT swallow
errors silently at the top level -- main.py wraps each firm in try/except so one
firm failing never aborts the run.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from core.http import HttpClient
from core.models import Posting


@dataclass
class Firm:
    name: str
    ats_type: str
    ats_identifier: Optional[str] = None
    careers_url: Optional[str] = None
    # ATS-specific overrides (e.g. workday host, generic render flag).
    options: dict[str, Any] = field(default_factory=dict)
    public_entry_level: Optional[bool] = None
    note: str = ""

    @classmethod
    def from_dict(cls, d: dict) -> "Firm":
        known = {"name", "ats_type", "ats_identifier", "careers_url",
                 "public_entry_level", "note"}
        options = {k: v for k, v in d.items() if k not in known}
        return cls(
            name=d["name"],
            ats_type=(d.get("ats_type") or "unknown").lower(),
            ats_identifier=d.get("ats_identifier"),
            careers_url=d.get("careers_url"),
            public_entry_level=d.get("public_entry_level"),
            note=d.get("note", "") or "",
            options=options,
        )


class Fetcher:
    ats_type = "base"

    def __init__(self, client: HttpClient) -> None:
        self.client = client

    def fetch(self, firm: Firm) -> list[Posting]:  # pragma: no cover - abstract
        raise NotImplementedError
