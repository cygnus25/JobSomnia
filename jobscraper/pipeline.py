# jobscraper/pipeline.py
"""Pipeline orchestration: build search config → scrape → analyze.

Writes (search_config.json, raw_jobs.json, jobs.json, seen.json, plus a
per-run copy of the first three under output/runs/<timestamp>/) happen
here; the builders return data. All paths are ROOT-anchored so the pipeline
works from any CWD.
"""
import json
import logging
from datetime import datetime
from pathlib import Path

from .config import RESUME_FILE, THRESHOLD, load_config, sources_context
from .llm import run_claude_json
from .scrape import dedup_key, scrape_jobs

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


def analyze_jobs(jobs: list[dict] | None = None) -> list[dict]:
    """Run LLM analysis on `jobs` and write output/jobs.json.

    `jobs` defaults to None, which reads output/raw_jobs.json (backward
    compat for direct callers/tests). run_pipeline() instead passes only
    the jobs not already in output/seen.json, so previously-scored jobs
    aren't re-sent to the LLM — and billed for — on every run.

    Jobs are sent to the LLM in batches of BATCH_SIZE, since the model has
    no way to read a file itself over HTTP; each batch's jobs are inlined
    as JSON in the same message as the prompt. Results from all batches are
    merged before writing.
    """
    today = datetime.now().strftime("%Y-%m-%d")
    jobs_to_score: list[dict] = (
        jobs if jobs is not None
        else json.loads((ROOT / "output/raw_jobs.json").read_text(encoding="utf-8"))
    )

    all_jobs = []
    for i in range(0, len(jobs_to_score), BATCH_SIZE):
        batch = jobs_to_score[i:i + BATCH_SIZE]
        context = (
            f"Today's date is {today}.\n\n"
            f"Jobs to score (JSON array):\n{json.dumps(batch, indent=2)}"
        )
        all_jobs.extend(run_claude_json(str(ROOT / "prompts/analyze.md"), context=context))

    (ROOT / "output/jobs.json").write_text(json.dumps(all_jobs, indent=2))
    return all_jobs


# ── cross-run dedupe (output/seen.json) ─────────────────────
def _load_seen() -> dict:
    """Load the cross-run dedupe record, or {} on the first-ever run."""
    path = ROOT / "output/seen.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def _partition_new_jobs(jobs: list[dict], seen: dict) -> tuple[list[dict], list[dict]]:
    """Split this run's scraped jobs into ones never recorded in `seen`
    (score these) and ones already seen in a prior run (skip scoring —
    that's the whole point of tracking them)."""
    new_jobs, already_seen = [], []
    for job in jobs:
        target = new_jobs if dedup_key(job["url"]) not in seen else already_seen
        target.append(job)
    return new_jobs, already_seen


def _update_seen(seen: dict, jobs: list[dict], today: str) -> None:
    """Record every job scraped this run in `seen`, in place: new jobs get
    a fresh record, repeats get last_seen bumped and runs_seen incremented."""
    for job in jobs:
        key = dedup_key(job["url"])
        record = seen.get(key)
        if record is None:
            seen[key] = {
                "url": job["url"],
                "title": job.get("title", ""),
                "first_seen": today,
                "last_seen": today,
                "runs_seen": 1,
            }
        else:
            record["last_seen"] = today
            record["runs_seen"] = record.get("runs_seen", 1) + 1


# ── pipeline orchestrator ─────────────────────────────────
def run_pipeline(on_progress=None) -> dict:
    """Run the full 3-step pipeline.

    Calls on_progress(step, label, status) at each stage where:
      step   — int 1-3
      label  — human-readable step name
      status — "running" or "done"

    Jobs already recorded in output/seen.json (from a prior run) are
    skipped during scoring — only newly-discovered jobs are sent to the
    LLM. This run's search_config.json, raw_jobs.json, and jobs.json are
    also copied to output/runs/<timestamp>/ for history, alongside the
    existing output/ writes.

    Returns {"total", "above_threshold", "new_count", "new_above_threshold",
    "run_dir"}. Raises RuntimeError on any failure.
    """
    def emit(step, label, status):
        if on_progress:
            on_progress(step, label, status)

    (ROOT / "output").mkdir(exist_ok=True)

    if not (ROOT / RESUME_FILE).exists():
        raise RuntimeError(f"Missing {RESUME_FILE} — add your resume before running.")

    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    run_dir = ROOT / "output/runs" / timestamp
    run_dir.mkdir(parents=True, exist_ok=True)

    emit(1, "Building search config", "running")
    config = build_search_config()
    (run_dir / "search_config.json").write_text(json.dumps(config, indent=2))
    search_queries = config.get("search_queries", [])
    if not search_queries:
        raise RuntimeError("No search queries generated — check prompts/build_queries.md")
    emit(1, "Building search config", "done")

    emit(2, "Scraping jobs", "running")
    jobs = scrape_jobs(search_queries)
    if not jobs:
        raise RuntimeError("No jobs found — check your FIRECRAWL_API_KEY or search queries.")
    (ROOT / "output/raw_jobs.json").write_text(json.dumps(jobs, indent=2))
    (run_dir / "raw_jobs.json").write_text(json.dumps(jobs, indent=2))
    emit(2, "Scraping jobs", "done")

    seen = _load_seen()
    new_jobs, already_seen = _partition_new_jobs(jobs, seen)
    log.info(f"  {len(new_jobs)} new job(s), {len(already_seen)} already seen — scoring only the new ones")

    emit(3, "Analyzing & scoring", "running")
    all_jobs = analyze_jobs(new_jobs)
    (run_dir / "jobs.json").write_text(json.dumps(all_jobs, indent=2))
    emit(3, "Analyzing & scoring", "done")

    today = datetime.now().strftime("%Y-%m-%d")
    _update_seen(seen, jobs, today)
    (ROOT / "output/seen.json").write_text(json.dumps(seen, indent=2))

    good_jobs = [j for j in all_jobs if j.get("score", 0) >= THRESHOLD]
    return {
        "total": len(all_jobs),
        "above_threshold": len(good_jobs),
        "new_count": len(new_jobs),
        "new_above_threshold": len(good_jobs),
        "run_dir": str(run_dir),
    }


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
