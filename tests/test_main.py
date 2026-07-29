"""End-to-end orchestration test with a fake fetcher (no network)."""

import yaml

import main as main_mod
from core.models import Posting
from fetchers.base import Fetcher


class FakeFetcher(Fetcher):
    ats_type = "greenhouse"

    def __init__(self, client, postings):
        super().__init__(client)
        self._postings = postings

    def fetch(self, firm):
        return list(self._postings)


class ExplodingFetcher(Fetcher):
    ats_type = "greenhouse"

    def fetch(self, firm):
        raise RuntimeError("boom")


class SuccessfulNotifier:
    sent = []

    def __init__(self, _config):
        pass

    def notify(self, digest):
        self.sent.append(digest)


class FakeEntryPageFetcher:
    def __init__(self, _client, target_years):
        assert target_years == [2027]

    def fetch_page(self, firm, _page_config):
        return [
            Posting(
                firm.name,
                "entry-1",
                "Entry-Level Recruiting",
                "United States",
                "https://firm.example/apply",
                "entrypage",
            )
        ]


def _write_yaml(path, data):
    path.write_text(yaml.safe_dump(data))


def _setup(tmp_path, monkeypatch, fetcher):
    firms = {"firms": [{"name": "Test Firm", "ats_type": "greenhouse", "ats_identifier": "tf"}]}
    _write_yaml(tmp_path / "firms.yaml", firms)
    # Reuse the repo config for realistic filters.
    import pathlib
    repo_cfg = pathlib.Path(main_mod.HERE / "config.yaml").read_text()
    (tmp_path / "config.yaml").write_text(repo_cfg)

    monkeypatch.setattr(main_mod, "build_registry", lambda client: {"greenhouse": fetcher})
    return tmp_path


def _args(tmp_path, **kw):
    argv = [
        "--config", str(tmp_path / "config.yaml"),
        "--firms", str(tmp_path / "firms.yaml"),
        "--db", str(tmp_path / "state.db"),
    ]
    for k, v in kw.items():
        flag = f"--{k.replace('_', '-')}"
        if isinstance(v, bool):
            if v:
                argv.append(flag)
        elif v is not None:
            argv.extend([flag, str(v)])
    return main_mod.parse_args(argv)


def test_dry_run_does_not_write_state(tmp_path, monkeypatch, capsys):
    posts = [
        Posting("Test Firm", "1", "First-Year Associate", "NY", "http://x/1", "greenhouse"),
        Posting("Test Firm", "2", "Lateral Partner", "NY", "http://x/2", "greenhouse"),
    ]
    tmp = _setup(tmp_path, monkeypatch, FakeFetcher(None, posts))
    rc = main_mod.run(_args(tmp, dry_run=True))
    assert rc == 0
    out = capsys.readouterr().out
    assert "First-Year Associate" in out
    assert "Lateral Partner" not in out  # filtered out
    # No DB should have been written to on dry-run.
    from core.diff import DiffStore
    with DiffStore(tmp / "state.db") as s:
        assert s.count() == 0
        assert s.shadow_count() == 0


def test_failed_send_is_audited_but_does_not_mark_seen(tmp_path, monkeypatch):
    posts = [Posting("Test Firm", "1", "Entry-Level Associate", "NY", "http://x/1", "greenhouse")]
    tmp = _setup(tmp_path, monkeypatch, FakeFetcher(None, posts))
    monkeypatch.delenv("SMTP_HOST", raising=False)
    rc = main_mod.run(_args(tmp, dry_run=False))
    assert rc == 1
    from core.diff import DiffStore
    with DiffStore(tmp / "state.db") as s:
        assert s.count() == 0
        assert s.list_notification_runs()[0].status == "failed"
    # It remains eligible for the next attempt.
    rc2 = main_mod.run(_args(tmp, dry_run=False))
    assert rc2 == 1
    with DiffStore(tmp / "state.db") as s:
        assert s.count() == 0
        assert len(s.list_notification_runs()) == 2


def test_successful_send_marks_state_and_is_idempotent(tmp_path, monkeypatch):
    posts = [Posting("Test Firm", "1", "Entry-Level Associate", "NY", "http://x/1", "greenhouse")]
    tmp = _setup(tmp_path, monkeypatch, FakeFetcher(None, posts))
    SuccessfulNotifier.sent = []
    monkeypatch.setattr(main_mod, "EmailNotifier", SuccessfulNotifier)
    monkeypatch.setattr(main_mod.SmtpConfig, "from_env", lambda: object())

    assert main_mod.run(_args(tmp, dry_run=False)) == 0
    from core.diff import DiffStore
    with DiffStore(tmp / "state.db") as s:
        assert s.count() == 1
        assert s.list_notification_runs()[0].status == "sent"

    assert main_mod.run(_args(tmp, dry_run=False)) == 0
    assert SuccessfulNotifier.sent[-1].match_count == 0
    with DiffStore(tmp / "state.db") as s:
        assert s.count() == 1


def test_seed_writes_state_without_email(tmp_path, monkeypatch):
    posts = [Posting("Test Firm", "1", "First-Year Associate", "NY", "http://x/1", "greenhouse")]
    tmp = _setup(tmp_path, monkeypatch, FakeFetcher(None, posts))
    monkeypatch.delenv("SMTP_HOST", raising=False)
    rc = main_mod.run(_args(tmp, seed=True))
    assert rc == 0  # seed never emails, so no SMTP failure
    from core.diff import DiffStore
    with DiffStore(tmp / "state.db") as s:
        assert s.count() == 1
    # A subsequent real run finds nothing new (backlog was seeded), but still
    # sends a heartbeat digest. No SMTP -> console fallback -> rc 1.
    rc2 = main_mod.run(_args(tmp, dry_run=False))
    assert rc2 == 1


def test_one_firm_failure_does_not_abort(tmp_path, monkeypatch):
    tmp = _setup(tmp_path, monkeypatch, ExplodingFetcher(None))
    rc = main_mod.run(_args(tmp, dry_run=True))
    assert rc == 0  # failure logged, run completes, nothing to notify


def test_history_does_not_fetch(tmp_path, monkeypatch, capsys):
    tmp = _setup(tmp_path, monkeypatch, ExplodingFetcher(None))
    assert main_mod.run(_args(tmp, history=5)) == 0
    assert "No exact notification audit rows yet." in capsys.readouterr().out


def test_unknown_ats_firm_still_polls_configured_entry_page(
    tmp_path, monkeypatch, capsys
):
    firms = {
        "firms": [
            {
                "name": "Entry Page Firm",
                "ats_type": "unknown",
                "careers_url": "https://firm.example/careers",
                "entry_pages": [
                    {
                        "url": "https://firm.example/careers",
                        "label": "Entry-Level Recruiting",
                    }
                ],
            }
        ]
    }
    _write_yaml(tmp_path / "firms.yaml", firms)
    import pathlib

    repo_cfg = pathlib.Path(main_mod.HERE / "config.yaml").read_text()
    (tmp_path / "config.yaml").write_text(repo_cfg)
    monkeypatch.setattr(main_mod, "EntryPageFetcher", FakeEntryPageFetcher)
    monkeypatch.setattr(main_mod, "build_registry", lambda _client: {})

    rc = main_mod.run(_args(tmp_path, dry_run=True))

    assert rc == 0
    output = capsys.readouterr().out
    assert "Entry-Level Recruiting" in output
    assert "https://firm.example/apply" in output
