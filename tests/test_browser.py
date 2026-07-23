"""Browser fetcher: empty-vs-blocked handling (no real browser needed)."""

import pytest

import fetchers.browser as browser_mod
from fetchers.base import Firm
from fetchers.browser import BrowserFetcher, _looks_blocked


class FakeResp:
    def __init__(self, status=200):
        self.status = status


class FakePage:
    def __init__(self, body="", url="https://firm.com/careers", status=200):
        self._body = body
        self.url = url
        self._status = status

    def goto(self, url, **kw):
        self.url = url
        return FakeResp(self._status)

    def wait_for_timeout(self, ms):
        pass

    def inner_text(self, sel):
        return self._body

    def eval_on_selector_all(self, sel, script):
        return []


class FakeCtx:
    def __init__(self, page):
        self._page = page

    def new_page(self):
        return self._page

    def close(self):
        pass


# --- _looks_blocked (pure) -------------------------------------------------

def test_looks_blocked_on_http_error():
    assert _looks_blocked(FakePage(body="x" * 500), FakeResp(403)) is True


def test_looks_blocked_on_challenge_marker():
    pg = FakePage(body="Just a moment... checking your browser " + "x" * 400)
    assert _looks_blocked(pg, FakeResp(200)) is True


def test_looks_blocked_on_near_empty_body():
    assert _looks_blocked(FakePage(body="   "), FakeResp(200)) is True


def test_not_blocked_when_real_content():
    pg = FakePage(body="Our current openings. " + "content " * 60, status=200)
    assert _looks_blocked(pg, FakeResp(200)) is False


# --- fetch(): empty vs blocked --------------------------------------------

def _fetcher_with(monkeypatch, page, links=None):
    monkeypatch.setattr(browser_mod, "_collect", lambda pg: links or {})
    f = BrowserFetcher(client=None)
    monkeypatch.setattr(f, "_page", lambda: FakeCtx(page))
    return f


def _firm(**opts):
    return Firm(name="Test Firm", ats_type="browser",
               careers_url="https://firm.com/careers", options=opts)


def test_rendered_but_empty_returns_empty_not_error(monkeypatch):
    # Page loads fine (real content) but has 0 job links -> [] (not a failure).
    page = FakePage(body="No current openings. " + "text " * 60)
    f = _fetcher_with(monkeypatch, page)
    assert f.fetch(_firm()) == []


def test_blocked_page_raises(monkeypatch):
    page = FakePage(body="Just a moment...", status=403)
    f = _fetcher_with(monkeypatch, page)
    with pytest.raises(RuntimeError):
        f.fetch(_firm())


def test_blocked_page_tolerated_returns_empty(monkeypatch):
    page = FakePage(body="Just a moment...", status=403)
    f = _fetcher_with(monkeypatch, page)
    assert f.fetch(_firm(tolerate_block=True)) == []
