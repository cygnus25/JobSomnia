# jobscraper/scrape.py
"""Stage 2: discover candidate pages via Firecrawl search, scrape each page,
extract individual job postings, dedupe."""
import json
import logging
import os
import re
import time
from datetime import datetime
from itertools import zip_longest
from urllib.parse import urljoin, urlparse

from firecrawl import FirecrawlApp

from .config import ROOT, load_config

log = logging.getLogger(__name__)

# Stage 2 (scrape) limits — a search hit is often a listing/category page
# (e.g. ph.jobstreet.com/nextjs-jobs) that contains many postings. We scrape
# each discovered URL and extract the individual postings from it. Cap the
# number of pages scraped per run so credit usage stays bounded.
MAX_PAGES_TO_SCRAPE = 20
SCRAPE_TIMEOUT_MS = 120000  # listing pages (JobStreet, LinkedIn) are JS-heavy

# Hosts Firecrawl's scrape() rejects outright ("Website Not Supported") —
# canonical form. Scraping these just burns budget for a guaranteed failure,
# so extract_postings() skips straight to the search-snippet fallback.
UNSCRAPABLE_HOSTS = {"linkedin.com", "reddit.com"}

# Hosts that failed this many scrapes in a row get persisted to
# FAILED_HOSTS_FILE and demoted to snippet-only on later runs, so a newly
# broken board stops burning JSON-scrape credits (5/page) every run.
SCRAPE_FAILURES_BEFORE_SKIP = 3
FAILED_HOSTS_FILE = ROOT / "output/failed_hosts.json"

# 429 handling: Firecrawl returns Retry-After seconds; honor it once per
# page, clamped so a bogus server value can't eat the 1200s run budget.
RETRY_AFTER_DEFAULT_S = 20
RETRY_AFTER_MAX_S = 60

# What Firecrawl should pull out of each scraped page.
EXTRACT_PROMPT = (
    "Extract every individual job posting on this page. For each posting capture "
    "the job title, the hiring company, the location, the direct URL to that "
    "specific posting (not this listing/search page), the date it was posted, and "
    "a short description. If the page is already a single job posting, return just "
    "that one. Ignore navigation links, ads, related searches, and other pages."
)
JOB_EXTRACT_SCHEMA = {
    "type": "object",
    "properties": {
        "jobs": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "company": {"type": "string"},
                    "location": {"type": "string"},
                    "url": {
                        "type": "string",
                        "description": "Direct link to the individual job posting.",
                    },
                    "posted_date": {"type": "string"},
                    "description": {"type": "string"},
                },
                "required": ["title"],
            },
        }
    },
    "required": ["jobs"],
}


def _canonical_host(host: str) -> str:
    """Collapse www. and two-letter regional prefixes (in.indeed.com,
    uk.linkedin.com, ph.jobstreet.com) so one site isn't treated as many."""
    host = host.lower()
    if host.startswith("www."):
        host = host[4:]
    labels = host.split(".")
    if len(labels) >= 3 and len(labels[0]) == 2:
        host = ".".join(labels[1:])
    return host


def _dedup_key(url: str) -> str:
    """Key for URL deduplication: canonical host + path + query."""
    p = urlparse(url)
    return f"{_canonical_host(p.netloc)}{p.path}?{p.query}"


# Public alias — pipeline.py uses this as the cross-run key in output/seen.json.
dedup_key = _dedup_key


# ── failed-host demotion (output/failed_hosts.json) ──────────
def _load_failed_hosts() -> dict:
    try:
        return json.loads(FAILED_HOSTS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_failed_hosts(hosts: dict) -> None:
    try:
        FAILED_HOSTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        FAILED_HOSTS_FILE.write_text(json.dumps(hosts, indent=2))
    except OSError as e:
        log.info(f"  Could not persist failed-hosts map: {e}")


def _record_scrape_failure(source: str) -> None:
    """Bump `source`'s consecutive-failure count. At the threshold it is
    demoted to snippet-only for future runs; any successful scrape clears
    the record (see _record_scrape_success)."""
    hosts = _load_failed_hosts()
    record = hosts.setdefault(source, {})
    record["consecutive_failures"] = record.get("consecutive_failures", 0) + 1
    record["last_failure"] = datetime.now().strftime("%Y-%m-%d")
    _save_failed_hosts(hosts)
    if record["consecutive_failures"] >= SCRAPE_FAILURES_BEFORE_SKIP:
        log.info(f"  {source} demoted to snippet-only after "
                 f"{record['consecutive_failures']} failed scrapes")


def _record_scrape_success(source: str) -> None:
    hosts = _load_failed_hosts()
    if source in hosts:
        del hosts[source]
        _save_failed_hosts(hosts)


def _firecrawl_status(e: Exception) -> int | None:
    """HTTP status of a Firecrawl error. The current SDK raises
    FirecrawlError subclasses carrying .status_code; fall back to
    requests-style .response and finally the message text so other SDK
    versions still classify."""
    status = getattr(e, "status_code", None)
    if status is None:
        resp = getattr(e, "response", None)
        status = getattr(resp, "status_code", None)
    if status is None:
        m = re.search(r"\b(40\d|42\d)\b", str(e))
        return int(m.group(1)) if m else None
    try:
        return int(status)
    except (TypeError, ValueError):
        return None


def _retry_after_seconds(e: Exception) -> float:
    """Retry-After header as seconds for a 429, clamped to [0, 60];
    falls back to RETRY_AFTER_DEFAULT_S when absent or unparseable."""
    resp = getattr(e, "response", None)
    raw = resp.headers.get("Retry-After") if resp is not None else None
    try:
        return max(0.0, min(float(raw), RETRY_AFTER_MAX_S))
    except (TypeError, ValueError):
        return RETRY_AFTER_DEFAULT_S


def discover_pages(app: "FirecrawlApp", search_queries: list[str]) -> list[dict]:
    """Run each search query and return unique candidate pages.

    A page may be a single posting OR a listing/category page that contains
    many postings — stage 1b sorts that out by scraping.

    Results are interleaved round-robin across queries (first hit of each
    query, then second of each, ...) so the MAX_PAGES_TO_SCRAPE cap doesn't
    starve sources whose queries run later in the list.
    """
    seen_urls: set[str] = set()
    results_per_query: list[list[dict]] = []

    for query in search_queries:
        log.info(f"  Searching: {query[:70]}...")
        hits: list[dict] = []
        try:
            response = app.search(query, limit=10)
            for r in response.web or []:
                url = r.url
                if not url or _dedup_key(url) in seen_urls:
                    continue
                seen_urls.add(_dedup_key(url))
                hits.append({
                    "url": url,
                    "title": r.title or "",
                    "description": r.description or "",
                })
        except Exception as e:
            log.info(f"  Query failed: {e}")
        log.info(f"    {len(hits)} new result(s)")
        results_per_query.append(hits)

    return [page for group in zip_longest(*results_per_query) for page in group if page]


def extract_postings(app: "FirecrawlApp", page: dict) -> list[dict]:
    """Scrape one page and extract the individual job postings it contains.

    Falls back to the search snippet (treated as a single posting) if the
    scrape fails or the page yields no structured postings, so we never lose
    a result that was already an individual posting.
    """
    listing_url = page["url"]
    source = _canonical_host(urlparse(listing_url).netloc)

    def _snippet_fallback() -> list[dict]:
        return [{
            "title": page["title"],
            "company": "",
            "location": "Remote",
            "url": listing_url,
            "description": page["description"],
            "posted_date": "",
            "source": source,
        }]

    if source in UNSCRAPABLE_HOSTS:
        log.info(f"    Skipping scrape ({source} is unscrapable) — keeping search snippet")
        return _snippet_fallback()

    failures = _load_failed_hosts().get(source, {}).get("consecutive_failures", 0)
    if failures >= SCRAPE_FAILURES_BEFORE_SKIP:
        log.info(f"    Skipping scrape ({source} failed {failures} scrapes in a row "
                 f"previously) — keeping search snippet")
        return _snippet_fallback()

    def _scrape():
        return app.scrape(
            listing_url,
            formats=[{"type": "json", "prompt": EXTRACT_PROMPT, "schema": JOB_EXTRACT_SCHEMA}],
            only_main_content=True,
            timeout=SCRAPE_TIMEOUT_MS,
        )

    try:
        doc = _scrape()
    except Exception as e:
        status = _firecrawl_status(e)
        if status == 402:
            raise RuntimeError(
                "Firecrawl credits exhausted (HTTP 402) — aborting the run instead of "
                "silently degrading every page to a search snippet. Top up at "
                "firecrawl.dev/pricing or wait for the monthly reset.") from e
        if status == 429:
            wait = _retry_after_seconds(e)
            log.info(f"    Rate limited (429) — sleeping {wait:.0f}s, retrying once")
            time.sleep(wait)
            try:
                doc = _scrape()
            except Exception as retry_err:
                if _firecrawl_status(retry_err) == 402:
                    raise RuntimeError(
                        "Firecrawl credits exhausted (HTTP 402) — aborting the run "
                        "instead of silently degrading every page to a search snippet. "
                        "Top up at firecrawl.dev/pricing or wait for the monthly reset."
                    ) from retry_err
                log.info(f"    Retry failed ({source}): {retry_err} — keeping search snippet")
                _record_scrape_failure(source)
                return _snippet_fallback()
        else:
            log.info(f"    Scrape failed ({source}): {e} — keeping search snippet")
            _record_scrape_failure(source)
            return _snippet_fallback()

    _record_scrape_success(source)

    data = doc.json if isinstance(doc.json, dict) else {}
    raw_postings = data.get("jobs") or []
    if not raw_postings:
        return _snippet_fallback()

    postings: list[dict] = []
    for p in raw_postings:
        if not isinstance(p, dict) or not (p.get("title") or "").strip():
            continue
        # Resolve the posting URL relative to the page; fall back to the page URL.
        posting_url = (p.get("url") or "").strip()
        posting_url = urljoin(listing_url, posting_url) if posting_url else listing_url
        postings.append({
            "title": p.get("title", "").strip(),
            "company": (p.get("company") or "").strip(),
            "location": (p.get("location") or "Remote").strip() or "Remote",
            "url": posting_url,
            "description": (p.get("description") or "").strip(),
            "posted_date": (p.get("posted_date") or "").strip(),
            "source": source,
        })
    return postings or _snippet_fallback()


def _apply_scrape_caps(pages: list[dict], scrape_caps: dict) -> list[dict]:
    """Drop pages whose canonical host has a cap of 0, and cap every other
    host at its configured number of pages (config.json "scrape_caps").
    Hosts not listed keep the global MAX_PAGES_TO_SCRAPE limit, applied
    afterwards in scrape_jobs(). Simple per-host counter — good enough since
    page counts here are small (tens, not thousands)."""
    host_counts: dict[str, int] = {}
    capped = []
    for page in pages:
        host = _canonical_host(urlparse(page["url"]).netloc)
        cap = scrape_caps.get(host, MAX_PAGES_TO_SCRAPE)
        if host_counts.get(host, 0) >= cap:
            continue
        host_counts[host] = host_counts.get(host, 0) + 1
        capped.append(page)
    return capped


def scrape_jobs(search_queries: list[str]) -> list[dict]:
    app = FirecrawlApp(api_key=os.environ["FIRECRAWL_API_KEY"])
    scrape_caps = load_config().get("scrape_caps", {})

    pages = discover_pages(app, search_queries)
    log.info(f"  Discovered {len(pages)} candidate pages")
    pages = _apply_scrape_caps(pages, scrape_caps)
    log.info(f"  {len(pages)} after per-host caps; scraping up to {MAX_PAGES_TO_SCRAPE}...")

    seen_urls: set[str] = set()
    jobs: list[dict] = []
    for page in pages[:MAX_PAGES_TO_SCRAPE]:
        log.info(f"  Scraping: {page['url'][:70]}...")
        for job in extract_postings(app, page):
            url = job["url"]
            if not url or _dedup_key(url) in seen_urls:
                continue
            seen_urls.add(_dedup_key(url))
            jobs.append(job)

    return jobs
