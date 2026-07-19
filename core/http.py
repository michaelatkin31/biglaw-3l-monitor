"""Polite HTTP client shared by all fetchers.

Requirements from the spec: real User-Agent, per-request timeouts, retry with
backoff, and small concurrency. Concurrency is bounded by the caller (main.py
fetches firms sequentially by default); this module owns the per-request
behaviour: timeouts, retries, and a sane UA.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

log = logging.getLogger(__name__)

DEFAULT_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 "
    "biglaw-3l-monitor/1.0 (+personal job-posting monitor)"
)


class HttpClient:
    """Thin wrapper around a requests.Session with retries + backoff.

    Retries cover transient failures (connection errors and 429/5xx) for both
    GET and POST -- Workday uses POST for its jobs endpoint, so POST must be
    retryable. Backoff is exponential (backoff_factor * 2**(n-1)).
    """

    def __init__(
        self,
        *,
        timeout: float = 20.0,
        retries: int = 3,
        backoff_factor: float = 1.0,
        user_agent: str = DEFAULT_UA,
    ) -> None:
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": user_agent})

        retry = Retry(
            total=retries,
            connect=retries,
            read=retries,
            status=retries,
            backoff_factor=backoff_factor,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset({"GET", "POST"}),
            raise_on_status=False,
            respect_retry_after_header=True,
        )
        adapter = HTTPAdapter(max_retries=retry)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)

    def get_json(self, url: str, **kwargs: Any) -> Any:
        resp = self.session.get(url, timeout=self.timeout, **kwargs)
        resp.raise_for_status()
        return resp.json()

    def post_json(self, url: str, json_body: dict, **kwargs: Any) -> Any:
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        headers.update(kwargs.pop("headers", {}))
        resp = self.session.post(
            url, json=json_body, headers=headers, timeout=self.timeout, **kwargs
        )
        resp.raise_for_status()
        return resp.json()

    def get_text(self, url: str, **kwargs: Any) -> Optional[str]:
        resp = self.session.get(url, timeout=self.timeout, **kwargs)
        resp.raise_for_status()
        return resp.text

    def close(self) -> None:
        self.session.close()
