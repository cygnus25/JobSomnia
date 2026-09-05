# jobscraper/schedule.py
"""Scheduled entrypoint: run the full pipeline headlessly and print a
plain-text notification summary to stdout — this is what a Windows Task
Scheduler task or a cron job captures (e.g. by redirecting output to a log
file, or piping it into a mail command).

Usage:
    python -m jobscraper.schedule

On success, prints a summary of the run (timestamp, raw jobs scraped, new
jobs, jobs scoring >= THRESHOLD, and up to 5 top-scoring jobs) and exits 0.
On failure, prints a clear error line and exits with code 1 so the
scheduler records the run as failed. See README.md's "Scheduled runs"
section for schtasks/cron setup examples.
"""
import sys
from datetime import datetime

from .config import THRESHOLD
from .notify import notify_run_failed, notify_run_result
from .pipeline import run_pipeline

MAX_TOP_JOBS = 5


def _format_summary(result: dict, timestamp: str) -> str:
    """Render `result` (run_pipeline()'s return value) as a plain-text
    notification summary."""
    lines = [
        "AI Job Scraper - scheduled run summary",
        f"Run time: {timestamp}",
        f"Raw jobs scraped: {result.get('raw_total', 0)}",
        f"New jobs: {result.get('new_count', 0)}",
        f"Jobs scoring >= {THRESHOLD}: {result.get('above_threshold', 0)}",
        "",
    ]

    top_jobs = result.get("top_jobs", [])[:MAX_TOP_JOBS]
    if top_jobs:
        lines.append(f"Top {len(top_jobs)} job(s):")
        for i, job in enumerate(top_jobs, start=1):
            title = job.get("title") or "(untitled)"
            company = job.get("company") or "(unknown company)"
            score = job.get("score", 0)
            url = job.get("url", "")
            lines.append(f"{i}. {title} - {company} - score {score} - {url}")
    else:
        lines.append("No new jobs scored this run.")

    return "\n".join(lines)


def run_scheduled() -> None:
    """Run the full pipeline and print a plain-text summary to stdout.

    Intended as the entrypoint for a Windows Task Scheduler task or a cron
    job (`python -m jobscraper.schedule`). On failure, prints a clear
    error line and calls sys.exit(1) so the scheduler flags the run as
    failed.
    """
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    try:
        result = run_pipeline()
    except Exception as exc:
        print(f"ERROR: scheduled run failed: {exc}")
        notify_run_failed(exc)
        sys.exit(1)
        return

    print(_format_summary(result, timestamp))
    notify_run_result(result, THRESHOLD)


if __name__ == "__main__":
    run_scheduled()
