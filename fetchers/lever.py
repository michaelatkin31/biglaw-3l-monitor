"""Lever fetcher.

Public JSON postings API -- no auth required:
    https://api.lever.co/v0/postings/{company}?mode=json

`ats_identifier` is the company slug (the {company} in jobs.lever.co/{company}).
"""

from __future__ import annotations

import logging

from core.models import Posting
from core.normalize import normalize_lever_posting

from .base import Fetcher, Firm

log = logging.getLogger(__name__)

_API = "https://api.lever.co/v0/postings/{company}"


class LeverFetcher(Fetcher):
    ats_type = "lever"

    def fetch(self, firm: Firm) -> list[Posting]:
        company = firm.ats_identifier
        if not company:
            raise ValueError(f"{firm.name}: lever requires ats_identifier (company slug)")
        url = _API.format(company=company)
        data = self.client.get_json(url, params={"mode": "json"})
        items = data if isinstance(data, list) else []
        postings = [normalize_lever_posting(firm.name, p) for p in items]
        log.debug("%s: lever returned %d postings", firm.name, len(postings))
        return postings
