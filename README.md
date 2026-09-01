# AI Job Hunt Agent

An autonomous job-hunting pipeline. It reads your resume, searches the web for matching remote roles, and scores each posting against your actual profile with an LLM — all reviewable in a local web dashboard.

It works for **any profession** — developer, designer, virtual assistant, writer, accountant, marketer. Everything (target roles, search queries, scoring) is derived from your `resume.md`; nothing about your field is hardcoded.

## How it works

```
resume.md
   │
   ▼
1. Build search config   LLM extracts target roles, key skills, and
   │                     search queries from your resume
   ▼
2. Discover & scrape     Firecrawl runs the queries, then scrapes each
   │                     result page and extracts individual postings
   ▼
3. Analyze & score       LLM scores every posting 0–100 against your
   │                     profile (stack match, seniority, remote signals,
   │                     freshness, red flags) and gives a verdict
   ▼
output/jobs.json
```

A FastAPI server (`server.py`) exposes the pipeline and results, and `ui/index.html` is a single-file dashboard with live progress (Server-Sent Events), score/verdict filtering, and applied/skipped tracking.

## Stack

- **Python + FastAPI** — pipeline orchestration and API
- **[Firecrawl](https://firecrawl.dev)** — web search and structured scraping (LLM extraction with a JSON schema)
- **Any OpenAI-compatible LLM gateway** (e.g. a local 9Router) — resume analysis and job scoring via prompt files in `prompts/`, called over HTTP with stdlib `urllib`
- **Vanilla JS + Tailwind** — zero-build single-file UI

## Setup

Requirements: Python 3.10+, a Firecrawl API key, and an OpenAI-compatible LLM endpoint + API key (e.g. a local 9Router gateway).

```bash
pip install -r requirements.txt
cp .env.example .env        # add your FIRECRAWL_API_KEY and LLM_* settings
```

Then add your own `resume.md` in the project root (markdown resume — it is gitignored and never leaves your machine).

Optionally edit `config.json` to change where the agent searches: `job_boards` is the list of sites to query (one search each), and `reddit_groups` are groups of subreddits (one grouped search each, with optional `extra_terms` added to the query). The defaults cover LinkedIn, Indeed, Wellfound, Glassdoor, JobStreet, OnlineJobs.ph, and a set of profession-neutral hiring subreddits — if your field has dedicated boards or subreddits (e.g. Dribbble for designers, r/VirtualAssistant for VAs), add them here.

## Run

```bash
# Web dashboard
python server.py            # → http://127.0.0.1:8000

# Or headless
python agent.py
```

Results land in `output/` (gitignored): `jobs.json` (scored jobs) and `raw_jobs.json` (everything scraped).

## Tests

```bash
pytest
```

## Project structure

```
agent.py          # CLI shim (the pipeline lives in jobscraper/)
jobscraper/       # config, llm (HTTP), scrape, pipeline
config.json       # search sources: job boards + Reddit subreddit groups
server.py         # FastAPI: /api/jobs, /api/status, /api/run (SSE)
ui/index.html     # single-file dashboard
prompts/          # LLM prompt files for each AI step
CLAUDE.md         # agent context (target roles, preferences, output contract)
tests/            # pytest suite (LLM/Firecrawl mocked)
```

## License

MIT — see [LICENSE](LICENSE).
