# jobscraper/scrape.py
"""Stage 2: discover candidate pages via Firecrawl search, scrape each page,
extract individual job postings, dedupe."""
import logging
import os
from itertools import zip_longest
from urllib.parse import urljoin, urlparse

from firecrawl import FirecrawlApp

log = logging.getLogger(__name__)

# Stage 2 (scrape) limits — a search hit is often a listing/category page
# (e.g. ph.jobstreet.com/nextjs-jobs) that contains many postings. We scrape
# each discovered URL and extract the individual postings from it. Cap the
# number of pages scraped per run so credit usage stays bounded.
MAX_PAGES_TO_SCRAPE = 20
SCRAPE_TIMEOUT_MS = 120000  # listing pages (JobStreet, LinkedIn) are JS-heavy

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

    try:
        doc = app.scrape(
            listing_url,
            formats=[{"type": "json", "prompt": EXTRACT_PROMPT, "schema": JOB_EXTRACT_SCHEMA}],
            only_main_content=True,
            timeout=SCRAPE_TIMEOUT_MS,
        )
    except Exception as e:
        log.info(f"    Scrape failed ({source}): {e} — keeping search snippet")
        return _snippet_fallback()

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


def scrape_jobs(search_queries: list[str]) -> list[dict]:
    app = FirecrawlApp(api_key=os.environ["FIRECRAWL_API_KEY"])

    pages = discover_pages(app, search_queries)
    log.info(f"  Discovered {len(pages)} candidate pages; scraping up to {MAX_PAGES_TO_SCRAPE}...")

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
