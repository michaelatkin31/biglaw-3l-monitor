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
    def __init__(
        self,
        filter_cfg: dict,
        firm_location_policies: dict[str, str] | None = None,
    ) -> None:
        self.include_keywords = [k.lower() for k in filter_cfg.get("include_keywords", [])]
        self.exclude_keywords = [k.lower() for k in filter_cfg.get("exclude_keywords", [])]
        # A very small set of anchored titles represent recruiting/application
        # landing pages rather than staff recruiting jobs. They may override
        # explicitly configured soft excludes such as "recruiting", but never
        # other staff-role excludes such as coordinator/manager/director.
        self.priority_include_regexes = [
            re.compile(p, re.IGNORECASE)
            for p in filter_cfg.get("priority_include_regexes", [])
        ]
        self.priority_override_exclude_keywords = {
            k.lower()
            for k in filter_cfg.get("priority_override_exclude_keywords", [])
        }
        self.summer_keywords = [
            k.lower() for k in filter_cfg.get("summer_associate_keywords", [])
        ]
        self.include_summer = bool(filter_cfg.get("include_summer_associate", False))
        self.include_regexes = [
            re.compile(p, re.IGNORECASE) for p in filter_cfg.get("include_regexes", [])
        ]
        # Regexes that mark a role as clearly NOT entry-level (e.g. an explicit
        # years-of-experience requirement). These fire like an exclude, EXCEPT when
        # the title also carries a confident entry signal (see entry_signal_keywords),
        # which always wins -- so "Entry-Level Associate (0-2 years)" still matches.
        self.exclude_regexes = [
            re.compile(p, re.IGNORECASE) for p in filter_cfg.get("exclude_regexes", [])
        ]
        # Experience gate on the DESCRIPTION body (when a fetcher provides one):
        # a stated years-of-experience floor there disqualifies a role whose
        # TITLE is silent about seniority (a bare "Corporate Associate" that the
        # body reveals wants "3+ years"). Kept separate from exclude_regexes --
        # these must be number-bearing, never the bare "years of experience",
        # which appears in almost every description including entry-level ones.
        self.experience_gate_description = bool(
            filter_cfg.get("experience_gate_description", False)
        )
        self.description_exclude_regexes = [
            re.compile(p, re.IGNORECASE)
            for p in filter_cfg.get("description_exclude_regexes", [])
        ]
        self.entry_signals = [k.lower() for k in filter_cfg.get("entry_signal_keywords", [])]
        self.target_class_years = {
            int(year) for year in filter_cfg.get("target_class_years", [])
        }
        self.enforce_target_class_year = bool(
            filter_cfg.get("enforce_target_class_year", bool(self.target_class_years))
        )
        # Search title by default; optionally fold in location/other fields.
        self.search_fields = filter_cfg.get("search_fields", ["title"])

        # US-only geo gate. BigLaw public boards carry a large tail of foreign
        # trainee/stage/NQ programmes (London, Amsterdam, Milan, Singapore, ...)
        # that are irrelevant to a US 3L and would otherwise flood a recall-first
        # keyword net. A posting is dropped ONLY when its location clearly names a
        # foreign place AND names no US place -- so ambiguous locations
        # ("3 Locations", "Multi-City", a bare US city) are always kept (recall).
        self.us_only = bool(filter_cfg.get("us_only", False))
        self.foreign_markers = [m.lower() for m in filter_cfg.get("foreign_location_markers", [])]
        self.us_markers = [m.lower() for m in filter_cfg.get("us_location_markers", [])]
        self.firm_location_policies = {
            name.lower(): policy.lower()
            for name, policy in (firm_location_policies or {}).items()
            if policy
        }

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

    def _is_foreign_only(self, posting: Posting) -> bool:
        """True iff a foreign place is named and no US place is.

        Checks the location AND the title: some boards (e.g. Baker McKenzie) drop
        the office into the title ("London • 3330 • Attorney") and leave the
        location field blank, which would otherwise slip the geo gate. Recall-safe:
        unknown/ambiguous strings ("3 Locations", a bare US city, empty) are NOT
        foreign-only, so they are kept; a foreign word only excludes when no US
        place is named anywhere in location+title.
        """
        loc = (getattr(posting, "location", "") or "").lower()
        title = (getattr(posting, "title", "") or "").lower()
        haystack = f"{loc} {title}".strip()
        if not haystack:
            return False
        has_foreign = any(self._kw_hit(m, haystack) for m in self.foreign_markers)
        if not has_foreign:
            return False
        has_us = any(self._kw_hit(m, haystack) for m in self.us_markers)
        return not has_us

    def _has_us_location(self, posting: Posting) -> bool:
        """Return whether title/location affirmatively names a US office.

        Deliberately does not treat a bare "US" token as enough: global practice
        titles such as "US / International Tax Lawyer (Zurich)" use "US" to
        describe the work, not the office. The configured city/state/country
        markers are much stronger evidence.
        """
        loc = (getattr(posting, "location", "") or "").lower()
        title = (getattr(posting, "title", "") or "").lower()
        haystack = f"{loc} {title}".strip()
        return bool(haystack) and any(
            self._kw_hit(marker, haystack) for marker in self.us_markers
        )

    def _location_policy(self, posting: Posting) -> str:
        return self.firm_location_policies.get(
            posting.firm.lower(), "exclude_known_foreign"
        )

    def _priority_include(self, title_text: str) -> bool:
        return any(rx.search(title_text) for rx in self.priority_include_regexes)

    def _has_target_year_signal(self, title_text: str) -> bool:
        """Match a configured class year only when it is tied to hiring/JD."""
        for year in self.target_class_years:
            y = re.escape(str(year))
            if re.search(
                rf"\b{y}\b[^.\n]{{0,40}}\b(?:associate|jd|class|graduate|hiring)\b",
                title_text,
                re.IGNORECASE,
            ) or re.search(
                rf"\b(?:associate|jd|class|graduate|hiring)\b[^.\n]{{0,40}}\b{y}\b",
                title_text,
                re.IGNORECASE,
            ):
                return True
        return False

    @staticmethod
    def _near_future_years(title_text: str) -> set[int]:
        return {
            int(year)
            for year in re.findall(r"\b20(?:2[5-9]|3\d)\b", title_text)
        }

    def _signal_text(self, posting: Posting) -> str:
        """Lowercased title + description -- where entry signals are looked for."""
        text = self._haystack(posting)
        description = (getattr(posting, "description", "") or "").lower()
        return f"{text} {description}" if description else text

    def _has_entry_signal(self, signal_text: str) -> bool:
        return any(self._kw_hit(k, signal_text) for k in self.entry_signals) or any(
            rx.search(signal_text) for rx in self.include_regexes
        )

    # Graded entry-level likelihood, used to rank the digest (higher = more
    # likely a genuine entry-level / new-grad role). NOT used for include/exclude
    # -- purely presentational ordering.
    _STRONG_SIGNALS = (
        "first-year", "first year", "1st year", "entry-level", "entry level",
        "new grad", "new graduate", "incoming associate", "entering class",
    )
    _MED_SIGNALS = (
        "3l", "judicial clerk", "clerkship", "post-clerkship", "junior associate",
        "junior-level", "junior level",
    )

    def entry_score(self, posting: Posting) -> int:
        """0 = ambiguous (bare associate/attorney); 2 = a junior/clerkship signal;
        3 = an explicit first-year/entry-level/class-year signal."""
        hay = self._signal_text(posting)
        if (
            self._has_target_year_signal(self._haystack(posting))
            or self._priority_include(self._haystack(posting))
            or any(rx.search(hay) for rx in self.include_regexes)
            or any(self._kw_hit(k, hay) for k in self._STRONG_SIGNALS)
        ):
            return 3
        if any(self._kw_hit(k, hay) for k in self._MED_SIGNALS):
            return 2
        return 0

    def decide(self, posting: Posting) -> FilterDecision:
        text = self._haystack(posting)
        priority_include = self._priority_include(text)

        # Exclude wins first.
        for kw in self.exclude_keywords:
            if self._kw_hit(kw, text):
                if (
                    priority_include
                    and kw in self.priority_override_exclude_keywords
                ):
                    continue
                return FilterDecision(False, f"excluded by keyword: {kw!r}")

        # A confident entry signal ("entry-level", "first-year", "class of 2026",
        # a class-year regex, ...) overrides the experience-based excludes below.
        # Checked over title AND description so a body-stated "Class of 2026" or
        # "entry-level" keeps a generically-titled role.
        description = (getattr(posting, "description", "") or "").lower()
        signal_text = self._signal_text(posting)
        target_year_signal = self._has_target_year_signal(text)
        has_entry_signal = (
            self._has_entry_signal(signal_text)
            or target_year_signal
            or priority_include
        )
        # A generic first-year/entry-level title remains eligible when it has no
        # year. If it explicitly names a class year, however, it must include a
        # configured target year. This prevents "2026 First Year Associate" from
        # reaching a class-of-2027 recipient.
        title_years = self._near_future_years(text)
        if (
            self.enforce_target_class_year
            and has_entry_signal
            and title_years
            and self.target_class_years.isdisjoint(title_years)
        ):
            return FilterDecision(
                False,
                "excluded by non-target class year: "
                + ", ".join(str(year) for year in sorted(title_years)),
            )
        # Experience-based excludes: a title stating years of experience / an
        # ordinal year (2nd+) is definitionally not entry-level. Recall-safe --
        # ambiguous "Corporate Associate" (no experience stated) still passes.
        if not has_entry_signal:
            for rx in self.exclude_regexes:
                if rx.search(text):
                    return FilterDecision(False, f"excluded by regex: {rx.pattern!r}")
            # Description experience gate: the title is silent about seniority,
            # but the body states a years-of-experience floor -> lateral, drop it.
            if self.experience_gate_description and description:
                for rx in self.description_exclude_regexes:
                    if rx.search(description):
                        return FilterDecision(
                            False, f"excluded by description regex: {rx.pattern!r}"
                        )

        # US-only geo gate: drop clearly-foreign postings before include matching
        # so a recall-first keyword net doesn't surface London/Milan/Singapore
        # trainee & NQ programmes to a US 3L.
        if self.us_only:
            location_policy = self._location_policy(posting)
            if location_policy == "require_us" and not self._has_us_location(posting):
                return FilterDecision(
                    False,
                    "excluded without affirmative US office "
                    f"(firm policy=require_us): {posting.location!r}",
                )
            if (
                location_policy != "require_us"
                and self._is_foreign_only(posting)
            ):
                return FilterDecision(
                    False, f"excluded non-US location: {posting.location!r}"
                )

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

        if priority_include:
            return FilterDecision(True, "included by priority title pattern")

        if target_year_signal:
            return FilterDecision(True, "included by target class year")

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
