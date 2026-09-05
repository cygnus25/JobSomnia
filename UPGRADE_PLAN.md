# UPGRADE_PLAN.md — salary + location + SJS handoff

**Written:** 2026-09-06 (NZ). **Purpose:** handoff plan for the next AI harness
session. Read this + `RESEARCH.md` + `AGENTS.md` before touching code.

---

## 1. State when you pick up — DO NOT REDO

An earlier session already implemented the first slice of this plan. It is on
disk, **uncommitted**, unit tests 64/64 green:

| Change | Files |
|---|---|
| **Student Job Search (sjs.co.nz) API source** — new module. SJS is a Next.js SPA over a private AWS API Gateway (`sjs-be-jobs-stack`, `https://8e0xo7eina.execute-api.ap-southeast-1.amazonaws.com/prod`) requiring SigV4 + Cognito. Its identity pool allows guest access (`allowGuestAccess: true`, read from the site's public JS bundles), so `GetId` + `GetCredentialsForIdentity` + stdlib SigV4 gives a working anonymous call. `GET /jobs` returns **1,214 live postings, verified live** with `payMin`/`payMax`/`payType` (~100% filled), `areaName`/`regionName` (Auckland-heavy), `advertStartDateNz`, `description`. Job URL: `https://www.sjs.co.nz/job-seeker/jobs/{uuid}`. | `jobscraper/sjs.py` (new), `jobscraper/sources_api.py` (`fetch_sjs` + fetchers map), `jobscraper/config.py` + `config.json` (`"sjs.co.nz"` in `api_sources`), `tests/test_sjs.py` (new) |
| **Salary passthrough** — RemoteOK `salary_min/max` → `"$60,000-$80,000"`, Remotive `salary` string, SJS pay fields → `"$23.95-$28.00/hr"`. Raw jobs now carry a `salary` string ("" when unknown). | `jobscraper/sources_api.py` (`_format_salary_range`), `jobscraper/sjs.py` (`_format_pay`) |
| **LLM scoring returns location + salary** — `analyze.md` schema gained `location` + `salary`; instructed to copy from input or extract from description text, never invent. | `prompts/analyze.md` |
| **UI** — salary chip (banknote icon, ember accent) next to verdict pill; sort options `Salary: High → Low` and `Location: A → Z` (`salaryValue()` compares range ceiling). Location already rendered on cards since before this session. | `ui/index.html` |

**Known-good:** `pytest tests/ -q` → 64 passed (this session). Firecrawl path,
LLM scoring, dashboard backend untouched except config/`analyze.md`.

**Known-unknown (verify, don't assume):** the full pipeline has NOT been run
end-to-end since these changes; dashboard not visually checked; salary chip
never rendered against real scored data.

---

## 2. Task A — verify the existing slice (gate for everything else)

1. `python -m pytest tests/ -q` — all green.
2. `python scripts/jobscraper-run.py` from repo root, **background + notify**
   (takes up to ~20 min; wrapper enforces 1200 s timeout — see AGENTS.md /
   skill notes). Do NOT pass CLI args; the script takes none.
3. After the run: `output/raw_jobs.json` should contain `sjs.co.nz` jobs with
   non-empty `salary`; `output/jobs.json` scored jobs should carry
   `location` + `salary` fields.
4. `python server.py` (background) → curl `GET /` (200) and `GET /api/jobs`
   (JSON array). Confirm cards carry salary. Leave the URL with Ysera.
5. If green → **commit** the slice (see §6). If not → fix before Task B.

Done-criterion: one committed change-set containing SJS source + salary
passthrough + UI chip, with a real run's `output/jobs.json` showing salary
values and no test regressions.

---

## 3. Remaining tasks (from RESEARCH.md, ranked value/effort)

Do in order; each is independent after Task A. One commit per task.

### Task B — ntfy.sh run notification (~1 h, no account)
- `jobscraper/notify.py`: stdlib `urllib` POST to `https://ntfy.sh/<topic>`
  with title + summary ("N new jobs, top: Title @ Company (score)").
- Topic from `.env` (`NTFY_TOPIC=`), unguessable random suffix — **topic is
  the password** (docs.ntfy.sh/publish). Pipeline calls it in a
  best-effort try/except after `run_pipeline()` returns; failure never fails
  the run. Source: RESEARCH.md §5.
- Done-criterion: real POST observed (curl or run), notification received,
  unit test with patched urlopen.

### Task C — Greenhouse + Lever watchlist (~half day, no auth, no Firecrawl credits)
- Config: `"ats_boards": {"greenhouse": ["rocketlab", ...], "lever": ["newzealandtradeandenterprise", "clearpoint", "kpmgnz", "enable"]}`.
- One fetcher each in `sources_api.py` style (best-effort → [] on failure):
  - `GET https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true`
  - `GET https://api.lever.co/v0/postings/{slug}?mode=json`
- Normalize to the common shape (title/company/location/url/description/
  posted_date/source). No structured salary on these — leave `salary` "" and
  let the LLM extraction in `analyze.md` pick pay out of descriptions.
- Sources: RESEARCH.md §1 (docs links + live-verified NZ boards there).
- Done-criterion: fetchers return real NZ postings (live check), unit tests,
  jobs flow through dedupe + scoring like other API sources.

### Task D — Adzuna NZ (~half day, needs a free API key)
- Register at developer.adzuna.com → `ADZUNA_APP_ID` / `ADZUNA_APP_KEY` in `.env`.
- `GET https://api.adzuna.com/v1/api/jobs/nz/search/1?what=...&where=Auckland`
- Map `salary_min`/`salary_max` (respect `salary_is_predicted` — append
  " (est.)" so estimates never masquerade as employer-stated), `created`,
  `redirect_url`, `category` (has `hospitality_catering` — relevant).
- Rate limits: 25/min, 250/day, 2,500/mo free — daily run fits easily.
- Done-criterion: as Task C, plus salary values appearing on Adzuna cards.

### Task E — Firecrawl credit/robustness guards (~2 h)
- In `jobscraper/scrape.py`: on 429 read `Retry-After`, sleep, retry once;
  on 402 (credits exhausted) **abort the run early with a clear error**
  instead of silently degrading to snippet fallback.
- Persist "host failed N scrapes in a row" to output, auto-skip next runs
  (extend the UNSCRAPABLE_HOSTS idea).
- Context: free plan = 1,000 credits/mo, JSON scrape = 5 credits/page, a
  typical run ≈ 116 credits. Sources: RESEARCH.md §3.
- Done-criterion: simulated 429/402 (patched client) behaves as specified;
  tests cover both branches.

### Task F — SQLite run history + crash-safe dedupe (1–2 d, biggest item)
- `output/jobs.db`, stdlib `sqlite3`, WAL mode:
  - `runs(id, started_at, finished_at, status)`
  - `jobs(dedup_key UNIQUE, title, company, url, source, first_seen_run,
    last_seen_run, score, verdict, status, salary, location)`
- Replace end-of-run `seen.json` write with per-batch upserts → crash
  mid-run no longer loses dedupe state (current failure mode: wrapper
  timeout wipes the whole run's scoring).
- Keep `seen.json`/`jobs.json` as generated exports so the dashboard and
  existing tests keep working.
- Band-aid first if F is deferred: incremental `seen.json` flush after each
  scoring batch (~5 lines in `pipeline.py`).
- Done-criterion: kill a run mid-scoring, next run doesn't re-score;
  dashboard shows run history (new "runs" row or endpoint).

### Task G — `seek.co.nz` scrape board (~10 min, config-only)
- Add to `config.json` `job_boards` + `scrape_caps` (cap ~4). No code —
  existing Firecrawl path handles it. SEEK's real API is partner-gated;
  do not attempt it. Done-criterion: SEEK postings appear in a run.

### Skip (deliberate)
- **Jooble NZ** — free key = 500 requests *lifetime*. Burns out in weeks.
- **Trade Me Jobs API** — best NZ structured salary but OAuth app
  registration; revisit only after B–F land.
- **RemoteOK USD conversion** — label as USD in the chip later if it bugs
  Ysera; not worth a rates dependency now.

---

## 4. Operating rules (from AGENTS.md + session conventions)

- Prefix shell commands with `rtk` for compressed output (never wrap
  commands whose raw output you must parse exactly).
- Stdlib-first. No new dependencies without a written justification.
  Every fetcher is best-effort: catch broadly, log, return [].
- Tests alongside every change; `pytest tests/ -q` green before any commit.
- One concern per commit; messages imperative ("Add SJS API source"), no
  "WIP". Never commit `output/`, `.env`, or `.codegraph/`.
- A `.codegraph/` index exists; prefer symbol lookups over re-reading files.

## 5. Secrets / env

- `.env` already has `FIRECRAWL_API_KEY` + `LLM_*` (9Router gateway).
- Task D adds `ADZUNA_APP_ID`/`ADZUNA_APP_KEY`; Task B adds `NTFY_TOPIC`.
- `.env.example` must gain matching placeholders in the same commit.

## 6. First prompt for the next harness (paste-ready)

> Read `C:/Users/ysera/Documents/apply/JobScraper/ai-job-scraper/UPGRADE_PLAN.md`,
> `RESEARCH.md`, and `AGENTS.md` in that repo. The salary+location+SJS slice
> in §1 is already implemented and uncommitted — do NOT redo it. Execute
> §2 (Task A) end-to-end: run the pipeline for real, verify SJS jobs and
> salary fields flow through to `output/jobs.json` and the dashboard, fix
> anything broken, then commit that slice. After that, implement Tasks B–G
> in order, one commit each, tests green before every commit. Follow §4
> operating rules and §5 env handling. Finish with: test output, the real
> run's `output/jobs.json` proving salary/location on scored jobs, a list
> of commits made, and anything you had to deviate on.
