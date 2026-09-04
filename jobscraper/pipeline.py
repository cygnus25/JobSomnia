# jobscraper/pipeline.py
"""Pipeline orchestration: build search config → scrape → analyze.

Writes (search_config.json, raw_jobs.json, jobs.json) happen here; the
builders return data. All paths are ROOT-anchored so the pipeline works from
any CWD.
"""
import json
import logging
from datetime import datetime
from pathlib import Path

from .config import RESUME_FILE, THRESHOLD, load_config, sources_context
from .llm import run_claude_json
from .scrape import scrape_jobs

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[1]


# ── step 1: build search config from resume ───────────────
def build_search_config() -> dict:
    """Ask the LLM to extract search config from the resume."""
    config = run_claude_json(
        str(ROOT / "prompts/build_queries.md"), context=sources_context(load_config()))
    (ROOT / "output/search_config.json").write_text(json.dumps(config, indent=2))
    log.info(f"  Roles: {config.get('target_roles')}")
    log.info(f"  Skills: {config.get('key_skills')}")
    log.info(f"  Queries ({len(config.get('search_queries', []))}): ready")
    return config


# ── step 3: analyze scraped jobs via LLM ──────────────────
# Jobs per analyze.md LLM call. max_tokens is 8192; sending all scraped jobs
# in one call risks truncating the output JSON and silently dropping jobs.
BATCH_SIZE = 10


def analyze_jobs() -> list[dict]:
    """Run LLM analysis on raw_jobs.json and write output/jobs.json.

    Jobs are read from output/raw_jobs.json and sent to the LLM in batches
    of BATCH_SIZE, since the model has no way to read the file itself over
    HTTP; each batch's jobs are inlined as JSON in the same message as the
    prompt. Results from all batches are merged before writing.
    """
    today = datetime.now().strftime("%Y-%m-%d")
    raw_jobs = json.loads((ROOT / "output/raw_jobs.json").read_text(encoding="utf-8"))

    all_jobs = []
    for i in range(0, len(raw_jobs), BATCH_SIZE):
        batch = raw_jobs[i:i + BATCH_SIZE]
        context = (
            f"Today's date is {today}.\n\n"
            f"Jobs to score (JSON array):\n{json.dumps(batch, indent=2)}"
        )
        all_jobs.extend(run_claude_json(str(ROOT / "prompts/analyze.md"), context=context))

    (ROOT / "output/jobs.json").write_text(json.dumps(all_jobs, indent=2))
    return all_jobs


# ── pipeline orchestrator ─────────────────────────────────
def run_pipeline(on_progress=None) -> dict:
    """Run the full 3-step pipeline.

    Calls on_progress(step, label, status) at each stage where:
      step   — int 1-3
      label  — human-readable step name
      status — "running" or "done"

    Returns {"total": int, "above_threshold": int}.
    Raises RuntimeError on any failure.
    """
    def emit(step, label, status):
        if on_progress:
            on_progress(step, label, status)

    (ROOT / "output").mkdir(exist_ok=True)

    if not (ROOT / RESUME_FILE).exists():
        raise RuntimeError(f"Missing {RESUME_FILE} — add your resume before running.")

    emit(1, "Building search config", "running")
    config = build_search_config()
    search_queries = config.get("search_queries", [])
    if not search_queries:
        raise RuntimeError("No search queries generated — check prompts/build_queries.md")
    emit(1, "Building search config", "done")

    emit(2, "Scraping jobs", "running")
    jobs = scrape_jobs(search_queries)
    if not jobs:
        raise RuntimeError("No jobs found — check your FIRECRAWL_API_KEY or search queries.")
    (ROOT / "output/raw_jobs.json").write_text(json.dumps(jobs, indent=2))
    emit(2, "Scraping jobs", "done")

    emit(3, "Analyzing & scoring", "running")
    all_jobs = analyze_jobs()
    emit(3, "Analyzing & scoring", "done")

    good_jobs = [j for j in all_jobs if j.get("score", 0) >= THRESHOLD]
    return {"total": len(all_jobs), "above_threshold": len(good_jobs)}


# ── main pipeline ─────────────────────────────────────────
def run():
    def print_progress(step, label, status):
        if status == "running":
            log.info(f"\nStep {step}: {label}...")

    try:
        result = run_pipeline(on_progress=print_progress)
        log.info(f"\nDone! {result['above_threshold']} of {result['total']} jobs above threshold ({THRESHOLD})")
    except RuntimeError as e:
        log.info(f"Error: {e}")
