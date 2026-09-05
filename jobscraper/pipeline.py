# jobscraper/pipeline.py
"""Pipeline orchestration: build search config → scrape → analyze.

Writes (search_config.json, raw_jobs.json, jobs.json, seen.json, plus a
per-run copy of the first three under output/runs/<timestamp>/) happen
here; the builders return data. Raw jobs come from Firecrawl search+scrape
plus, optionally, free job-board APIs (sources_api.fetch_api_jobs).

Cross-run dedupe and run history live in output/jobs.db (store.py): every
scraped job is recorded before scoring and each scoring batch is persisted
as it completes, so a crash mid-run loses nothing — the next run re-scores
only what never landed. output/jobs.json (all scored jobs, accumulated
across runs) and output/seen.json are generated exports of the DB written
at the end of a successful run, so the dashboard keeps working unchanged.
All paths are ROOT-anchored so the pipeline works from any CWD.
"""
import json
import logging
from datetime import datetime
from pathlib import Path

from . import store
from .config import RESUME_FILE, THRESHOLD, load_config, sources_context
from .llm import run_claude_json
from .scrape import scrape_jobs
from .sources_api import fetch_api_jobs

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


def analyze_jobs(jobs: list[dict] | None = None, run_id: int | None = None,
                 db=None, today: str | None = None) -> list[dict]:
    """Run LLM analysis on `jobs` and return the scored jobs.

    `jobs` defaults to None, which reads output/raw_jobs.json (backward
    compat for direct callers/tests). run_pipeline() instead passes only
    the jobs not already scored in output/jobs.db, so previously-scored
    jobs aren't re-sent to the LLM — and billed for — on every run.

    Jobs are sent to the LLM in batches of BATCH_SIZE, since the model has
    no way to read a file itself over HTTP; each batch's jobs are inlined
    as JSON in the same message as the prompt. With `run_id`, each batch's
    results are persisted to the DB the moment they come back (crash-safe
    scoring). Without one, output/jobs.json is written directly from the
    results (legacy direct-caller path).

    When called with explicit `jobs`, this write only reflects `jobs` —
    run_pipeline() overwrites output/jobs.json afterwards with the full DB
    export.
    """
    today = today or datetime.now().strftime("%Y-%m-%d")
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
        batch_results = run_claude_json(str(ROOT / "prompts/analyze.md"), context=context)
        all_jobs.extend(batch_results)
        if run_id is not None:
            store.record_scores(run_id, batch_results, today, db=db,
                                batch_inputs=batch)

    if run_id is None:
        (ROOT / "output/jobs.json").write_text(json.dumps(all_jobs, indent=2))
    return all_jobs


# ── pipeline orchestrator ─────────────────────────────────
def run_pipeline(on_progress=None) -> dict:
    """Run the full 3-step pipeline.

    Calls on_progress(step, label, status) at each stage where:
      step   — int 1-3
      label  — human-readable step name
      status — "running" or "done"

    Raw jobs are Firecrawl search+scrape (scrape_jobs) plus, per
    config.json's "api_sources" (default: all; [] disables), free
    job-board APIs (sources_api.fetch_api_jobs) — merged before anything
    else happens, so API-sourced jobs go through the same dedupe and LLM
    scoring as scraped ones.

    Cross-run state lives in output/jobs.db: every scraped job is recorded
    before scoring, and each scoring batch is persisted as it returns, so
    a crash mid-run loses nothing and the next run re-scores only jobs
    that were never scored. Only unscored jobs are sent to the LLM.
    output/jobs.json (all scored jobs accumulated across runs) and
    output/seen.json are regenerated from the DB at the end of a
    successful run. This run's search_config.json, raw_jobs.json, and
    jobs.json (holding only this run's newly-scored jobs) are also copied
    to output/runs/<timestamp>/ for history.

    Returns {"total", "above_threshold", "new_count", "new_above_threshold",
    "raw_total", "top_jobs", "run_dir"} describing this run's newly-scored
    jobs (not the accumulated output/jobs.json total). "raw_total" is the
    count of raw jobs scraped/fetched this run (new + already-scored).
    "top_jobs" is up to the 5 highest-scoring jobs from this run's newly
    analyzed jobs, each as {"title", "company", "score", "url"} — used by
    jobscraper/schedule.py to build its notification summary. Raises
    RuntimeError on any failure (after marking the run failed in the DB).
    """
    def emit(step, label, status):
        if on_progress:
            on_progress(step, label, status)

    (ROOT / "output").mkdir(exist_ok=True)

    if not (ROOT / RESUME_FILE).exists():
        raise RuntimeError(f"Missing {RESUME_FILE} — add your resume before running.")

    db = ROOT / "output/jobs.db"
    today = datetime.now().strftime("%Y-%m-%d")
    store.bootstrap_from_json(
        ROOT / "output/seen.json", ROOT / "output/jobs.json", today, db=db)

    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    run_dir = ROOT / "output/runs" / timestamp
    run_dir.mkdir(parents=True, exist_ok=True)
    run_id = store.start_run(datetime.now().strftime("%Y-%m-%d %H:%M:%S"), db=db)

    try:
        emit(1, "Building search config", "running")
        config = build_search_config()
        (run_dir / "search_config.json").write_text(json.dumps(config, indent=2))
        search_queries = config.get("search_queries", [])
        if not search_queries:
            raise RuntimeError("No search queries generated — check prompts/build_queries.md")
        emit(1, "Building search config", "done")

        emit(2, "Scraping jobs", "running")
        jobs = scrape_jobs(search_queries) + fetch_api_jobs()
        if not jobs:
            raise RuntimeError(
                "No jobs found — check your FIRECRAWL_API_KEY, search queries, or api_sources config.")
        (ROOT / "output/raw_jobs.json").write_text(json.dumps(jobs, indent=2))
        (run_dir / "raw_jobs.json").write_text(json.dumps(jobs, indent=2))
        store.record_seen(run_id, jobs, today, db=db)
        to_score, already_scored = store.partition_new_jobs(jobs, db=db)
        log.info(f"  {len(to_score)} unscored job(s), {len(already_scored)} already scored "
                 f"— scoring the unscored ones")
        emit(2, "Scraping jobs", "done")

        emit(3, "Analyzing & scoring", "running")
        all_jobs = analyze_jobs(to_score, run_id=run_id, db=db, today=today)
        # Safety-net sweep: whatever this run's scoring returned is in the
        # DB before the exports are rebuilt, even if a per-batch write was
        # skipped (idempotent upserts — no double counting).
        store.record_scores(run_id, all_jobs, today, db=db, batch_inputs=to_score)
        (run_dir / "jobs.json").write_text(json.dumps(all_jobs, indent=2))
        (ROOT / "output/jobs.json").write_text(
            json.dumps(store.load_scored_jobs(db=db), indent=2))
        (ROOT / "output/seen.json").write_text(json.dumps(store.load_seen(db=db), indent=2))
        emit(3, "Analyzing & scoring", "done")

        good_jobs = [j for j in all_jobs if j.get("score", 0) >= THRESHOLD]
        top_jobs = sorted(all_jobs, key=lambda j: j.get("score", 0), reverse=True)[:5]
        store.finish_run(run_id, "ok", raw_total=len(jobs), new_count=len(to_score),
                         above_threshold=len(good_jobs),
                         finished_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"), db=db)
        result = {
            "total": len(all_jobs),
            "above_threshold": len(good_jobs),
            "new_count": len(to_score),
            "new_above_threshold": len(good_jobs),
            "raw_total": len(jobs),
            "top_jobs": [
                {
                    "title": j.get("title", ""),
                    "company": j.get("company", ""),
                    "score": j.get("score", 0),
                    "url": j.get("url", ""),
                }
                for j in top_jobs
            ],
            "run_dir": str(run_dir),
        }
    except BaseException:
        store.finish_run(run_id, "failed",
                         finished_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"), db=db)
        raise

    return result


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
