# JobSomnia

An autonomous job-hunting pipeline. It reads your resume, searches the web for matching remote roles, and scores each posting against your actual profile with an LLM. Results show up in a local web dashboard.

It works for any profession: developer, designer, virtual assistant, writer, accountant, marketer. Everything (target roles, search queries, scoring) is derived from your `resume.md`. Nothing about your field is hardcoded.

## How it works

```
resume.md
   |
   v
1. Build search config   LLM extracts target roles, key skills, and
   |                     search queries from your resume
   v
2. Discover & scrape     Firecrawl runs the queries, then scrapes each
   |                     result page and extracts individual postings.
   |                     Free job-board APIs are queried in the same pass:
   |                     Remotive, RemoteOK, Arbeitnow, Student Job Search
   |                     (NZ, signed guest API), Greenhouse + Lever
   |                     watchlists, and Adzuna NZ (optional key).
   v
3. Analyze & score       LLM scores every posting 0-100 against your
   |                     profile (stack match, seniority, remote signals,
   |                     freshness, red flags) and returns a verdict, the
   |                     location, and any salary found in the posting.
   |                     Jobs run through in batches of 10; each batch is
   |                     persisted to output/jobs.db the moment it lands,
   |                     so a killed run resumes instead of re-billing.
   |                     Transient gateway errors (429/502/503/504) retry
   |                     with backoff, and inputs the model omits from a
   |                     batch still count as attempted, so a resumed run
   |                     never re-bills them.
   v
output/jobs.json (generated export of the DB)
```

A FastAPI server (`server.py`) exposes the pipeline and results, and `ui/index.html` is a single-file dashboard with live progress (Server-Sent Events), score/verdict filtering, and applied/skipped tracking.

The dashboard is cumulative: jobs persist across runs with their scores, new matches get added, and anything you mark applied stays marked. Every run also snapshots itself to `output/runs/<timestamp>/` so you can diff what changed.

## Stack

- Python + FastAPI for the pipeline and API
- [Firecrawl](https://firecrawl.dev) for web search and structured scraping (LLM extraction with a JSON schema)
- Any OpenAI-compatible LLM gateway (e.g. a local 9Router) for resume analysis and job scoring via prompt files in `prompts/`, called over HTTP with stdlib `urllib`
- Vanilla JS + Tailwind, zero-build single-file UI

## Setup

Requirements: Python 3.10+, a Firecrawl API key, and an OpenAI-compatible LLM endpoint + API key (e.g. a local 9Router gateway).

```bash
pip install -r requirements.txt
cp .env.example .env        # add your FIRECRAWL_API_KEY and LLM_* settings
```

Then add your own `resume.md` in the project root (markdown resume — it is gitignored and never leaves your machine).

Optionally edit `config.json` to change where the agent searches: `job_boards` is the list of sites to query (one search each), and `reddit_groups` are groups of subreddits (one grouped search each, with optional `extra_terms` added to the query). The defaults cover LinkedIn, Indeed, Wellfound, Glassdoor, JobStreet, OnlineJobs.ph, SEEK NZ, and a set of profession-neutral hiring subreddits. If your field has dedicated boards or subreddits (e.g. Dribbble for designers, r/VirtualAssistant for VAs), add them here.

`scrape_caps` sets per-site page limits (a site set to 0 is skipped entirely — LinkedIn and Reddit reject scrapers, so they default to 0). Hosts that repeatedly fail to scrape get demoted to snippet-only automatically (`output/failed_hosts.json`). `api_sources` lists free public job-board APIs queried alongside Firecrawl on every run: `remotive.com`, `remoteok.com`, `arbeitnow.com`, `sjs.co.nz` (Student Job Search, NZ), `greenhouse`, and `lever` are keyless; `adzuna` needs a free key in `.env`. Set it to `[]` to disable API sourcing, or list only the ones you want.

`ats_boards` configures the Greenhouse/Lever watchlists: Greenhouse board tokens (`rocketlab`) and Lever company slugs (`clearpoint`) to poll — find them on a company's jobs page URL or via an ATS-finder tool. The `adzuna` section shapes the Adzuna NZ query (`what`, `where`, `max_days_old`).

## Run

```bash
# Web dashboard
python server.py            # -> http://127.0.0.1:8000

# Or headless
python agent.py
```

Results land in `output/` (gitignored). `jobs.db` (SQLite, WAL) is the source of truth: cross-run dedupe, per-batch scoring progress, and run history. `jobs.json` (scored jobs, cumulative) and `seen.json` are regenerated exports written at the end of a successful run, alongside `raw_jobs.json` (everything scraped this run) and per-run snapshots under `output/runs/<timestamp>/`.

## Scheduled runs

For a hands-off daily run, `jobscraper/schedule.py` runs the full pipeline and prints a plain-text summary to stdout (run time, raw jobs scraped, new jobs, jobs scoring above the threshold, and up to 5 top-scoring jobs). It exits with code 1 on failure so the scheduler can flag the run. If `NTFY_TOPIC` is set in `.env`, it also pushes the summary (or a failure alert) to that [ntfy.sh](https://ntfy.sh/app) topic — no account needed; the unguessable topic name is the credential, so subscribe on your phone/desktop and keep the suffix random.

```bash
python -m jobscraper.schedule
```

Windows (Task Scheduler), daily 8am:

```powershell
schtasks /create /tn "AI Job Scraper" /tr "cmd /c cd /d C:\path\to\ai-job-scraper && python -m jobscraper.schedule >> output\schedule.log 2>&1" /sc daily /st 08:00
```

Linux/macOS (cron), daily 8am:

```cron
0 8 * * * cd /path/to/ai-job-scraper && /usr/bin/python3 -m jobscraper.schedule >> output/schedule.log 2>&1
```

Hermes Agent (cronjob) — runs daily at 8am and the agent delivers the summary to your chat (Telegram/Discord/desktop, wherever the profile is connected):

```bash
hermes cron add \
  --name "JobScraper daily digest" \
  --schedule "every day at 8am" \
  --script scripts/jobscraper-run.py \
  --workdir /path/to/ai-job-scraper
```

The script captures `python -m jobscraper.schedule` output; the agent turns it into a readable digest (top jobs with links, new-vs-seen counts). Use `hermes cron list` to verify and `hermes cron run <id>` to test-fire once.

## Tests

```bash
pytest
```

115 tests. The LLM, Firecrawl, and the job-board APIs are all mocked, so the suite passes offline and never flakes.

## Project structure

```
agent.py          # CLI shim (the pipeline lives in jobscraper/)
jobscraper/       # config, llm (HTTP), scrape, sources_api, sjs, store,
                  # notify, pipeline, schedule
config.json       # search sources: job boards, Reddit groups, scrape caps,
                  # api_sources, ats_boards, adzuna
server.py         # FastAPI: /api/jobs, /api/runs, /api/status, /api/run (SSE)
ui/index.html     # single-file dashboard
prompts/          # LLM prompt files for each AI step
CLAUDE.md         # agent context (target roles, preferences, output contract)
tests/            # pytest suite (LLM/Firecrawl/APIs mocked)
```

## License

MIT — see [LICENSE](LICENSE).
