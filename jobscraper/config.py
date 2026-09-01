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
