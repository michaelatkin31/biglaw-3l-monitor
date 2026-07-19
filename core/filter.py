"""Include/exclude + class-year filtering for normalized postings.

Rules (from spec):
  * A posting matches if ANY include keyword/regex hits AND NO exclude keyword
    hits. Exclude always wins over include.
  * "summer associate" is excluded by default but can be re-enabled via a config
    flag (``include_summer_associate``).
  * Class-year patterns (a 4-digit near-future year adjacent to "associate" or
    "class") are handled as regexes in the config.

All keyword/regex lists live in config.yaml so they are tunable without code
edits. Every fetched-but-filtered-out posting is logged at DEBUG so the
false-negative rate stays auditable.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Iterable

from .models import Posting

log = logging.getLogger(__name__)


@dataclass
class FilterDecision:
    matched: bool
    reason: str


class PostingFilter:
    def __init__(self, filter_cfg: dict) -> None:
        self.include_keywords = [k.lower() for k in filter_cfg.get("include_keywords", [])]
        self.exclude_keywords = [k.lower() for k in filter_cfg.get("exclude_keywords", [])]
        self.summer_keywords = [
            k.lower() for k in filter_cfg.get("summer_associate_keywords", [])
        ]
        self.include_summer = bool(filter_cfg.get("include_summer_associate", False))
        self.include_regexes = [
            re.compile(p, re.IGNORECASE) for p in filter_cfg.get("include_regexes", [])
        ]
        # Search title by default; optionally fold in location/other fields.
        self.search_fields = filter_cfg.get("search_fields", ["title"])

    def _haystack(self, posting: Posting) -> str:
        parts = []
        for field in self.search_fields:
            parts.append(getattr(posting, field, "") or "")
        return " ".join(parts).lower()

    @staticmethod
    def _kw_hit(keyword: str, text: str) -> bool:
        # Word-boundary match so short tokens like "3l" don't match inside words
        # (e.g. avoid matching the "3l" in a random slug) while multi-word
        # phrases still match naturally.
        return re.search(rf"(?<!\w){re.escape(keyword)}(?!\w)", text) is not None

    def decide(self, posting: Posting) -> FilterDecision:
        text = self._haystack(posting)

        # Exclude wins first.
        for kw in self.exclude_keywords:
            if self._kw_hit(kw, text):
                return FilterDecision(False, f"excluded by keyword: {kw!r}")

        # Summer roles: excluded by default, but when the toggle is on they
        # become *includes* (merely un-excluding them would leave nothing to
        # match "Summer Associate" against the normal include list).
        if not self.include_summer:
            for kw in self.summer_keywords:
                if self._kw_hit(kw, text):
                    return FilterDecision(False, f"excluded summer role: {kw!r}")
        else:
            for kw in self.summer_keywords:
                if self._kw_hit(kw, text):
                    return FilterDecision(True, f"included summer role: {kw!r}")

        for kw in self.include_keywords:
            if self._kw_hit(kw, text):
                return FilterDecision(True, f"included by keyword: {kw!r}")

        for rx in self.include_regexes:
            if rx.search(text):
                return FilterDecision(True, f"included by regex: {rx.pattern!r}")

        return FilterDecision(False, "no include match")

    def apply(self, postings: Iterable[Posting]) -> list[Posting]:
        """Return only matching postings; log the rest at DEBUG."""
        matched: list[Posting] = []
        for p in postings:
            decision = self.decide(p)
            if decision.matched:
                log.debug("MATCH   [%s] %s -- %s", p.firm, p.title, decision.reason)
                matched.append(p)
            else:
                log.debug(
                    "FILTERED[%s] %s -- %s", p.firm, p.title, decision.reason
                )
        return matched
