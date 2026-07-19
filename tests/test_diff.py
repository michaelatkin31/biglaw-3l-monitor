from core.diff import DiffStore
from core.models import Posting


def _p(job_id, title="First-Year Associate", firm="Firm"):
    return Posting(
        firm=firm, job_id=job_id, title=title, location="NY",
        url=f"http://x/{job_id}", ats="greenhouse",
    )


def test_new_then_seen(tmp_path):
    db = tmp_path / "state.db"
    with DiffStore(db) as store:
        first = [_p("1"), _p("2")]
        unseen = store.select_unseen(first)
        assert len(unseen) == 2
        store.mark_seen(unseen)
        # Re-running the same set yields nothing new (idempotent).
        assert store.select_unseen(first) == []


def test_only_new_ids_surface(tmp_path):
    db = tmp_path / "state.db"
    with DiffStore(db) as store:
        store.mark_seen([_p("1")])
        unseen = store.select_unseen([_p("1"), _p("2"), _p("3")])
        assert {p.job_id for p in unseen} == {"2", "3"}


def test_same_id_different_firm_is_distinct(tmp_path):
    db = tmp_path / "state.db"
    with DiffStore(db) as store:
        store.mark_seen([_p("1", firm="A")])
        unseen = store.select_unseen([_p("1", firm="A"), _p("1", firm="B")])
        assert {p.firm for p in unseen} == {"B"}


def test_persists_across_connections(tmp_path):
    db = tmp_path / "state.db"
    with DiffStore(db) as store:
        store.mark_seen([_p("1")])
    with DiffStore(db) as store2:
        assert store2.select_unseen([_p("1")]) == []
        assert store2.count() == 1
