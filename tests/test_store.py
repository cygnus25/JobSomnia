import json

import pytest

import jobscraper.store as store
from jobscraper.scrape import dedup_key


@pytest.fixture
def db(tmp_path):
    return tmp_path / "jobs.db"


def _job(url, title="Dev", company="Co"):
    return {"title": title, "company": company, "location": "Remote", "url": url,
            "description": "", "posted_date": "", "source": "example.com"}


def test_record_scores_marks_omitted_inputs_as_attempted(db):
    """analyze.md only returns jobs scoring >= threshold — a batch input
    the scorer omitted counts as attempted (seen by the LLM in a completed
    batch) and must not be re-billed on every run."""
    run_id = store.start_run("2024-01-01", db=db)
    kept = _job("https://example.com/kept")
    omitted = _job("https://example.com/omitted")
    store.record_seen(run_id, [kept, omitted], "2024-01-01", db=db)

    store.record_scores(run_id, [{"title": "Dev", "url": kept["url"], "score": 85}],
                        "2024-01-01", db=db, batch_inputs=[kept, omitted])

    to_score, already = store.partition_new_jobs([kept, omitted], db=db)
    assert (to_score, already) == ([], [kept, omitted])
    assert store.load_scored_jobs(db=db) == [
        {"title": "Dev", "url": kept["url"], "score": 85}]  # only the returned result


def test_record_seen_makes_job_scoreable_then_scored(db):
    run_id = store.start_run("2024-01-01 08:00:00", db=db)
    job = _job("https://example.com/1")
    store.record_seen(run_id, [job], "2024-01-01", db=db)

    # Seen but unscored — the crash-recovery case: it must still be scored.
    assert store.partition_new_jobs([job], db=db) == ([job], [])

    store.record_scores(run_id, [{"title": "Dev", "url": job["url"], "score": 80}],
                        "2024-01-01", db=db)
    to_score, already = store.partition_new_jobs([job], db=db)
    assert (to_score, already) == ([], [job])


def test_record_seen_bumps_runs_seen_but_keeps_first_seen(db):
    job = _job("https://example.com/1")
    store.record_seen(store.start_run("2024-01-01", db=db), [job], "2024-01-01", db=db)
    store.record_seen(store.start_run("2024-01-02", db=db), [job], "2024-01-02", db=db)

    rec = store.load_seen(db=db)[dedup_key(job["url"])]
    assert rec["runs_seen"] == 2
    assert rec["first_seen"] == "2024-01-01"
    assert rec["last_seen"] == "2024-01-02"
    assert rec["url"] == job["url"]


def test_record_seen_skips_url_less_entries(db):
    store.record_seen(store.start_run("2024-01-01", db=db),
                      [{"title": "no url", "url": ""}], "2024-01-01", db=db)
    assert store.load_seen(db=db) == {}


def test_record_scores_skips_url_less_entries(db):
    run_id = store.start_run("2024-01-01", db=db)
    store.record_scores(run_id, [{"title": "malformed", "score": 9}], "2024-01-01", db=db)
    assert store.load_scored_jobs(db=db) == []


def test_load_scored_jobs_filters_by_run(db):
    run1 = store.start_run("2024-01-01", db=db)
    run2 = store.start_run("2024-01-02", db=db)
    scored1 = {"title": "Old", "url": "https://example.com/1", "score": 70}
    scored2 = {"title": "New", "url": "https://example.com/2", "score": 90}
    store.record_scores(run1, [scored1], "2024-01-01", db=db)
    store.record_scores(run2, [scored2], "2024-01-02", db=db)

    assert store.load_scored_jobs(db=db) == [scored1, scored2]  # oldest first
    assert store.load_scored_jobs(db=db, run_id=run2) == [scored2]


def test_load_scored_jobs_preserves_full_llm_output(db):
    run_id = store.start_run("2024-01-01", db=db)
    scored = {"title": "Dev", "url": "https://example.com/1", "score": 80,
              "verdict": "apply", "salary": "$60,000-$80,000", "location": "Auckland",
              "match_reasons": ["react"], "red_flags": [], "suggested_angle": "x"}
    store.record_scores(run_id, [scored], "2024-01-01", db=db)

    assert store.load_scored_jobs(db=db) == [scored]


def test_start_run_fails_orphaned_running_runs(db):
    orphan = store.start_run("2024-01-01 08:00:00", db=db)  # never finished (killed run)
    store.start_run("2024-01-02 08:00:00", db=db)

    runs = store.run_history(db=db)
    assert [r["status"] for r in runs] == ["running", "failed"]  # newest first
    assert runs[1]["id"] == orphan
    assert runs[1]["finished_at"] == "2024-01-02 08:00:00"


def test_run_history_roundtrip(db):
    ok_run = store.start_run("2024-01-01 08:00:00", db=db)
    store.finish_run(ok_run, "ok", raw_total=5, new_count=2, above_threshold=1,
                     finished_at="2024-01-01 08:03:00", db=db)
    store.start_run("2024-01-02 08:00:00", db=db)  # still 'running' (crashed run)

    runs = store.run_history(db=db)
    assert [r["status"] for r in runs] == ["running", "ok"]  # newest first
    assert runs[1]["raw_total"] == 5
    assert runs[1]["new_count"] == 2
    assert runs[1]["above_threshold"] == 1
    assert runs[1]["finished_at"] == "2024-01-01 08:03:00"


def test_bootstrap_imports_seen_and_scored_jobs(db, tmp_path):
    seen_path = tmp_path / "seen.json"
    jobs_path = tmp_path / "jobs.json"
    url = "https://example.com/1"
    seen_path.write_text(json.dumps({
        dedup_key(url): {"url": url, "title": "Dev", "first_seen": "2024-01-01",
                         "last_seen": "2024-01-05", "runs_seen": 3}}))
    scored = {"title": "Dev", "url": url, "score": 88, "verdict": "apply"}
    jobs_path.write_text(json.dumps([scored]))

    assert store.bootstrap_from_json(seen_path, jobs_path, "2026-09-06", db=db) is True
    assert store.bootstrap_from_json(seen_path, jobs_path, "2026-09-06", db=db) is False

    rec = store.load_seen(db=db)[dedup_key(url)]
    assert rec["runs_seen"] == 3 and rec["last_seen"] == "2024-01-05"
    assert store.load_scored_jobs(db=db) == [scored]
    # Imported scores count as scored: the job won't be re-billed.
    to_score, already = store.partition_new_jobs([_job(url)], db=db)
    assert (to_score, already) == ([], [_job(url)])


def test_bootstrap_noop_when_db_already_has_jobs(db, tmp_path):
    store.record_seen(store.start_run("2024-01-01", db=db),
                      [_job("https://example.com/x")], "2024-01-01", db=db)
    seen_path = tmp_path / "seen.json"
    seen_path.write_text(json.dumps({}))

    assert store.bootstrap_from_json(seen_path, tmp_path / "missing.json",
                                     "2026-09-06", db=db) is False


def test_ignored_url_mismatch_keeps_api_salary_on_rescrape(db):
    """A re-scrape without salary data (e.g. the API source was offline)
    must not blank out a salary recorded by an earlier run."""
    job = _job("https://example.com/1")
    store.record_seen(store.start_run("2024-01-01", db=db),
                      [{**job, "salary": "$23.95-$28.00/hr"}], "2024-01-01", db=db)
    store.record_seen(store.start_run("2024-01-02", db=db), [job], "2024-01-02", db=db)

    row = store.connect(db).execute(
        "SELECT salary FROM jobs WHERE dedup_key = ?", (dedup_key(job["url"]),)).fetchone()
    assert row["salary"] == "$23.95-$28.00/hr"
