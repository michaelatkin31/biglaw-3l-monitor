"""Workday fetcher.

Workday exposes an unauthenticated JSON "CXS" jobs endpoint used by the public
careers site:
    POST https://{host}/wday/cxs/{tenant}/{site}/jobs
    body: {"appliedFacets": {}, "limit": 20, "offset": N, "searchText": ""}

`ats_identifier` is "tenant/site". The host includes a Workday data-center number
({tenant}.wd{N}.myworkdayjobs.com) which is NOT derivable from the tenant alone.
Pin it in firms.yaml as `workday_host` when known; otherwise we probe a small,
fixed set of data-center subdomains once and log the one that worked so it can be
pinned (keeps us from hammering Workday on every run).
"""

from __future__ import annotations

import logging
from typing import Optional

import requests

from core.models import Posting
from core.normalize import normalize_workday_job

from .base import Fetcher, Firm

log = logging.getLogger(__name__)

# Probed in order only when workday_host is not pinned. Kept short deliberately
# but covers the data centers actually seen on BigLaw tenants (wd503 and wd115
# are both live in firms.yaml -- a shorter list silently 422s those firms).
_DC_CANDIDATES = ["wd1", "wd3", "wd5", "wd12", "wd101", "wd103", "wd105", "wd115", "wd503"]
_PAGE_SIZE = 20
_MAX_JOBS = 1000  # safety cap so a huge board can't loop forever


class WorkdayFetcher(Fetcher):
    ats_type = "workday"

    def _split_identifier(self, firm: Firm) -> tuple[str, str]:
        ident = firm.ats_identifier or ""
        if "/" not in ident:
            raise ValueError(
                f"{firm.name}: workday ats_identifier must be 'tenant/site' "
                f"(got {ident!r})"
            )
        tenant, site = ident.split("/", 1)
        return tenant.strip(), site.strip()

    def _candidate_hosts(self, firm: Firm, tenant: str) -> list[str]:
        pinned = firm.options.get("workday_host")
        if pinned:
            return [pinned]
        return [f"{tenant}.{dc}.myworkdayjobs.com" for dc in _DC_CANDIDATES]

    def fetch(self, firm: Firm) -> list[Posting]:
        tenant, site = self._split_identifier(firm)
        locale = firm.options.get("workday_locale", "en-US")

        last_err: Optional[Exception] = None
        for host in self._candidate_hosts(firm, tenant):
            cxs = f"https://{host}/wday/cxs/{tenant}/{site}/jobs"
            base_url = f"https://{host}/{locale}/{site}"
            try:
                postings = self._paginate(firm, cxs, base_url)
            except requests.HTTPError as e:
                # 404 typically means "wrong data-center host" -> try the next.
                last_err = e
                log.debug("%s: workday host %s failed (%s)", firm.name, host, e)
                continue
            if not firm.options.get("workday_host"):
                log.info(
                    "%s: resolved workday host to %s -- pin as 'workday_host' in "
                    "firms.yaml to skip probing",
                    firm.name,
                    host,
                )
            return postings

        raise RuntimeError(
            f"{firm.name}: could not reach any Workday host for {tenant}/{site} "
            f"(last error: {last_err})"
        )

    def _paginate(self, firm: Firm, cxs_url: str, base_url: str) -> list[Posting]:
        postings: list[Posting] = []
        offset = 0
        total = None
        while offset < _MAX_JOBS:
            body = {
                "appliedFacets": {},
                "limit": _PAGE_SIZE,
                "offset": offset,
                "searchText": "",
            }
            data = self.client.post_json(cxs_url, body)
            if total is None:
                total = int(data.get("total", 0))
            page = data.get("jobPostings", []) or []
            if not page:
                break
            postings.extend(
                normalize_workday_job(firm.name, j, base_url) for j in page
            )
            offset += _PAGE_SIZE
            if total is not None and offset >= total:
                break
        log.debug("%s: workday returned %d jobs", firm.name, len(postings))
        return postings
