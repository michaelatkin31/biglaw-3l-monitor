# DECISIONS.md

Assumptions, deferrals, and judgment calls made while building this
autonomously. Read alongside `README.md`.

## 1. Repository location

The project now lives in its intended standalone repository,
`michaelatkin31/biglaw-3l-monitor`. Workflows are at the repository root and the
daily monitor is active.

## 2. Classification verification

The initial bootstrap was search-derived because its build sandbox blocked
direct HTTPS. The registry was subsequently expanded and live-classified on
2026-07-20. The 28 recruiting pages added from the recipient reconciliation were
all fetched live on 2026-07-28; eleven require the existing Chromium fallback
because their CDNs reject plain HTTP clients. `classify.py` remains the refresh
tool for ATS fingerprints and should be rerun when a careers site migrates.

## 3. Firm list: union plus a reproducible reconciliation

The registry retains the larger **Vault Law 100 ∪ Am Law 200** working set rather
than replacing it with a smaller attachment. The recipient-supplied reference is
stored verbatim as 163 actual names in
`sources/yue_combined_vault_am_law.yaml`; its original numbering had a blank item
155.

`reconcile_firms.py --check` resolves that source against canonical registry
names and fails on an unresolved name, broken alias, or duplicate. Explicit
aliases document short brands and combinations. In particular:

- Lane Powell combined into Ballard Spahr and is not polled twice.
- Lewis Roca combined into Womble Bond Dickinson and is not polled twice.
- Ulmer & Berne combined into UB Greensfelder and is not polled twice.
- Locke Lord, Nexsen Pruet, and Waller Lansden likewise resolve to their current
  combined firms already in the registry.

Genuinely absent active firms were added without removing the 67 registry-only
firms, because the attachment is useful reconciliation evidence but not a safe
authoritative deletion list.

## 4. Known coverage ceiling and entry-page policy

Of 226 canonical firms, 154 have a supported ATS fetcher. Another 28 have an
official recruiting page monitor, yielding 182 firms with at least one active
source; 44 still have no pollable public source.

Recruiting pages do not emit a posting merely because they advertise a law
student or summer program. They require a 3L/entry-level phrase paired with
open-application evidence, or a target-class-year opportunity. Target-year
1L/2L summer-associate pages are explicitly rejected. A stable fingerprint of
the relevant application link and evidence provides idempotency and permits a
new alert when the application path actually changes.

School-gated OCI, Symplicity, and private Flo Recruit flows remain an unavoidable
ceiling: an application that never appears on a public board or page cannot be
detected by this monitor.

## 5. State / diff model

`state.db` (SQLite) has four distinct responsibilities:

- `seen_jobs` records only successfully sent or explicitly seeded precision
  matches, keyed by `(firm, job_id)`.
- `notification_runs` records the exact rendered digest and attempt status.
- `notification_items` records the postings and reasons included in that digest.
- `shadow_jobs` upserts broader recall candidates that were intentionally kept
  out of the recipient-facing email.

The pending audit row is created before SMTP. Only after SMTP accepts the
message are its postings and the `sent` status committed in one transaction.
On failure, the audit becomes `failed` and seen-state is untouched, so the
postings retry on the next run. A `pending` row can also expose a process crash
that happened between rendering and completion.

Exact recipient addresses are intentionally omitted because the repository and
database are public. Existing `seen_jobs` rows are retained as legacy evidence,
but their original digest body and delivery outcome cannot be reconstructed.

## 6. Notification policy

- **Always sends a digest, every run** — including empty days, where it emails a
  short "no new postings" note. This makes a delivered email double as a heartbeat
  confirming the monitor ran, rather than leaving silence ambiguous between "nothing
  new" and "the job broke." (Originally silent on empty days; changed by request.)
- **Precision-first daily digest**: only titles with an explicit 3L,
  first-year, entry-level, incoming/new-associate, or configured target-class-year
  signal are emailed. Bare `associate` / `attorney` / `lawyer` titles are no
  longer recipient-facing. A July 22-28 replay reduced 76 historical candidates
  to one applicable precision match. This deliberately accepts more false
  negatives in exchange for making the daily email useful rather than noisy.
- **Target year is configuration** (`target_class_years: [2027]`). Generic
  `First-Year Associate` remains eligible, while an explicitly conflicting title
  such as `2026 First Year Associate` is rejected.
- **Shadow recall remains broad**: bare associate/attorney/lawyer titles that
  survive the staff, experience, summer, and geography gates are retained in
  `shadow_jobs`. This makes the precision tradeoff measurable without sending
  those roles to the recipient.
- `notify.py` splits **rendering** (`render_digest`) from **delivery**
  (`EmailNotifier` / `ConsoleNotifier`) behind a `Notifier` protocol, so a future
  read-only web UI over `state.db` — or a Slack channel — can reuse the renderer.
- **First-run backfill**: the first real run would email *every* currently-open
  matching posting. `--seed` writes state without emailing so you can start clean.

## 7. Fetcher specifics

- **Workday host resolution**: the public host includes a data-center number
  (`{tenant}.wdN.myworkdayjobs.com`) that isn't derivable from the tenant.
  `classify.py` captures it and pins `workday_host` in `firms.yaml`. If a firm is
  configured with only `tenant/site`, the fetcher probes a short, fixed list of
  data-center subdomains once and logs the winner (so it can be pinned) — kept
  deliberately small to avoid hammering Workday.
- **Workday `posted_date`** is a relative string ("Posted 5 Days Ago") — the CXS
  jobs list carries no absolute date. It's stored as-is (informational only;
  identity is `(firm, job_id)`).
- **Generic fetcher** extracts schema.org `JobPosting` in both standard
  encodings: `application/ld+json` blocks (what most ATSs emit for Google-for-Jobs
  SEO) and inline **microdata** (`itemtype=".../JobPosting"` + `itemprop` on the
  rendered cards). The microdata path is what makes some WordPress careers
  front-ends pollable over plain HTTP — e.g. Kilpatrick's
  `kilpatrickrecruits.com/open-positions/` mirrors its iCIMS jobs into static
  microdata cards, so it needs no browser despite an earlier note calling it "not
  pollable." Playwright is **optional** and used only for firms explicitly marked
  `render: playwright`, to keep the CI run light.
- **Browser fetcher: empty ≠ blocked.** It used to raise (a run failure) whenever
  it found zero job links, conflating "page rendered fine but has no current
  openings" (common for small firms — e.g. Harter Secrest on some days) with
  "we were bot-walled / the page failed to load." Now `_looks_blocked` checks the
  HTTP status, Cloudflare/bot-wall challenge markers, and whether the body has any
  real text: a rendered-but-empty board returns `[]` (no failure), while a genuine
  block still raises. Firms behind an *intermittent* wall can set
  `tolerate_block: true` to downgrade even a block to a logged skip — used for
  **Buchanan** (bipc.com is Cloudflare-walled ~2/3 of days; its real ATS is
  `buchanan.viglobalcloud.com` viRecruit, but the listing needs a per-firm `Tag`
  GUID only reachable from the walled careers page, so it isn't cleanly pollable
  yet — capture that tagged URL to switch it to `ats_type: virecruit`).

## 7b. Cutting lateral noise (description experience gate + normalization fixes)

Live data (the postings emailed over two days) showed the recall-first net was
~95% lateral: of 34 emails, exactly one was genuinely entry-level. Almost every
lateral was a bare "X Associate" title whose real "N years" requirement lived in
the **description**, not the title. Response:

- The first response was a description experience gate, but later recipient
  feedback showed title-only ATSs still produced too much noise. The final policy
  is therefore precision-first: the experience gate remains a defensive check,
  but ambiguous titles never reach the daily email in the first place.

- **Description experience gate** (`core/filter.py` + `description_exclude_regexes`
  in config). When a fetcher supplies a description, a stated years-of-experience
  floor there disqualifies a seniority-silent title — unless an entry signal is
  present anywhere in title+description. Kept **number-bearing only** (the bare
  "years of experience" phrase is in nearly every description, entry-level
  included) and **recall-safe** (floors starting at 0–1 are kept; no description
  ⇒ never dropped by this gate). Patterns cover digits, ranges, "at least N",
  and spelled-out counts ("two to four years", which slipped a digit-only first
  cut). `Posting.description` is transient — used by the filter, **not** persisted
  to `state.db` (identity is still `(firm, job_id)`), so no schema change.
- **Description sources**: Greenhouse `content`, Lever/Ashby `descriptionPlain`,
  career.page/jsonapi `description`, JSON-LD `description`. Fetchers whose listing
  carries no body (Workday CXS, viRecruit, Radancy, browser, microdata) are
  title-only unless a firm opts into `fetch_description: true` (generic fetcher
  pulls each detail page; enabled for Kilpatrick, whose microdata cards omit the
  body but whose detail pages state the floor). Measured effect on the
  description-bearing firms: ~200 lateral roles gated out of ~550 fetched, and
  Orrick/Seyfarth/Kilpatrick went from 9 lateral false-positives to 0.
- **Normalization fixes** surfaced by the same data: WP-JSON `{'rendered': …}`
  title/content wrappers are unwrapped (Dinsmore no longer emits a dict as its
  title); a leading req-number prefix ("1029 - …") is stripped; the US-only geo
  gate now also scans the **title** (Baker McKenzie renders the office there and
  leaves location blank — Zurich/Geneva/London roles were slipping through).
- **Digest dedup**: the same visible role arriving under two job_ids (both "new")
  now renders once, and the subject count reflects the deduped total.
- **Firm-aware US policy**: known US-focused boards retain the recall-safe
  foreign-marker rule, but a global board may set
  `location_policy: require_us`. Baker McKenzie uses it because a global-board
  title can have blank structured location and an office not present in any
  finite blacklist (Bogotá was the motivating example). A bare `US` token is not
  affirmative office evidence because titles like "US / International Tax
  Lawyer (Zurich)" describe a practice, not a location.

Known ceiling: the big remaining un-gated buckets are Workday, viRecruit, and
browser firms (no listing body, and viRecruit/browser have no cheap per-job URL
to fetch). Extending the gate there (Workday has a CXS job-detail endpoint) is a
sensible follow-up.

## 8. Runner / polite fetching

- Daily cron at **08:17 UTC** (~4 AM ET; ~3 AM in winter). Deliberately off the
  hour: GitHub throttles the flood of jobs scheduled at `:00`, and the old
  `0 12` run routinely fired ~90 min late (~10 AM ET). GitHub cron is UTC, so a
  fixed local hour year-round isn't possible; edit the cron to shift it.
- `state.db` is **committed back** to the repo by the Action (bot commit, empty-
  commit guarded), including after failed monitor steps so the failed-attempt
  audit survives. Chosen over Actions cache/artifact for simplicity and zero
  config for a personal tool.
- HTTP client: real browser User-Agent, 20s timeouts, 3 retries with exponential
  backoff, retryable on 429/5xx (incl. Workday's POST). Firms are fetched
  sequentially (small, polite concurrency).

## 9. Deferred / not done

- **44 firms still lack a public, pollable ATS or recruiting page.** School-only
  and authenticated recruiting systems cannot be covered without new access.
- ATS fingerprints can still drift; `classify.py` remains a periodic
  maintenance tool rather than a runtime guesser.
- There is no web UI over the audit tables; `python main.py --history [N]` is the
  current review surface.
