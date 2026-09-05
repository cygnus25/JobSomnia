# jobscraper/store.py — SQLite run history + crash-safe dedupe.
#
# One file (output/jobs.db, stdlib sqlite3, WAL mode) is the source of truth
# for cross-run dedupe: every scraped job is upserted as soon as it's
# fetched and every scored batch is persisted as it completes, so a crash
# mid-run (e.g. the scheduler's 1200s timeout) loses nothing — the next run
# re-scores only the batches that never landed. seen.json / jobs.json stay
# on disk as generated exports so the dashboard and any external tooling
# keep working unchanged.
#
# Every function takes an explicit `db` path (defaulting to DB_PATH) so
# callers own their pathing and tests can point the store at tmp dirs.
import json
import logging
import sqlite3
from pathlib import Path

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "output/jobs.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL DEFAULT 'running',
    raw_total INTEGER,
    new_count INTEGER,
    above_threshold INTEGER
);
CREATE TABLE IF NOT EXISTS jobs (
    dedup_key TEXT PRIMARY KEY,
    url TEXT NOT NULL,
    title TEXT,
    company TEXT,
    source TEXT,
    location TEXT,
    salary TEXT,
    score REAL,
    verdict TEXT,
    status TEXT NOT NULL DEFAULT 'none',
    data TEXT,
    first_seen TEXT,
    last_seen TEXT,
    runs_seen INTEGER NOT NULL DEFAULT 1,
    first_seen_run INTEGER REFERENCES runs(id),
    last_seen_run INTEGER REFERENCES runs(id),
    last_scored_run INTEGER REFERENCES runs(id)
);
"""


def _resolve(db) -> Path:
    return Path(db) if db is not None else DB_PATH


def connect(db=None) -> sqlite3.Connection:
    """Open `db` (creating it and the schema if needed) in WAL mode so the
    dashboard's reads never block the pipeline's writes."""
    path = _resolve(db)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    return conn


# ── run history ──────────────────────────────────────────────
def start_run(started_at: str, db=None) -> int:
    conn = connect(db)
    try:
        # A run still marked 'running' here never finished (killed process)
        # — close it out so run history can't show phantom live runs.
        conn.execute(
            "UPDATE runs SET status = 'failed', "
            "finished_at = COALESCE(finished_at, ?) WHERE status = 'running'",
            (started_at,))
        cur = conn.execute("INSERT INTO runs (started_at) VALUES (?)", (started_at,))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def finish_run(run_id: int, status: str, raw_total=None, new_count=None,
               above_threshold=None, finished_at=None, db=None) -> None:
    conn = connect(db)
    try:
        conn.execute(
            "UPDATE runs SET finished_at = ?, status = ?, raw_total = ?, "
            "new_count = ?, above_threshold = ? WHERE id = ?",
            (finished_at, status, raw_total, new_count, above_threshold, run_id))
        conn.commit()
    finally:
        conn.close()


def run_history(db=None, limit: int = 20) -> list[dict]:
    """Recent runs, newest first (dashboard /api/runs)."""
    conn = connect(db)
    try:
        rows = conn.execute(
            "SELECT id, started_at, finished_at, status, raw_total, new_count, "
            "above_threshold FROM runs ORDER BY id DESC LIMIT ?", (limit,))
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ── dedupe + scored-job records ──────────────────────────────
def record_seen(run_id: int, jobs: list[dict], today: str, db=None) -> None:
    """Upsert every job scraped this run — before any LLM scoring — so the
    dedupe state survives a crash whenever a run dies. Repeats bump
    last_seen/runs_seen; first_seen/first_seen_run stay put."""
    from .scrape import dedup_key

    conn = connect(db)
    try:
        for job in jobs:
            url = job.get("url") or ""
            if not url:
                continue
            conn.execute(
                """
                INSERT INTO jobs (dedup_key, url, title, company, source, location,
                                  salary, first_seen, last_seen, runs_seen,
                                  first_seen_run, last_seen_run)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                ON CONFLICT(dedup_key) DO UPDATE SET
                    title = excluded.title,
                    company = excluded.company,
                    source = excluded.source,
                    location = excluded.location,
                    salary = CASE WHEN excluded.salary != '' THEN excluded.salary
                                  ELSE jobs.salary END,
                    last_seen = excluded.last_seen,
                    last_seen_run = excluded.last_seen_run,
                    runs_seen = jobs.runs_seen + 1
                """,
                (dedup_key(url), url, job.get("title", ""), job.get("company", ""),
                 job.get("source", ""), job.get("location", ""), job.get("salary", ""),
                 today, today, run_id, run_id))
        conn.commit()
    finally:
        conn.close()


def partition_new_jobs(jobs: list[dict], db=None) -> tuple[list[dict], list[dict]]:
    """Split scraped jobs into those still needing an LLM score and those
    already scored-or-attempted (skip: the DB has their results, or the
    scorer saw them in an earlier completed batch and omitted them —
    analyze.md only returns jobs scoring >= THRESHOLD). Keying on
    attempted-ness rather than mere seen-ness is what makes a killed run
    recoverable: only batches that never returned get re-billed."""
    from .scrape import dedup_key

    conn = connect(db)
    try:
        attempted = {r["dedup_key"] for r in conn.execute(
            "SELECT dedup_key FROM jobs WHERE data IS NOT NULL "
            "OR last_scored_run IS NOT NULL")}
    finally:
        conn.close()
    to_score, already_scored = [], []
    for job in jobs:
        key = dedup_key(job.get("url") or "")
        target = already_scored if key in attempted else to_score
        target.append(job)
    return to_score, already_scored


def record_scores(run_id: int, scored_jobs: list[dict], today: str, db=None,
                  batch_inputs: list[dict] | None = None) -> None:
    """Persist one scoring batch: the full LLM output as `data` (rebuilt
    into the jobs.json export later) plus the extracted score/verdict for
    SQL views. Called once per analyze batch, so a crash between batches
    only loses the batches that never returned.

    Results are matched back to `batch_inputs` (url echo, then title, then
    position) so scored-ness keys on the job we actually scraped, not on
    the LLM reliably echoing its url — a dropped url must not cause the
    job to be re-billed on every run. Rows are keyed by the input job's
    url; result JSON is stored verbatim.

    Every batch input is marked last_scored_run — with a result when it
    has one, without when the scorer omitted it (sub-threshold jobs are
    deliberately not returned by analyze.md). Only inputs of batches that
    never returned stay unmarked, so alone they get re-scored after a
    crash."""
    from .scrape import dedup_key

    inputs = list(batch_inputs or [])
    by_url: dict = {}
    by_title: dict = {}
    for inp in inputs:
        u = inp.get("url") or ""
        if u:
            by_url.setdefault(dedup_key(u), inp)
        t = (inp.get("title") or "").strip().lower()
        if t:
            by_title.setdefault(t, inp)

    def _match(result: dict):
        r_url = result.get("url") or ""
        if r_url and dedup_key(r_url) in by_url:
            return by_url[dedup_key(r_url)]
        t = (result.get("title") or "").strip().lower()
        if t and t in by_title:
            return by_title[t]
        return None

    conn = connect(db)
    try:
        matched_keys: set[str] = set()
        for idx, result in enumerate(scored_jobs):
            match = _match(result)
            if match is None and idx < len(inputs):
                match = inputs[idx]  # positional last resort (batches are ordered)
            url = (match or {}).get("url") or result.get("url") or ""
            if not url:
                continue  # can't be deduped; stays in this run's in-memory results only
            matched_keys.add(dedup_key(url))
            score = result.get("score")
            try:
                score = float(score) if score is not None else None
            except (TypeError, ValueError):
                score = None
            conn.execute(
                """
                INSERT INTO jobs (dedup_key, url, title, company, source, location,
                                  salary, score, verdict, data, first_seen, last_seen,
                                  runs_seen, first_seen_run, last_seen_run, last_scored_run)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
                ON CONFLICT(dedup_key) DO UPDATE SET
                    title = excluded.title,
                    company = excluded.company,
                    source = excluded.source,
                    location = excluded.location,
                    salary = CASE WHEN excluded.salary != '' THEN excluded.salary
                                  ELSE jobs.salary END,
                    score = excluded.score,
                    verdict = excluded.verdict,
                    data = excluded.data,
                    last_seen = excluded.last_seen,
                    last_seen_run = excluded.last_seen_run,
                    last_scored_run = excluded.last_scored_run
                """,
                (dedup_key(url), url,
                 result.get("title") or (match or {}).get("title", ""),
                 result.get("company") or (match or {}).get("company", ""),
                 result.get("source") or (match or {}).get("source", ""),
                 result.get("location") or (match or {}).get("location", ""),
                 result.get("salary") or (match or {}).get("salary", ""),
                 score, result.get("verdict", ""), json.dumps(result), today, today,
                 run_id, run_id, run_id))
        for inp in inputs:
            # Mark inputs the scorer omitted (sub-threshold) as attempted
            # so they aren't re-billed on every run.
            url = inp.get("url") or ""
            if not url or dedup_key(url) in matched_keys:
                continue
            conn.execute(
                "UPDATE jobs SET last_scored_run = ?, last_seen_run = ?, last_seen = ? "
                "WHERE dedup_key = ?", (run_id, run_id, today, dedup_key(url)))
        conn.commit()
    finally:
        conn.close()


# ── generated exports (seen.json / jobs.json) ────────────────
def load_seen(db=None) -> dict:
    """The jobs table as a seen.json-shaped dict for the export."""
    conn = connect(db)
    try:
        rows = conn.execute(
            "SELECT dedup_key, url, title, first_seen, last_seen, runs_seen FROM jobs")
        return {
            r["dedup_key"]: {
                "url": r["url"],
                "title": r["title"] or "",
                "first_seen": r["first_seen"] or "",
                "last_seen": r["last_seen"] or "",
                "runs_seen": r["runs_seen"],
            }
            for r in rows
        }
    finally:
        conn.close()


def load_scored_jobs(db=None, run_id: int | None = None) -> list[dict]:
    """All scored jobs (jobs.json export), oldest first so this run's fresh
    scores land at the end — same shape the old end-of-run merge produced.
    With `run_id`, only the jobs that run scored."""
    conn = connect(db)
    try:
        if run_id is None:
            rows = conn.execute("SELECT data FROM jobs WHERE data IS NOT NULL ORDER BY rowid")
        else:
            rows = conn.execute(
                "SELECT data FROM jobs WHERE data IS NOT NULL AND last_scored_run = ? "
                "ORDER BY rowid", (run_id,))
        return [json.loads(r["data"]) for r in rows]
    finally:
        conn.close()


# ── one-time import of pre-SQLite state ──────────────────────
def bootstrap_from_json(seen_path: Path, jobs_path: Path, today: str, db=None) -> bool:
    """Import output/seen.json + output/jobs.json from before the SQLite
    upgrade so dedupe history (and prior LLM scores — no re-billing) carry
    over. No-ops when the DB already has jobs. Returns True on import."""
    conn = connect(db)
    try:
        if conn.execute("SELECT 1 FROM jobs LIMIT 1").fetchone():
            return False
    finally:
        conn.close()

    from .scrape import dedup_key

    imported = 0
    if seen_path.exists():
        seen = json.loads(seen_path.read_text(encoding="utf-8"))
        conn = connect(db)
        try:
            for key, rec in seen.items():
                conn.execute(
                    "INSERT OR IGNORE INTO jobs (dedup_key, url, title, first_seen, "
                    "last_seen, runs_seen) VALUES (?, ?, ?, ?, ?, ?)",
                    (key, rec.get("url", ""), rec.get("title", ""),
                     rec.get("first_seen", ""), rec.get("last_seen", ""),
                     rec.get("runs_seen", 1)))
                imported += 1
            conn.commit()
        finally:
            conn.close()

    if jobs_path.exists():
        jobs = json.loads(jobs_path.read_text(encoding="utf-8"))
        conn = connect(db)
        try:
            for job in jobs:
                url = job.get("url") or ""
                if not url:
                    continue
                score = job.get("score")
                try:
                    score = float(score) if score is not None else None
                except (TypeError, ValueError):
                    score = None
                conn.execute(
                    """
                    INSERT INTO jobs (dedup_key, url, title, company, source, location,
                                      salary, score, verdict, data, first_seen, last_seen)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(dedup_key) DO UPDATE SET
                        score = excluded.score,
                        verdict = excluded.verdict,
                        data = excluded.data
                    """,
                    (dedup_key(url), url, job.get("title", ""), job.get("company", ""),
                     job.get("source", ""), job.get("location", ""), job.get("salary", ""),
                     score, job.get("verdict", ""), json.dumps(job), today, today))
                imported += 1
            conn.commit()
        finally:
            conn.close()

    if imported:
        log.info(f"  Imported {imported} record(s) from pre-SQLite seen.json/jobs.json")
    return imported > 0
