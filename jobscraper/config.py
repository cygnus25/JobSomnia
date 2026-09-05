# jobscraper/config.py
"""Search-sources config: defaults, config.json overrides, prompt context."""
import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[1]

THRESHOLD = 70
RESUME_FILE = "resume.md"
CONFIG_FILE = "config.json"

# Where to search. config.json (same shape) overrides these defaults, so the
# job boards and subreddits can be tailored without editing code or prompts.
DEFAULT_CONFIG = {
    "job_boards": [
        "linkedin.com/jobs",
        "indeed.com",
        "wellfound.com",
        "glassdoor.com",
        "jobstreet.com",
        "onlinejobs.ph",
    ],
    "reddit_groups": [
        {"name": "Job boards", "subreddits": ["jobbit", "remotejobs", "WorkOnline"]},
        {"name": "Freelance/gig", "subreddits": ["freelance", "Upwork"]},
        {
            "name": "Community",
            "subreddits": ["forhire", "digitalnomad", "remotework"],
            "extra_terms": "hiring",
        },
    ],
    # Per-host page caps for stage 2 (scrape.py), keyed by canonical host.
    # 0 drops the host entirely (Firecrawl can't scrape linkedin.com or
    # reddit.com — see UNSCRAPABLE_HOSTS); others bound how many of that
    # host's pages get scraped even within the global MAX_PAGES_TO_SCRAPE
    # budget, so no single source can crowd out the rest.
    "scrape_caps": {
        "linkedin.com": 0,
        "reddit.com": 0,
        "glassdoor.com": 6,
        "wellfound.com": 5,
        "indeed.com": 5,
        "jobstreet.com": 4,
        "onlinejobs.ph": 4,
    },
    # Free public job-board APIs (jobscraper/sources_api.py) queried
    # alongside Firecrawl search+scrape. [] disables API sourcing entirely;
    # config.json can list a subset to query only some of them.
    "api_sources": [
        "remotive.com", "remoteok.com", "arbeitnow.com", "sjs.co.nz",
        "greenhouse", "lever",
    ],
    # ATS watchlists for the api_sources entries "greenhouse" and "lever":
    # company board tokens (boards-api.greenhouse.io/v1/boards/{token}) and
    # company slugs (api.lever.co/v0/postings/{slug}). Discover tokens via
    # a company's jobs page URL or an ATS-finder tool.
    "ats_boards": {
        "greenhouse": ["rocketlab"],
        "lever": ["newzealandtradeandenterprise", "clearpoint", "kpmgnz", "enable"],
    },
}


def load_config() -> dict:
    """Return the search-sources config: DEFAULT_CONFIG overridden by any
    top-level keys present in config.json."""
    cfg = dict(DEFAULT_CONFIG)
    path = ROOT / CONFIG_FILE
    if path.exists():
        cfg.update(json.loads(path.read_text(encoding="utf-8")))
    return cfg


def sources_context(cfg: dict) -> str:
    """Render the configured job boards and subreddit groups as prompt context
    for prompts/build_queries.md."""
    boards = "\n".join(f"- {b}" for b in cfg.get("job_boards", []))
    groups = []
    for g in cfg.get("reddit_groups", []):
        line = f"- {g['name']}: " + ", ".join(f"r/{s}" for s in g.get("subreddits", []))
        if g.get("extra_terms"):
            line += f' (also include the term "{g["extra_terms"]}" in the query)'
        groups.append(line)
    return (
        "\nJob boards to cover (one query each):\n"
        + boards
        + "\n\nReddit subreddit groups (one grouped query each):\n"
        + "\n".join(groups)
    )
