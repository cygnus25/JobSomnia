# RESEARCH.md — improvement options for ai-job-scraper

Researched 2026-09-06. Every load-bearing claim links to a real source (official docs, or a live API probe I ran against the actual endpoints — marked **[live]**). Recommendations ranked by value/effort for a solo junior dev at the end.

---

## TL;DR

| # | Recommendation | Value | Effort | Why |
|---|----------------|-------|--------|-----|
| 1 | Capture salary fields the APIs **already return** (Remotive `salary`, RemoteOK `salary_min`/`salary_max`) | High | ~30 min | Free data sitting in responses `sources_api.py` already fetches and drops on the floor |
| 2 | ntfy.sh run-finished notification | High | <1 h | No account, one `urllib` POST, phone/desktop push; perfect fit for the scheduler backlog |
| 3 | LLM salary extraction in the existing scoring pass | High | ~2–4 h | Verified benchmarks: small models extract salary from description text at ~100% accuracy for ~$0.001/posting; plugs into `run_claude_json` |
| 4 | Add Greenhouse + Lever watchlist boards | High | ~half day | Public, no-auth JSON APIs; NZ-relevant boards verified live (Rocket Lab, NZTE, ClearPoint, KPMG NZ, Enable) |
| 5 | Add Adzuna NZ as an API source | Medium | ~half day | Free key, NZ endpoint, structured `salary_min/max`, hospitality category; 2,500 hits/mo covers a daily run |
| 6 | Firecrawl 429/Retry-After backoff + credit accounting | Medium | ~2 h | Free plan: 10 req/min, 2 concurrent, 1,000 credits/mo; a JSON-mode scrape burns 5 credits/page |
| 7 | SQLite run history + incremental dedupe | Medium-high | 1–2 d | Kills the "crash mid-run loses seen.json" failure mode; enables run-history views; stdlib `sqlite3` |
| 8 | Add `seek.co.nz` to `job_boards` (scrape, not API) | Medium | ~10 min | SEEK's real API requires partner approval; Firecrawl scrape is the only path |
| 9 | Jooble NZ API | Low | ~1 h + key | Works for NZ (`nz.jooble.org`) but free key is capped at **500 requests lifetime** — burns out fast |
| 10 | Trade Me Jobs API | Medium | 1–2 d | THE NZ-native board with salary filters, but requires OAuth app registration — more setup than its early value justifies |

---

## 1. Free job-board APIs beyond what exists

Existing API sources: Remotive, RemoteOK, Arbeitnow (`jobscraper/sources_api.py`). Findings per candidate:

### Strong fits

**Greenhouse Job Board API — no auth, live-verified NZ content.**
- `GET https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs` — "Job Board data is publicly available, so authentication is not required for any GET endpoints." `?content=true` adds the full HTML job description, `first_published`, and structured `location`/`offices`. ([docs.greenhouse.io/job-board.html](https://docs.greenhouse.io/job-board.html))
- **[live]** `boards-api.greenhouse.io/v1/boards/rocketlab/jobs?content=true` → 456 jobs, including postings located `Auckland, NZ` (verified response JSON).
- No structured salary field — salary only appears if the employer wrote it into the description HTML (see §2).

**Lever Postings API — no key, live-verified NZ boards.**
- `GET https://api.lever.co/v0/postings/{company-slug}?mode=json` — public read-only, no API key; returns `text`, `descriptionPlain`, `hostedUrl`, `applyUrl`, `categories`, `workplaceType`, `createdAt` ([github.com/lever/postings-api](https://github.com/lever/postings-api), [sevic.dev notes](https://sevic.dev/notes/lever-public-jobs-api-nodejs)).
- **[live]** NZ-relevant boards verified returning data: `newzealandtradeandenterprise` (NZTE, 5 postings), `clearpoint` (4), `enable` (2), `kpmgnz` (34). Auckland roles confirmed via the boards' web UI ([jobs.lever.co/newzealandtradeandenterprise](https://jobs.lever.co/newzealandtradeandenterprise), [jobs.lever.co/clearpoint](https://jobs.lever.co/clearpoint)).
- The catch: you need a per-company slug list. Maintenance-free once configured, but discovery is manual (or via ATS-finder tools like [workway.dev/tools/ats-finder](https://www.workway.dev/tools/ats-finder)). No structured salary field.
- Fit: add a `"ats_boards": {"lever": [...], "greenhouse": [...]}` config section; one fetcher function each, same normalization as the current three.

**Adzuna — free key, real NZ endpoint, structured salary.**
- Self-serve `app_id`/`app_key`; country code in the path: `GET https://api.adzuna.com/v1/api/jobs/nz/search/1?what=...&where=Auckland&results_per_page=...` ([developer.adzuna.com/docs/search](https://developer.adzuna.com/docs/search)). Adzuna operates [adzuna.co.nz](https://www.adzuna.co.nz/api), and `nz` (New Zealand, NZD) is a documented supported country ([example list](https://github.com/folathecoder/adzuna-job-search-mcp)).
- **[live]** Endpoint exists and responds (401 without a key — auth is the only gate).
- Response fields: title, company, parsed location, category (incl. `hospitality_catering` — relevant to Ysera's hospitality track), description **excerpt**, `salary_min`/`salary_max`, `salary_is_predicted`, `created`, `redirect_url` ([jobspipe.dev/blog/adzuna-api](https://jobspipe.dev/blog/adzuna-api)).
- Free-tier limits (official ToS): **25 hits/min, 250 hits/day, 1,000/week, 2,500/month** ([developer.adzuna.com/docs/terms_of_service](https://developer.adzuna.com/docs/terms_of_service)) — a daily run of ~5 queries uses ~150/month. Fits.
- Bonus: `/jobs/nz/histogram` gives a free salary distribution per query — cheap NZ salary benchmarking without scraping ([jobspipe.dev/blog/adzuna-api](https://jobspipe.dev/blog/adzuna-api)).

### Weak fits

**Jooble NZ — works, but 500 lifetime requests.**
- Per-country keys; `nz.jooble.org/api/about` is the NZ registration page ([jooble help](https://help.jooble.org/en/support/solutions/articles/60001448238-rest-api-documentation)). Response includes a formatted `salary` string, `location`, `snippet`, `type`, `link`.
- **Deal-breaker-ish:** "The free REST API plan includes a total lifetime limit of **500 requests per key**" (same doc, modified 2026-08-16). At one query per search phrase, a few weeks of daily runs exhausts it. Skip unless it's a one-off survey.

**SEEK (the main NZ board) — API locked.**
- "Access to the SEEK API requires approval from SEEK… apply… by filling out our Integration Request form"; the API targets recruitment-software partners, seven-stage integration process ([developer.seek.com](https://developer.seek.com), [talent.seek.com.au/partners/how-to-integrate](https://talent.seek.com.au/partners/how-to-integrate)). Not available to a solo project.
- Practical path: add `seek.co.nz` to `config.json` `job_boards` and let the existing Firecrawl search+scrape handle it, with a `scrape_caps` entry (LinkedIn-style). No new code needed — just config.

**Trade Me Jobs — NZ-native, structured salary filters, but OAuth.**
- `GET https://api.trademe.co.nz/v1/Search/Jobs.{json}` supports `region`, `district`, `salary_min`, `salary_max`, `hourly_min/max` in NZD — exactly the structured salary signal no other NZ source gives ([developer.trademe.co.nz/api-reference/search-methods/jobs-search](http://developer.trademe.co.nz/api-reference/search-methods/jobs-search)).
- Requires registering an app and OAuth authentication ("Requires Authentication? Yes", same page). Doable with stdlib (`urllib` + `oauth1` signing by hand is annoying; realistically this wants a tiny dependency or an hour of HMAC fiddling). Defer until the cheap wins land.

**Remotive / RemoteOK — already integrated; note their terms.**
- Remotive: no key; rate limit "excessive requests (more than 2x per minute) will be blocked", advise max ~4 pulls/day; jobs delayed 24h; must link back to Remotive ([github.com/remotive-com/remote-jobs-api](https://github.com/remotive-com/remote-jobs-api)). Daily scheduler cadence is fine.
- RemoteOK: free JSON at `https://remoteok.com/api`, plus RSS; needs a real User-Agent (already handled) ([remoteok.featurebase.app](https://remoteok.featurebase.app/help/articles/3140840-is-there-an-api-or-rssjson-feed-of-remote-jobs)).

---

## 2. Salary enrichment

### Where salary data comes from on these boards

| Source | Salary field | Structured? | Notes |
|---|---|---|---|
| RemoteOK | `salary_min`, `salary_max` (USD) | Yes | Only when the employer publishes it ("#OpenSalaries"); **[live]** sample: `60000`/`80000`. Current `fetch_remoteok()` drops these fields |
| Remotive | `salary` | Free-text string | **[live]** sample: `"$20k -$35k"`. Current `fetch_remotive()` drops it |
| Adzuna | `salary_min`, `salary_max` | Yes | Plus `salary_is_predicted` — when the posting states no range, Adzuna **models** one (Jobsworth); treat predicted values as estimates, not employer numbers ([jobspipe.dev/blog/adzuna-api](https://jobspipe.dev/blog/adzuna-api)) |
| Jooble | `salary` | Formatted string | `{min} - {max} {currency}` ([Jooble docs](https://help.jooble.org/en/support/solutions/articles/60001448238-rest-api-documentation)) |
| Arbeitnow | none | — | **[live]** response keys: company_name, created_at, description, job_types, location, remote, slug, tags, title, url — no salary |
| Greenhouse / Lever | none | — | No structured comp field in the public board APIs; salary only if written into the description HTML |
| Firecrawl-scraped boards (Seek, TradeMe, Indeed) | varies | — | NZ listings frequently omit salary entirely — NZ culture on Seek/TradeMe skews "on application" |

**Immediate win:** the repo fetches Remotive/RemoteOK already and throws away their salary fields (verified against current `sources_api.py`). Add `salary` passthrough to the normalized job dict (~6 lines + one dashboard field). RemoteOK's is USD — flag currency or convert roughly; Remotive's string needs parsing anyway.

### LLM extraction from descriptions — viable fallback, verified

- Independent benchmark (small LLMs extracting salary/geo from real job descriptions, re-run April 2026): Claude Haiku 4.5 = **100% salary accuracy**, GPT-5 Mini = **100%**, Gemini 2.5 Flash = 80%, at $0.003–$0.019 per posting — and a full month of job-board automation cost **~$0.35, later ~$0.01**. Small-model structured extraction is genuinely production-grade for this task ([blog.alleyne.dev/i-benchmarked-6-llms-to-automate-my-job-board-for-035month](https://blog.alleyne.dev/i-benchmarked-6-llms-to-automate-my-job-board-for-035month)).
- Fits the repo with **zero new machinery**: extend the `analyze.md` prompt/schema so each scoring batch also returns `salary_min`/`salary_max`/`currency`/`confidence` or nulls; `run_claude_json` already parses JSON and the pipeline already touches every description. No extra LLM pass needed — piggyback on Step 3.
- Caveats to design for: (a) most NZ postings won't state salary, so null must be a first-class outcome; (b) mark LLM-extracted values distinctly from API-provided ones (e.g. `salary_source: "llm" | "api" | null`) — don't show estimates as employer-stated (same principle as Adzuna's `salary_is_predicted`); (c) salary-in-description extraction is exactly what the benchmark measured, so trust but spot-check.

---

## 3. Firecrawl scrape robustness (rate limits, blocked domains, caps)

Current plan relevant limits (official):

- Free plan: **1,000 credits/month**, 2 concurrent browsers, **10 req/min** on `/scrape` and `/search` ([docs.firecrawl.dev/rate-limits](https://docs.firecrawl.dev/rate-limits), [firecrawl.dev/pricing](https://www.firecrawl.dev/pricing)).
- Credits: Scrape = 1/page, **JSON-mode scrape = 5 credits/page** (1 base + 4 JSON), Search = 2 credits per 10 results ([choosing-the-data-extractor](https://docs.firecrawl.dev/developer-guides/usage-guides/choosing-the-data-extractor), [pricing](https://www.firecrawl.dev/pricing)).
- **[live-cost math]** This repo's typical run: ~8 queries × 2 credits + 20 pages × 5 credits ≈ **116 credits/run** → ~8 full runs/month on the free plan before credits die. That is the real budget ceiling, more binding than the per-minute rate limit.

Concrete patterns worth adopting:

1. **Respect `Retry-After` on 429.** Firecrawl returns 429 "Rate limit exceeded" with a `Retry-After` header; docs prescribe back-off-and-retry for 429/408, and 402 "Insufficient credits" is non-retryable — treat it as a run-aborting condition, not a scrape failure ([docs.firecrawl.dev/api-reference/errors](https://docs.firecrawl.dev/api-reference/errors)). Today `extract_postings()` catches everything and silently falls back to the snippet, so a credit exhaustion would degrade the whole run invisibly. Distinguish 402 → abort early with a log/notification; 429 → sleep `Retry-After` and retry once.
2. **Per-host caps (already exist) + failure-driven demotion.** `_apply_scrape_caps` and `UNSCRAPABLE_HOSTS` are the right shape; extend the hardcoded set with a persisted "hosts that failed N scrapes in a row" map written back to `config`/`output`, so a newly-broken board stops burning 5 credits/page every run without a code edit.
3. **Prefer API sources for volume.** Every Greenhouse/Lever/Adzuna posting is a POST/retrieve the pipeline doesn't spend Firecrawl credits on — another reason rec 4/5 rank high. Remotive explicitly wants ≤2 pulls/min ([Remotive ToS](https://github.com/remotive-com/remote-jobs-api)).
4. **Timeouts:** 408 is retryable with backoff; the existing `SCRAPE_TIMEOUT_MS = 120000` for JS-heavy pages is aligned with docs guidance ([errors](https://docs.firecrawl.dev/api-reference/errors)).
5. `/extract` is deprecated in favor of `/scrape` JSON mode and `/agent` — the repo is already on the supported path ([choosing-the-data-extractor](https://docs.firecrawl.dev/developer-guides/usage-guides/choosing-the-data-extractor)). No action.

---

## 4. Run history / dedupe beyond `seen.json`

Current failure mode (verified from repo docs + `pipeline.py` design): `seen.json` is written **only at successful run end**; a crash mid-Step-3 (e.g. the wrapper's 1200s timeout) loses the entire run's dedupe state, so the next run re-scores everything. Also nothing records *when* a job was seen, so "jobs from the last 7 days" or "new this run" views are impossible.

Options, cheapest first:

1. **Incremental seen.json flush (band-aid, ~5 lines):** after each scoring batch, merge + write `seen.json`. Fixes the crash-loses-everything bug; adds no capability. If you do nothing else, do this.
2. **SQLite (recommended).** One file `output/jobs.db`, stdlib `sqlite3`, zero deps:
   - `runs(id INTEGER PRIMARY KEY, started_at, finished_at, status)` — run history for the dashboard.
   - `jobs(dedup_key TEXT UNIQUE, title, company, url, source, first_seen_run, last_seen_run, score, verdict, status)` — `INSERT OR IGNORE` gives cross-run dedupe **as a database constraint** instead of a JSON set written at end; the write happens per-batch, so a crash loses nothing.
   - WAL mode (`PRAGMA journal_mode=WAL`) keeps the dashboard's reads and the writer from blocking ([SQLite WAL docs](https://www2.sqlite.org/matrix/wal.html)).
   - Run-history views ("new this run", "seen 3+ times without applying") become SQL, not JSON surgery.
   - This is the standard upgrade for scraped-data projects: single file, concurrent readers, built into Python ([dev.to: Why I store scraped data in SQLite](https://dev.to/0012303/why-i-store-all-my-scraped-data-in-sqlite-not-json-not-csv-56pa)); `seen.json` can stay as a generated export for backward compat.
3. **Content-hash dedupe (additive, later).** Key on `title + normalized company` as well as URL, because aggregators (Adzuna explicitly: "the same role can appear more than once through different boards" — [jobspipe](https://jobspipe.dev/blog/adzuna-api)) duplicate cross-source. Cheap as a secondary index once SQLite exists.

---

## 5. Notifications for a local Windows machine (cheap/free)

All three are stdlib-only (no new deps) — they differ in setup friction and notification quality.

**ntfy.sh — recommended default.**
- No signup, no account: `POST https://ntfy.sh/<your-topic>` with the message body; subscribe on any device ([docs.ntfy.sh/publish](https://docs.ntfy.sh/publish)). One `urllib.request` call from the pipeline (~15 lines including a summary builder).
- Python one-liner shape: `urllib.request.urlopen(urllib.request.Request("https://ntfy.sh/jobs-scraper-Xk29a", data=b"3 new jobs, top score 88", headers={"Title": "JobScraper: run done"}))` ([same docs](https://docs.ntfy.sh/publish)).
- Free-server limits: default burst 60 requests/visitor with 1 token replenished per 5s (`visitor-request-limit-burst`/`visitor-request-limit-replenish` — [docs.ntfy.sh/config](https://docs.ntfy.sh/config/)); one message per daily run is nowhere near them. Hosted free usage is documented as fine "within limits (enough for 90+% of users)" ([ntfy FAQ/GitHub](https://github.com/binwiederhier/ntfy)); third-party integration guides cite ~250 msgs/day for the hosted free tier ([ohdear.app docs](https://ohdear.app/docs/notifications/ntfy)).
- **Topic is a password** — no auth on publish means pick an unguessable topic name ([docs.ntfy.sh/publish](https://docs.ntfy.sh/publish)). Use a random suffix.
- Windows reception: web app at ntfy.sh/app, or a Store client — e.g. **Notidesk** (native ntfy client, Windows toast notifications) ([apps.microsoft.com/detail/9pjdd0jkr719](https://apps.microsoft.com/detail/9pjdd0jkr719)); ntfy also has Android/iOS apps for phone push ([ntfy.sh](https://ntfy.sh)).

**Telegram bot — best notification UX, slightly more setup.**
- One-time: create bot via @BotFather → token; send the bot a message; `GET getUpdates` returns your `chat_id` ([teleclaw.bot tutorial](https://teleclaw.bot/blog/telegram-bot-api-tutorial), [willschenk.com curl walkthrough](https://willschenk.com/labnotes/2024/telegram_with_curl/)).
- Send: `POST https://api.telegram.org/bot<TOKEN>/sendMessage` with `chat_id` + `text` ([openpublicapis reference](https://openpublicapis.com/api/telegram-bot)). Free, no meaningful limit at 1–2 messages/day. ~20 stdlib lines. Bonus: supports Markdown and link buttons, so "top 3 new jobs" as tappable links.

**SMTP email — zero new infra, weakest push.**
- Stdlib `smtplib` + `email.message.EmailMessage` over `SMTP_SSL` with an app password (Gmail or existing mailbox) ([realpython.com/python-send-email](https://realpython.com/python-send-email)). Good as a **daily digest**; bad as an alert (easy to ignore, app-password setup friction, credential in `.env`).
- Verdict: ntfy for "run finished / N new high-scores" pings; optionally SMTP later for a weekly digest. Both can share one `notify.py` with two functions.

---

## Ranked recommendations (value ÷ effort, solo junior dev)

1. **Capture existing salary fields** (Remotive `salary`, RemoteOK `salary_min/max`) — pure win, ~30 min, immediate salary data on every remote posting. *(effort S, value High)*
2. **ntfy.sh run notification** — 1 h, no account, completes the scheduler story ("run happened, here's what's new"). *(S, High)*
3. **LLM salary extraction piggybacked on the scoring pass** — extend `analyze.md` schema with nullable salary fields; benchmarked ~100% accuracy on small models at ~$0.001/posting. Tag `salary_source` so estimates are never shown as stated. *(M, High)*
4. **Greenhouse + Lever watchlist** — config-driven board tokens/slugs (verified NZ boards: `rocketlab`, `newzealandtradeandenterprise`, `clearpoint`, `kpmgnz`, `enable`); one fetcher each, no auth, no Firecrawl credits. *(M, High)*
5. **Adzuna NZ** — free key, 2,500 hits/mo covers daily runs; structured salary + `salary_is_predicted` + hospitality category; also unlocks the free `/histogram` salary benchmark. *(M, Medium)*
6. **Firecrawl 429/Retry-After + 402-abort + failure-demotion for UNSCRAPABLE_HOSTS** — small patches that stop silent credit burn and invisible degradation. *(S–M, Medium)*
7. **SQLite run history + per-batch dedupe** — the biggest structural item: crash-safe dedupe, run-history views for the dashboard, no deps. Do after 1–6; do the incremental `seen.json` flush today if 7 is deferred. *(L, Medium-High)*
8. **`seek.co.nz` via existing Firecrawl path** — config-only addition with a scrape cap; SEEK's real API is partner-gated. *(S, Medium)*
9. **Jooble NZ** — skip while the free key is 500 requests *lifetime*. *(S, Low)*
10. **Trade Me Jobs API** — best NZ structured salary source, but OAuth setup makes it a 1–2 day item; revisit once 1–7 landed. *(L, Medium)*

## Source index

- Remotive API (endpoint, rate limits, ToS): https://github.com/remotive-com/remote-jobs-api
- RemoteOK feeds: https://remoteok.featurebase.app/help/articles/3140840-is-there-an-api-or-rssjson-feed-of-remote-jobs
- Arbeitnow API: https://publicapis.dev/resource/arbeitnow/i44hom4t (field list verified live)
- Adzuna docs / ToS / field guide: https://developer.adzuna.com/docs/search · https://developer.adzuna.com/docs/terms_of_service · https://jobspipe.dev/blog/adzuna-api
- Jooble REST docs (regional keys, 500-lifetime limit): https://help.jooble.org/en/support/solutions/articles/60001448238-rest-api-documentation · https://nz.jooble.org/api/about
- Greenhouse Job Board API: https://docs.greenhouse.io/job-board.html
- Lever Postings API: https://github.com/lever/postings-api
- SEEK API gating: https://developer.seek.com · https://talent.seek.com.au/partners/how-to-integrate
- Trade Me Jobs search API: http://developer.trademe.co.nz/api-reference/search-methods/jobs-search
- Firecrawl rate limits / pricing / errors / extractor guide: https://docs.firecrawl.dev/rate-limits · https://www.firecrawl.dev/pricing · https://docs.firecrawl.dev/api-reference/errors · https://docs.firecrawl.dev/developer-guides/usage-guides/choosing-the-data-extractor
- LLM salary-extraction benchmark: https://blog.alleyne.dev/i-benchmarked-6-llms-to-automate-my-job-board-for-035month
- ntfy publish / config / clients: https://docs.ntfy.sh/publish/ · https://docs.ntfy.sh/config/ · https://apps.microsoft.com/detail/9pjdd0jkr719 · https://ohdear.app/docs/notifications/ntfy
- Telegram Bot API: https://teleclaw.bot/blog/telegram-bot-api-tutorial · https://willschenk.com/labnotes/2024/telegram_with_curl/ · https://openpublicapis.com/api/telegram-bot
- Python SMTP: https://realpython.com/python-send-email/
- SQLite WAL / scraped-data-in-SQLite pattern: https://www2.sqlite.org/matrix/wal.html · https://dev.to/0012303/why-i-store-all-my-scraped-data-in-sqlite-not-json-not-csv-56pa
- ATS-finder (board-token discovery): https://www.workway.dev/tools/ats-finder
