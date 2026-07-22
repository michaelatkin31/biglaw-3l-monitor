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
3. **Filter** by include/exclude keywords + class-year regexes (see below).
4. **Diff** against `state.db` — a posting is *new* if `(firm, job_id)` hasn't
   been seen **and** it passes the filter. Re-running the same day emails
   nothing (idempotent).
5. **Notify** — one grouped-by-firm email digest. Silent on empty days.

---

## Coverage: what actually gets polled

`firms.yaml` ships with **198 firms** — the **union of the Vault Law 100 and the
Am Law 200** (expanded 2026-07-20 from the earlier ~75-firm intersection).
Every firm's ATS was **live-verified** (careers page fetched, candidate JSON/HTML
endpoint hit, board titles inspected for real attorney roles vs the staff-board trap).

Nine ATS backends have working fetchers, so the monitor actively polls
**85 firms** (up from ~22 originally):

- **48 viRecruit** (`vi by Aderant`) — the biggest source of coverage. vi's *Apply*
  step is login-gated, but the **listing page is public HTML**
  (`viRecruitSelfApply/RecDefault.aspx` or `ReDefault.aspx`), which the `virecruit`
  fetcher parses. Spans the top tier (Cleary, Jones Day, O'Melveny, Milbank, Boies
  Schiller, Akin Gump…) and much of the Am Law tail (Faegre Drinker, Littler,
  Cozen, Duane Morris, Foley, Fox Rothschild, Akerman…).
- **28 Workday** — Skadden, Simpson Thacher, Cooley, McDermott, Hogan Lovells,
  DLA Piper, Perkins Coie, Greenberg Traurig, Pillsbury, HSF Kramer, Jackson
  Lewis, Fragomen, … (all with `workday_host` pinned).
- **3 Greenhouse** (Fried Frank, Hughes Hubbard, …) · **2 career.page** (Jibe —
  Morrison & Foerster, Ogletree) · **1 Ashby** (Barnes & Thornburg) ·
  **1 SmartRecruiters** (Crowell & Moring) · **1 Radancy** (A&O Shearman) ·
  **2 generic** (Bond Schoeneck JSON-LD; Kilpatrick microdata).

The Am Law tail (ranks ~50-200) leans far more on these pollable ATSs — and posts
entry-level / 2L-summer roles publicly — much more than the gated elite core, so
the union expansion roughly **doubled** genuine coverage.

The remaining ~35 firms have **no pollable endpoint**: `viglobal`/self-hosted
viRecruit behind Cloudflare or broken TLS (Kirkland, Paul Hastings, Willkie),
iCIMS / Taleo / LawCruit / Avature, a Cloudflare-gated Workday tenant (Paul
Weiss), or email-only (Cravath, Davis Polk, Sullivan & Cromwell, Susman, Quinn
Emanuel). A couple (Wachtell, Proskauer) expose a public API but it's a
**staff-only board** with zero attorneys, so polling it would be noise. See
`DECISIONS.md` §4.

Two ATSs were investigated with a headless browser and rejected: **Flo Recruit**
(the job board is disabled for 5/7 of our firms — they use it only for OCI event
forwarding — and the 2 with it enabled were empty) and **iCIMS attorney boards**
(login-gated, e.g. `lw.icims.com` → `login.icims.com`; only the *staff* iCIMS is
public). Headless rendering works fine; the blockers are auth and absent data, so
a browser doesn't help. (A&O Shearman's Radancy site, by contrast, is public
server-rendered HTML — hence pollable with plain HTTP.)

> **Reality for a job-seeker:** even among the 40 polled firms, live data shows the
> public boards are overwhelmingly *lateral* (experienced) associate roles.
> Genuine entry-level postings ("first-year", "class of 202X", "post-clerkship")
> are rare on public boards. The filter is tuned **recall-first** (see
> "Tuning the filter") so the rare real one is never missed, at the cost of some
> lateral roles in the digest. Treat this tool as *one* strong signal, not a
> substitute for OSCAR (clerkships), NALP, and direct firm-by-firm checks.

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
```

CLI flags: `--dry-run`, `--seed`, `--firm NAME` (repeatable), `--limit N`,
`--config`, `--firms`, `--db`, `-v/--verbose`.

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

`.github/workflows/monitor.yml` runs daily at **12:00 UTC** (= 8:00 AM ET during
EDT / 7:00 AM ET during EST — GitHub cron is always UTC; edit the cron for a
fixed local hour) and also supports **manual runs** (`workflow_dispatch`).

State persistence: the workflow commits the updated `state.db` back to the repo
after each run (bot commit, guarded against empty commits) so the next run diffs
against it. Simple and free for a personal tool; swap to an Actions cache/artifact
if you'd rather not commit a binary.

> **Activating the schedule:** GitHub only runs workflows found at the repository
> root's `.github/workflows/`. If you keep this project as a subdirectory, move
> it to its own repo (or hoist `.github/` to the repo root) for the cron to fire.

---

## Tuning the filter

All keyword/regex lists live in `config.yaml` under `filters:` — no code edits
needed. The filter is **recall-first** by design (this is a primary job-search
net; missing a real posting is worse than showing a lateral one):

- **Include** (any match): the bare fee-earner words `associate` / `attorney` /
  `lawyer` cast the wide net, plus explicit entry signals (`first-year
  associate`, `entry-level associate`, `3L`, `junior associate`, `judicial
  clerk`, class-year regexes like `Class of 2027` / `Class Years 2026`).
- **Exclude** (any match wins over include): seniority a 3L can't fill
  (`senior`, `mid-level`, `of counsel`, `partner`, `lateral`, `experienced`);
  non-attorney staff titles (`paralegal`, `coordinator`, `manager`, `analyst`,
  `recruiting`, `conflicts`, …); and foreign-qualification words (`solicitor`,
  `trainee`, `m/w/d`, `rechtsanwalt`, …).
- **US-only geo gate** (`us_only: true`): drops postings whose location names a
  foreign place and no US place (kills the London/Frankfurt/Singapore trainee
  tail). Recall-safe — ambiguous locations ("3 Locations", a bare US city) are
  kept. Tune via `us_location_markers` / `foreign_location_markers`.
- **Summer associate**: excluded by default (a graduated 3L's summer window has
  passed); flip `include_summer_associate: true` to include 2L summer programs.
- **Precision mode**: for far fewer, higher-confidence emails, delete
  `associate`/`attorney`/`lawyer` from `include_keywords` — the explicit entry
  signals then do the matching (but generically-titled entry roles get missed).

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
  public_entry_level: unknown
  note: ""
```

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
