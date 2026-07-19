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


def test_real_run_writes_state_and_is_idempotent(tmp_path, monkeypatch):
    posts = [Posting("Test Firm", "1", "Entry-Level Associate", "NY", "http://x/1", "greenhouse")]
    tmp = _setup(tmp_path, monkeypatch, FakeFetcher(None, posts))
    # Force console notifier path by not setting SMTP env; dry-run=False but no
    # SMTP -> EmailNotifier raises, main falls back to console + returns 1.
    monkeypatch.delenv("SMTP_HOST", raising=False)
    rc = main_mod.run(_args(tmp, dry_run=False))
    # First run: one new match, email fails (no SMTP) -> rc 1 but state written.
    assert rc == 1
    from core.diff import DiffStore
    with DiffStore(tmp / "state.db") as s:
        assert s.count() == 1
    # Second run: nothing new -> rc 0 (silent).
    rc2 = main_mod.run(_args(tmp, dry_run=False))
    assert rc2 == 0


def test_seed_writes_state_without_email(tmp_path, monkeypatch):
    posts = [Posting("Test Firm", "1", "First-Year Associate", "NY", "http://x/1", "greenhouse")]
    tmp = _setup(tmp_path, monkeypatch, FakeFetcher(None, posts))
    monkeypatch.delenv("SMTP_HOST", raising=False)
    rc = main_mod.run(_args(tmp, seed=True))
    assert rc == 0  # seed never emails, so no SMTP failure
    from core.diff import DiffStore
    with DiffStore(tmp / "state.db") as s:
        assert s.count() == 1
    # A subsequent real run finds nothing new (backlog was seeded).
    rc2 = main_mod.run(_args(tmp, dry_run=False))
    assert rc2 == 0


def test_one_firm_failure_does_not_abort(tmp_path, monkeypatch):
    tmp = _setup(tmp_path, monkeypatch, ExplodingFetcher(None))
    rc = main_mod.run(_args(tmp, dry_run=True))
    assert rc == 0  # failure logged, run completes, nothing to notify
