# jobscraper/sources_api.py
"""Free/public job-board APIs, queried alongside Firecrawl search+scrape
(stdlib urllib only, matching llm.py's HTTP style).

Each fetch_*() hits one board's public API and normalizes its postings into
the same shape scrape_jobs() produces (title, company, location, url,
description, posted_date, source), so pipeline.py can merge the two lists
and run every job through the same seen.json dedupe and LLM scoring.

These free APIs are unreliable and outside our control, so every fetcher is
best-effort: it catches every exception and returns [] rather than ever
failing the run.
"""
import json
import logging
import os
import re
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from html import unescape
from itertools import zip_longest

from .config import load_config

log = logging.getLogger(__name__)

TIMEOUT_S = 30
# RemoteOK in particular rejects requests with no/generic User-Agent, so all
# three sources send an identifying one.
USER_AGENT = "ai-job-scraper/1.0"

# Keep each source's contribution bounded — these feeds can return hundreds
# of postings, and it's the newest ones that matter for a daily run.
MAX_JOBS_PER_SOURCE = 50

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _format_salary_range(lo, hi) -> str:
    """(60000, 80000) -> '$60,000-$80,000'; equal/one-sided/single values
    collapse to one figure; '' when neither is a positive number."""
    try:
        lo_v, hi_v = float(lo or 0), float(hi or 0)
    except (TypeError, ValueError):
        return ""
    if lo_v <= 0 and hi_v <= 0:
        return ""
    if lo_v > 0 and hi_v > 0 and lo_v != hi_v:
        return f"${lo_v:,.0f}-${hi_v:,.0f}"
    value = max(lo_v, hi_v)
    return f"${value:,.0f}"


def _strip_html(text: str) -> str:
    """Strip HTML tags from `text` (RemoteOK descriptions are HTML),
    collapsing the whitespace left behind."""
    return _WS_RE.sub(" ", _TAG_RE.sub(" ", text or "")).strip()


def _html_to_text(markup: str) -> str:
    """HTML -> plain text. Greenhouse's `content` field ships HTML-escaped
    (&lt;p&gt;), so unescape before stripping tags and again for entities
    inside the text that survive stripping."""
    return unescape(_strip_html(unescape(markup or "")))


def _interleave_boards(board_jobs: list[list[dict]]) -> list[dict]:
    """Round-robin merge per-board job lists (each already newest-first)
    so fetch_api_jobs()'s per-source cap can't let one board crowd the
    other configured boards out of the run."""
    return [j for group in zip_longest(*board_jobs) for j in group if j]


def _get_json(url: str):
    """GET `url` and return the parsed JSON body. Raises on any failure —
    each fetch_*() catches broadly and degrades to []."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def fetch_remotive() -> list[dict]:
    """Fetch remote postings from Remotive's public API."""
    try:
        data = _get_json("https://remotive.com/api/remote-jobs")
        jobs = []
        for j in data.get("jobs", []):
            url = j.get("url", "")
            if not url:
                continue
            jobs.append({
                "title": j.get("title", ""),
                "company": j.get("company_name", ""),
                "location": j.get("candidate_required_location") or "Remote",
                "url": url,
                "description": j.get("description", ""),
                "posted_date": j.get("publication_date", ""),
                "source": "remotive.com",
                "salary": (j.get("salary") or "").strip(),
            })
        return jobs
    except Exception as e:
        log.info(f"  Remotive API failed: {e}")
        return []


def fetch_remoteok() -> list[dict]:
    """Fetch remote postings from RemoteOK's public API.

    The response is a JSON array whose first element is a legal-notice
    object, not a posting — it's skipped.
    """
    try:
        data = _get_json("https://remoteok.com/api")
        jobs = []
        for j in data[1:]:
            if not isinstance(j, dict):
                continue
            url = j.get("url", "")
            if not url:
                continue
            jobs.append({
                "title": j.get("position", ""),
                "company": j.get("company", ""),
                "location": j.get("location") or "Remote",
                "url": url,
                "description": _strip_html(j.get("description", "")),
                "posted_date": j.get("date", ""),
                "source": "remoteok.com",
                "salary": _format_salary_range(j.get("salary_min"), j.get("salary_max")),
            })
        return jobs
    except Exception as e:
        log.info(f"  RemoteOK API failed: {e}")
        return []


def fetch_arbeitnow() -> list[dict]:
    """Fetch remote postings from Arbeitnow's public job board API."""
    try:
        data = _get_json("https://www.arbeitnow.com/api/job-board-api")
        jobs = []
        for j in data.get("data", []):
            url = j.get("url", "")
            if not url:
                continue
            jobs.append({
                "title": j.get("title", ""),
                "company": j.get("company_name", ""),
                "location": j.get("location") or "Remote",
                "url": url,
                "description": j.get("description", ""),
                "posted_date": str(j.get("created_at", "")),
                "source": "arbeitnow.com",
            })
        return jobs
    except Exception as e:
        log.info(f"  Arbeitnow API failed: {e}")
        return []


def fetch_sjs() -> list[dict]:
    """Fetch NZ student postings from Student Job Search's (private) API —
    live in jobscraper/sjs.py; kept thin here so tests can patch it the
    same way as the other fetchers."""
    from .sjs import fetch_sjs_capped
    return fetch_sjs_capped()


def fetch_greenhouse() -> list[dict]:
    """Fetch postings from the Greenhouse boards listed in config's
    "ats_boards" ("greenhouse" -> board tokens). Public no-auth API;
    content=true adds the full description the LLM scorer reads. These
    boards carry no structured salary — `salary` stays "" so the analyze
    pass can extract pay from the description text."""
    tokens = load_config().get("ats_boards", {}).get("greenhouse", [])
    board_jobs: list[list[dict]] = []
    for token in tokens:
        try:
            data = _get_json(
                f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true")
            jobs = []
            for j in data.get("jobs", []):
                url = j.get("absolute_url", "")
                if not url:
                    continue
                jobs.append({
                    "title": j.get("title", ""),
                    "company": j.get("company_name") or token,
                    "location": (j.get("location") or {}).get("name", ""),
                    "url": url,
                    "description": _html_to_text(j.get("content", "")),
                    "posted_date": j.get("first_published") or j.get("updated_at") or "",
                    "source": "greenhouse",
                    "salary": "",
                })
            jobs.sort(key=lambda j: j["posted_date"], reverse=True)
            board_jobs.append(jobs[:MAX_JOBS_PER_SOURCE])
        except Exception as e:
            log.info(f"  Greenhouse board {token!r} failed: {e}")
    return _interleave_boards(board_jobs)


def fetch_lever() -> list[dict]:
    """Fetch postings from the Lever boards listed in config's "ats_boards"
    ("lever" -> company slugs). Public no-auth API. No structured salary —
    `salary` stays "" for the analyze pass to extract from descriptions."""
    slugs = load_config().get("ats_boards", {}).get("lever", [])
    board_jobs: list[list[dict]] = []
    for slug in slugs:
        try:
            data = _get_json(f"https://api.lever.co/v0/postings/{slug}?mode=json")
            jobs = []
            for j in data if isinstance(data, list) else []:
                url = j.get("hostedUrl") or j.get("applyUrl") or ""
                if not url:
                    continue
                jobs.append({
                    "title": j.get("text", ""),
                    "company": slug,
                    "location": (j.get("categories") or {}).get("location", ""),
                    "url": url,
                    "description": (j.get("descriptionPlain")
                                    or j.get("descriptionBodyPlain")
                                    or _html_to_text(j.get("descriptionBody") or "")),
                    "posted_date": _epoch_ms_to_date(j.get("createdAt")),
                    "source": "lever",
                    "salary": "",
                })
            jobs.sort(key=lambda j: j["posted_date"], reverse=True)
            board_jobs.append(jobs[:MAX_JOBS_PER_SOURCE])
        except Exception as e:
            log.info(f"  Lever board {slug!r} failed: {e}")
    return _interleave_boards(board_jobs)


def _epoch_ms_to_date(value) -> str:
    """Lever's createdAt (epoch milliseconds) -> 'YYYY-MM-DD'; non-numeric
    values pass through as strings."""
    try:
        return datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
    except (TypeError, ValueError, OSError, OverflowError):
        return str(value or "")


def fetch_adzuna() -> list[dict]:
    """Fetch NZ postings from Adzuna's API (free key: ADZUNA_APP_ID /
    ADZUNA_APP_KEY in .env — without them this source is off and returns
    []). Query shape (what/where/max_days_old) comes from config's
    "adzuna" section. Free tier: 25 hits/min, 250/day — one request per
    run fits easily.

    Adzuna models a salary range when the employer states none
    (salary_is_predicted=1); those get " (est.)" appended so estimates
    never masquerade as employer-stated pay."""
    app_id = (os.environ.get("ADZUNA_APP_ID") or "").strip()
    app_key = (os.environ.get("ADZUNA_APP_KEY") or "").strip()
    if not app_id or not app_key:
        log.info("  ADZUNA_APP_ID/ADZUNA_APP_KEY not set — skipping Adzuna")
        return []
    az = load_config().get("adzuna", {})
    params = urllib.parse.urlencode({
        "app_id": app_id,
        "app_key": app_key,
        "what": az.get("what", ""),
        "where": az.get("where", ""),
        "max_days_old": az.get("max_days_old", 14),
        "results_per_page": az.get("results_per_page", MAX_JOBS_PER_SOURCE),
        "sort_by": az.get("sort_by", "date"),
    })
    try:
        data = _get_json(f"https://api.adzuna.com/v1/api/jobs/nz/search/1?{params}")
        jobs = []
        for j in data.get("results", []):
            url = j.get("redirect_url", "")
            if not url:
                continue
            salary = _format_salary_range(j.get("salary_min"), j.get("salary_max"))
            if salary and j.get("salary_is_predicted"):
                salary += " (est.)"
            jobs.append({
                "title": j.get("title", ""),
                "company": (j.get("company") or {}).get("display_name") or "",
                "location": (j.get("location") or {}).get("display_name") or "",
                "url": url,
                "description": j.get("description", ""),
                "posted_date": j.get("created", ""),
                "source": "adzuna",
                "salary": salary,
            })
        return jobs
    except Exception as e:
        log.info(f"  Adzuna API failed: {e}")
        return []


def fetch_api_jobs() -> list[dict]:
    """Fetch jobs from every source listed in config.json's "api_sources"
    (default: all three — see config.DEFAULT_CONFIG; [] disables API
    sourcing entirely). Each source is capped at MAX_JOBS_PER_SOURCE jobs,
    trusting these feeds' natural newest-first ordering.

    The fetch_*() -> source-name mapping is built here (not at module
    level) so tests can patch.object(sources_api, "fetch_remotive", ...)
    and have fetch_api_jobs() actually see the replacement."""
    fetchers = {
        "remotive.com": fetch_remotive,
        "remoteok.com": fetch_remoteok,
        "arbeitnow.com": fetch_arbeitnow,
        "sjs.co.nz": fetch_sjs,
        "greenhouse": fetch_greenhouse,
        "lever": fetch_lever,
        "adzuna": fetch_adzuna,
    }
    jobs: list[dict] = []
    for name in load_config().get("api_sources", []):
        fetch = fetchers.get(name)
        if fetch is None:
            log.info(f"  Unknown api_sources entry {name!r} — skipping")
            continue
        source_jobs = fetch()[:MAX_JOBS_PER_SOURCE]
        log.info(f"  {name}: {len(source_jobs)} job(s)")
        jobs.extend(source_jobs)
    return jobs
