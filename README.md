# BigLaw 3L / Entry-Associate Posting Monitor

Monitors the public careers pages of large US law firms (the **Vault Law 100 ∩
Am Law 200** intersection) and emails a **daily digest** when new *3L /
first-year / entry-level associate* postings appear.

Personal, low-traffic tool. Optimized for reliability and a low false-positive
rate — not scale.

---

## How it works

```
firms.yaml            # registry: name, ats_type, per-ATS identifiers
config.yaml           # http settings + include/exclude keyword filters (tunable)
fetchers/
  greenhouse.py       # boards-api.greenhouse.io public JSON
  lever.py            # api.lever.co public JSON
  workday.py          # POST .../wday/cxs/{tenant}/{site}/jobs (JSON, paginated)
  generic.py          # HTML fallback: schema.org JobPosting JSON-LD + microdata (+ optional Playwright)
core/
  models.py           # the normalized Posting shape
  normalize.py        # raw ATS payload -> {firm, job_id, title, location, url, posted_date, ats}
  filter.py           # include/exclude keyword + class-year logic
  diff.py             # SQLite: track seen (firm, job_id)
  notify.py           # email digest (SMTP) behind a small Notifier interface
  http.py             # polite client: real UA, timeouts, retry+backoff
main.py               # orchestrates: fetch -> normalize -> filter -> diff -> notify
classify.py           # helper: auto-detect each firm's ATS from its careers page
```

**Core architecture principle:** one fetcher per ATS backend, *not* one scraper
per firm. Each firm is classified once (by `ats_type` in `firms.yaml`) and routed
to the matching fetcher at runtime. Firms with `ats_type: unknown` are skipped
(never crash the run).

The pipeline per run:

1. **Fetch** each firm via its ATS fetcher (per-firm `try/except` — one firm
   failing never aborts the run).
2. **Normalize** every posting to `{firm, job_id, title, location, url,
   posted_date, ats}`.
3. **Filter** by include/exclude keywords + class-year regexes + a description
   experience gate (see below).
4. **Shadow-audit** broader associate/attorney/lawyer candidates in `state.db`
   without putting them in the daily email.
5. **Diff** precision matches against `state.db` — a posting is *new* if
   `(firm, job_id)` has not been successfully sent or seeded.
6. **Notify** — one precision-only email digest every run, including a short
   heartbeat on empty days. The exact subject, text, HTML, included postings,
   and SMTP outcome are recorded before seen-state is advanced.

---

## Coverage: what actually gets polled

`firms.yaml` now contains **226 canonical firms**:

- **154** have one of the 11 supported ATS/board fetchers.
- **28** additional firms from the recipient-supplied list have an official US
  recruiting page monitored for an explicit open 3L / entry-level signal.
- **182 firms total** therefore have at least one active source; **44** remain
  listed but lack a currently pollable public source.

The supplied combined Vault/Am Law reference is preserved at
`sources/yue_combined_vault_am_law.yaml`. It contains **163 actual names** (the
original numbering had a blank item 155), all of which resolve to the registry.
Short names and absorbed firms are explicit aliases, so Lane Powell is not
polled separately from Ballard Spahr, Lewis Roca is not polled separately from
Womble Bond Dickinson, and Ulmer & Berne is not polled separately from UB
Greensfelder.

Recruiting landing pages are not treated as open jobs merely because they say
“Law Students.” The page monitor requires an entry-level/3L signal plus
application-open evidence, or a target-year opportunity; it also rejects
target-year 1L/2L summer-associate programs. Eleven bot-protected pages use the
existing headless Chromium fallback. All 28 configured pages were live-tested
when added.

Generate the executable reconciliation and coverage report with:

```bash
python reconcile_firms.py --check
```

Public boards still skew heavily toward experienced lawyers, and many elite
firms hire new graduates through OCI or school-gated systems. Treat this monitor
as one signal, not a substitute for school recruiting systems and direct checks.

**Verify / refresh the classification** (optional but recommended) from any
machine with open outbound HTTPS:

```bash
python classify.py            # dry report: what ATS it detects per firm
python classify.py --write    # detect AND update firms.yaml in place
```

Or trigger the **`classify-firms`** GitHub Action in your fork (Actions →
classify-firms → Run workflow) — it re-probes and commits `firms.yaml`.
`classify.py` never guesses tokens; firms it can't fingerprint stay `unknown`.

---

## Run locally

```bash
pip install -r requirements.txt

# Validate end-to-end WITHOUT sending mail or touching state:
python main.py --dry-run

# One firm, verbose (shows every fetched-but-filtered posting at DEBUG).
# Use a Greenhouse/Workday firm -- others have no public fetcher and just skip:
python main.py --firm "Cooley" --dry-run -v

# Seed state on first setup so the first real run doesn't email the whole
# current backlog:
python main.py --seed

# Real run (needs SMTP env vars, see below):
python main.py

# Inspect exact recent delivery attempts plus legacy seen rows:
python main.py --history       # latest 20
python main.py --history 100
```

CLI flags: `--dry-run`, `--seed`, `--history [N]`, `--firm NAME` (repeatable),
`--limit N`, `--config`, `--firms`, `--db`, `-v/--verbose`.

---

## Email / secrets

Delivery is SMTP, configured entirely via environment variables — **no secrets
in code**. A Gmail app-password works well.

| Env var      | Required | Notes |
|--------------|----------|-------|
| `SMTP_HOST`  | yes      | e.g. `smtp.gmail.com` |
| `SMTP_PORT`  | no       | default `587` (STARTTLS). `465` → implicit TLS |
| `SMTP_USER`  | no*      | required if your server needs auth (Gmail does) |
| `SMTP_PASS`  | no*      | Gmail: an **app password**, not your login password |
| `EMAIL_TO`   | yes      | recipient; comma-separated for multiple |
| `EMAIL_FROM` | yes      | sender address |

In GitHub Actions, set these as **repository secrets** (Settings → Secrets and
variables → Actions). The workflow maps each secret to the matching env var.

---

## Scheduled runs (GitHub Actions)

`.github/workflows/monitor.yml` runs daily at **08:17 UTC** (about 4:17 AM ET
during EDT / 3:17 AM during EST) and also supports manual runs. The off-hour
minute avoids GitHub's heavier on-the-hour cron queue.

State persistence: the workflow commits the updated `state.db` back to the repo
after each run. The persistence step also runs after a monitor failure so a
failed-send audit survives, while the included postings remain eligible for the
next successful delivery.

### Notification audit

`state.db` now contains:

- `notification_runs`: exact subject/text/HTML, summary, counts, timestamps,
  and `pending` / `sent` / `failed` / `seeded` status.
- `notification_items`: every posting included in a digest, with its filter
  reason and entry score.
- `shadow_jobs`: broader recall candidates excluded from recipient-facing mail,
  upserted by `(firm, job_id)` so they do not grow once per day.
- `seen_jobs`: only successfully sent or explicitly seeded postings.

Recipient addresses and SMTP credentials are deliberately not stored because
this repository and its committed SQLite database are public. `sent` means the
configured SMTP server accepted the message; it cannot prove downstream inbox
delivery. Rows already in `seen_jobs` predate exact digest auditing, but
`--history` still displays recent legacy examples for reference.

> **Activating the schedule:** GitHub only runs workflows found at the repository
> root's `.github/workflows/`. If you keep this project as a subdirectory, move
> it to its own repo (or hoist `.github/` to the repo root) for the cron to fire.

---

## Tuning the filter

All keyword/regex lists live in `config.yaml` under `filters:` — no code edits
needed. The daily email filter is **precision-first**:

- **Include** (any match): explicit new-grad signals such as `first-year
  associate`, `entry-level associate`, `3L hiring`, `incoming associate`, and
  the configured `target_class_years` (currently `2027`). Bare `associate`,
  `attorney`, and `lawyer` titles are deliberately not emailed.
- **Class-year conflict rule**: a generic `First-Year Associate` remains
  eligible, but a title that explicitly names a non-target year (for example,
  `2026 First Year Associate`) is rejected.
- **Recruiting landing titles**: narrowly anchored `Entry-Level Recruiting`,
  `Entry-Level Associate Opportunities`, and `3L Hiring` titles may override
  the generic `recruiting` staff exclusion. A title such as `Entry-Level
  Recruiting Coordinator` remains excluded.
- **Exclude** (any match wins over include): seniority a 3L can't fill
  (`senior`, `mid-level`, `of counsel`, `partner`, `lateral`, `experienced`);
  non-attorney staff titles (`paralegal`, `coordinator`, `manager`, `analyst`,
  `recruiting`, `conflicts`, …); and foreign-qualification words (`solicitor`,
  `trainee`, `m/w/d`, `rechtsanwalt`, …).
- **Description experience gate** (`experience_gate_description: true`): the big
  lever against lateral noise. BigLaw boards rarely put seniority in the *title*
  (a lateral role is just "Corporate Associate"); the "3+ years" requirement
  lives in the body. When a fetcher provides a description, a stated
  years-of-experience floor there drops a title that is silent about seniority —
  unless an entry signal is present anywhere. Handles digits, ranges, "at least
  N", and spelled-out counts ("two to four years"). Recall-safe: floors starting
  at 0–1 ("0-2 years") are kept, and a posting with no description is never
  dropped by this gate. Descriptions come from Greenhouse/Lever/Ashby/career.page/
  jsonapi/JSON-LD listings; fetchers whose listing has no body (Workday CXS,
  viRecruit, Radancy, browser, microdata) are **title-only** unless the firm sets
  `fetch_description: true` (generic fetcher only — pulls each detail page; used
  for Kilpatrick). Tune via `description_exclude_regexes`.
- **US-only geo gate** (`us_only: true`): ordinary US-focused boards drop known
  foreign-only locations but retain ambiguous values. Global boards can set
  `location_policy: require_us`; those postings must affirmatively name a
  configured US city/state/country marker. Baker McKenzie uses this stricter
  policy because its global board often leaves location blank and has emitted
  Amsterdam, Zurich, London, and Bogotá roles.
- **Summer associate**: excluded by default (a graduated 3L's summer window has
  passed); flip `include_summer_associate: true` to include 2L summer programs.
Every fetched-but-filtered posting is logged at DEBUG (`-v`) so you can audit the
false-negative rate.

---

## Adding / reclassifying a firm

Edit `firms.yaml`:

```yaml
- name: "Example LLP"
  careers_url: "https://www.example.com/careers"
  ats_type: greenhouse          # greenhouse|lever|workday|generic|careerpage|smartrecruiters|unknown
  ats_identifier: "examplellp"  # GH token | Lever slug | "tenant/site" (Workday) | career.page subdomain | SmartRecruiters company id
  location_policy: require_us   # optional: global board must name a US office
  entry_pages:                  # optional: additional 3L/entry recruiting page
    - url: "https://www.example.com/careers/law-students"
      label: "Entry-Level Recruiting"
      render: true              # only when plain HTTP is blocked/JS-only
      tolerate_block: true      # optional: intermittent CDN block => skip, not fail
  public_entry_level: unknown
  note: ""
```

Optional per-firm flags (any extra key lands in the firm's `options`):

- `fetch_description: true` — **generic** fetcher only: pull each posting's detail
  page so the description experience gate can act on it (used where the listing
  cards omit the body, e.g. Kilpatrick). Adds one request per posting.
- `tolerate_block: true` — **browser** fetcher only: treat a bot-wall block
  (Cloudflare "Just a moment…", etc.) as a logged skip rather than a run failure.
  For sites behind an intermittent wall (e.g. Buchanan) so they stop failing daily
  but still succeed on days the wall lets a headless browser through.
- `render: playwright` — **generic** fetcher: render a JS-only page before parsing.

Per-ATS `ats_identifier`:

- **greenhouse** — the board token (`boards.greenhouse.io/{token}`)
- **lever** — the company slug (`jobs.lever.co/{company}`)
- **workday** — `"tenant/site"`. Optionally pin `workday_host:
  tenant.wdN.myworkdayjobs.com` to skip data-center probing.
- **generic** — leave `null`; the fetcher reads `careers_url` and extracts
  schema.org `JobPosting` from both JSON-LD blocks and inline microdata cards.
  For a truly JS-rendered page add `render: playwright` (and install Playwright —
  see `requirements.txt`).

Or just run `python classify.py --firm "Example LLP" --write`.

---

## Testing

```bash
python -m pytest -q
```

Unit tests cover normalization, filtering, diffing, the generic JSON-LD and
microdata parsers, the ATS-detection logic, and the full orchestration (with a
fake fetcher — no network).
