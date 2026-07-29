from core.diff import DiffStore
from core.models import Posting, RunSummary
from core.notify import render_digest


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


def test_successful_notification_atomically_marks_seen(tmp_path):
    db = tmp_path / "state.db"
    posts = [_p("1")]
    digest = render_digest(posts, RunSummary())
    with DiffStore(db) as store:
        run_id = store.begin_notification(
            digest,
            "test summary",
            posts,
            match_reasons={posts[0].key(): "included by entry-level"},
            score_fn=lambda _posting: 3,
        )
        assert store.count() == 0
        assert store.list_notification_runs()[0].status == "pending"

        store.finish_notification(run_id, posts)

        assert store.count() == 1
        audit_run = store.list_notification_runs()[0]
        assert audit_run.status == "sent"
        items = store.notification_items(run_id)
        assert items[0]["title"] == "First-Year Associate"
        assert items[0]["entry_score"] == 3


def test_failed_notification_remains_retryable(tmp_path):
    db = tmp_path / "state.db"
    posts = [_p("1")]
    digest = render_digest(posts, RunSummary())
    with DiffStore(db) as store:
        run_id = store.begin_notification(digest, "summary", posts)
        store.fail_notification(run_id, "smtp unavailable")

        assert store.count() == 0
        assert store.select_unseen(posts) == posts
        audit_run = store.list_notification_runs()[0]
        assert audit_run.status == "failed"
        assert audit_run.error == "smtp unavailable"


def test_shadow_candidates_are_upserted_not_duplicated(tmp_path):
    db = tmp_path / "state.db"
    shadow = _p("shadow", title="Corporate Associate")
    digest = render_digest([], RunSummary())
    with DiffStore(db) as store:
        first_run = store.begin_notification(
            digest,
            "summary",
            [],
            shadow_postings=[shadow],
            shadow_reasons={shadow.key(): "included by keyword: 'associate'"},
            now="2026-07-28T10:00:00+00:00",
        )
        store.finish_notification(first_run, [], now="2026-07-28T10:01:00+00:00")
        second_run = store.begin_notification(
            digest,
            "summary",
            [],
            shadow_postings=[shadow],
            now="2026-07-29T10:00:00+00:00",
        )
        store.finish_notification(second_run, [], now="2026-07-29T10:01:00+00:00")

        assert store.shadow_count() == 1
        assert store.list_notification_runs()[0].shadow_count == 1


def test_seed_is_audited_and_marks_seen(tmp_path):
    db = tmp_path / "state.db"
    posts = [_p("1")]
    digest = render_digest(posts, RunSummary())
    with DiffStore(db) as store:
        run_id = store.begin_notification(digest, "summary", posts)
        store.finish_notification(run_id, posts, status="seeded")

        assert store.count() == 1
        assert store.list_notification_runs()[0].status == "seeded"
