# jobscraper/notify.py — run-finished push notification via ntfy.sh.
#
# ntfy.sh needs no account: POSTing to https://ntfy.sh/<topic> pushes to
# every device subscribed to that topic (docs.ntfy.sh/publish). The topic
# is the only credential — publishing is unauthenticated, so it lives in
# .env as NTFY_TOPIC with an unguessable random suffix (see .env.example).
#
# notify_*() are the pipeline-facing entry points and are deliberately
# best-effort: a dead notification service logs and returns, never failing
# the run. send() raises so tests and direct callers can assert on it.
import logging
import os
import urllib.parse
import urllib.request

log = logging.getLogger(__name__)

NTFY_BASE = "https://ntfy.sh"
TIMEOUT_S = 15


def send(message: str, title: str = "") -> None:
    """POST `message` to the ntfy.sh topic named by $NTFY_TOPIC.

    Raises ValueError when NTFY_TOPIC is unset and propagates network
    errors — callers who can't tolerate that should use the notify_*()
    wrappers instead.
    """
    topic = (os.environ.get("NTFY_TOPIC") or "").strip()
    if not topic:
        raise ValueError("NTFY_TOPIC not set — add it to .env (see .env.example)")
    req = urllib.request.Request(
        f"{NTFY_BASE}/{urllib.parse.quote(topic)}",
        data=message.encode("utf-8"),
        headers={"Title": title} if title else {},
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
        resp.read()  # drain the response so the connection closes cleanly


def run_summary(result: dict, threshold: int) -> tuple[str, str]:
    """(title, body) describing a finished run from run_pipeline()'s
    return value — 'N new jobs, top: Title @ Company (score)'."""
    new_count = result.get("new_count", 0)
    above = result.get("above_threshold", 0)
    title = "JobScraper: run finished"
    parts = [f"{new_count} new job(s), {above} scored >= {threshold}"]
    top_jobs = result.get("top_jobs") or []
    if top_jobs:
        job = top_jobs[0]
        parts.append(
            "Top: {} @ {} ({})".format(
                job.get("title") or "(untitled)",
                job.get("company") or "(unknown company)",
                job.get("score", 0),
            )
        )
    return title, ". ".join(parts)


def notify_run_result(result: dict, threshold: int) -> None:
    """Best-effort push of a finished-run summary. Never raises."""
    try:
        title, body = run_summary(result, threshold)
        send(body, title=title)
        log.info(f"  ntfy notification sent: {body}")
    except Exception as e:
        log.info(f"  ntfy notification failed: {e}")


def notify_run_failed(error: Exception) -> None:
    """Best-effort push of a run-failure alert. Never raises."""
    try:
        send(f"Scheduled run failed: {error}", title="JobScraper: run FAILED")
    except Exception as e:
        log.info(f"  ntfy failure alert not sent: {e}")
