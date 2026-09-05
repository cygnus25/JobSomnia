import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
import pytest


def test_discover_pages_interleaves_results_across_queries():
    import jobscraper.scrape as scrape
    results = {
        "q1": [SimpleNamespace(url=f"https://a.com/{i}", title="", description="") for i in range(3)],
        "q2": [SimpleNamespace(url=f"https://b.com/{i}", title="", description="") for i in range(2)],
        "q3": [],
    }
    fake_app = SimpleNamespace(search=lambda q, limit: SimpleNamespace(web=results[q]))

    pages = scrape.discover_pages(fake_app, ["q1", "q2", "q3"])

    assert [p["url"] for p in pages] == [
        "https://a.com/0",
        "https://b.com/0",
        "https://a.com/1",
        "https://b.com/1",
        "https://a.com/2",
    ]


def test_discover_pages_dedupes_regional_subdomains():
    import jobscraper.scrape as scrape
    results = {
        "q1": [SimpleNamespace(url="https://www.indeed.com/viewjob?jk=abc", title="", description="")],
        "q2": [
            SimpleNamespace(url="https://in.indeed.com/viewjob?jk=abc", title="", description=""),
            SimpleNamespace(url="https://uk.linkedin.com/jobs/view/123", title="", description=""),
        ],
        "q3": [SimpleNamespace(url="https://www.linkedin.com/jobs/view/123", title="", description="")],
    }
    fake_app = SimpleNamespace(search=lambda q, limit: SimpleNamespace(web=results[q]))

    pages = scrape.discover_pages(fake_app, ["q1", "q2", "q3"])

    assert [p["url"] for p in pages] == [
        "https://www.indeed.com/viewjob?jk=abc",
        "https://uk.linkedin.com/jobs/view/123",
    ]


def test_canonical_host_leaves_regular_domains_alone():
    import jobscraper.scrape as scrape
    assert scrape._canonical_host("www.indeed.com") == "indeed.com"
    assert scrape._canonical_host("ng.indeed.com") == "indeed.com"
    assert scrape._canonical_host("ph.jobstreet.com") == "jobstreet.com"
    assert scrape._canonical_host("onlinejobs.ph") == "onlinejobs.ph"
    assert scrape._canonical_host("glassdoor.co.uk") == "glassdoor.co.uk"
    assert scrape._canonical_host("reddit.com") == "reddit.com"


def test_extract_postings_skips_scrape_for_unscrapable_hosts():
    import jobscraper.scrape as scrape
    page = {"url": "https://www.linkedin.com/jobs/view/123", "title": "Dev role", "description": "desc"}
    fake_app = SimpleNamespace(scrape=Mock())

    postings = scrape.extract_postings(fake_app, page)

    fake_app.scrape.assert_not_called()
    assert postings == [{
        "title": "Dev role",
        "company": "",
        "location": "Remote",
        "url": "https://www.linkedin.com/jobs/view/123",
        "description": "desc",
        "posted_date": "",
        "source": "linkedin.com",
    }]


def test_scrape_jobs_drops_cap_zero_hosts(monkeypatch):
    import jobscraper.scrape as scrape
    pages = [
        {"url": "https://www.linkedin.com/jobs/view/1", "title": "LI Job", "description": ""},
        {"url": "https://indeed.com/viewjob?jk=1", "title": "Indeed Job", "description": ""},
    ]
    monkeypatch.setattr(scrape, "discover_pages", lambda app, queries: pages)
    monkeypatch.setattr(scrape, "load_config", lambda: {"scrape_caps": {"linkedin.com": 0}})
    fake_app = SimpleNamespace(scrape=Mock(return_value=SimpleNamespace(json={"jobs": []})))
    monkeypatch.setattr(scrape, "FirecrawlApp", lambda api_key: fake_app)
    monkeypatch.setenv("FIRECRAWL_API_KEY", "test-key")

    jobs = scrape.scrape_jobs(["q"])

    urls = [j["url"] for j in jobs]
    assert not any("linkedin.com" in u for u in urls)
    assert any("indeed.com" in u for u in urls)
    # linkedin.com is dropped before ever reaching extract_postings, so the
    # only scrape() call made is for the surviving indeed.com page.
    assert fake_app.scrape.call_count == 1


def test_scrape_jobs_enforces_per_host_cap(monkeypatch):
    import jobscraper.scrape as scrape
    pages = [
        {"url": f"https://indeed.com/viewjob?jk={i}", "title": f"Job {i}", "description": ""}
        for i in range(8)
    ]
    monkeypatch.setattr(scrape, "discover_pages", lambda app, queries: pages)
    monkeypatch.setattr(scrape, "load_config", lambda: {"scrape_caps": {"indeed.com": 5}})
    fake_app = SimpleNamespace(scrape=Mock(return_value=SimpleNamespace(json={"jobs": []})))
    monkeypatch.setattr(scrape, "FirecrawlApp", lambda api_key: fake_app)
    monkeypatch.setenv("FIRECRAWL_API_KEY", "test-key")

    jobs = scrape.scrape_jobs(["q"])

    assert fake_app.scrape.call_count == 5
    assert len(jobs) == 5


def test_run_pipeline_emits_all_step_events(tmp_path, monkeypatch):
    import jobscraper.pipeline as pipeline
    monkeypatch.setattr(pipeline, "ROOT", tmp_path)
    (tmp_path / "resume.md").write_text("# Resume")
    (tmp_path / "output").mkdir()

    mock_config = {"search_queries": ["frontend dev remote"]}
    mock_raw_jobs = [{"title": "Dev", "company": "Co", "location": "Remote",
                      "url": "https://example.com", "description": "",
                      "posted_date": "", "source": "example.com"}]
    mock_analyzed = [{"title": "Dev", "company": "Co", "url": "https://example.com",
                      "score": 85, "verdict": "apply", "match_reasons": [],
                      "red_flags": [], "suggested_angle": ""}]

    events = []

    with patch.object(pipeline, "build_search_config", return_value=mock_config), \
         patch.object(pipeline, "scrape_jobs", return_value=mock_raw_jobs), \
         patch.object(pipeline, "fetch_api_jobs", return_value=[]), \
         patch.object(pipeline, "analyze_jobs", return_value=mock_analyzed):
        result = pipeline.run_pipeline(on_progress=lambda s, l, st: events.append((s, st)))

    step_statuses = {(s, st) for s, st in events}
    assert (1, "running") in step_statuses
    assert (1, "done") in step_statuses
    assert (2, "running") in step_statuses
    assert (2, "done") in step_statuses
    assert (3, "running") in step_statuses
    assert (3, "done") in step_statuses
    assert result["total"] == 1
    assert result["above_threshold"] == 1
    assert result["new_count"] == 1
    assert result["new_above_threshold"] == 1
    assert Path(result["run_dir"]).is_dir()


def test_run_pipeline_raises_when_resume_missing(tmp_path, monkeypatch):
    import jobscraper.pipeline as pipeline
    monkeypatch.setattr(pipeline, "ROOT", tmp_path)
    with pytest.raises(RuntimeError, match="Missing resume.md"):
        pipeline.run_pipeline()


def test_run_pipeline_works_without_callback(tmp_path, monkeypatch):
    import jobscraper.pipeline as pipeline
    monkeypatch.setattr(pipeline, "ROOT", tmp_path)
    (tmp_path / "resume.md").write_text("# Resume")
    (tmp_path / "output").mkdir()

    mock_config = {"search_queries": ["q"]}
    mock_raw_jobs = [{"title": "Dev", "company": "Co", "location": "Remote",
                      "url": "https://example.com", "description": "",
                      "posted_date": "", "source": "example.com"}]
    mock_analyzed = [{"title": "Dev", "url": "https://example.com", "score": 50,
                      "verdict": "skip", "match_reasons": [], "red_flags": [],
                      "suggested_angle": ""}]

    with patch.object(pipeline, "build_search_config", return_value=mock_config), \
         patch.object(pipeline, "scrape_jobs", return_value=mock_raw_jobs), \
         patch.object(pipeline, "fetch_api_jobs", return_value=[]), \
         patch.object(pipeline, "analyze_jobs", return_value=mock_analyzed):
        result = pipeline.run_pipeline()

    assert result["total"] == 1
    assert result["above_threshold"] == 0


def test_run_pipeline_writes_run_history(tmp_path, monkeypatch):
    import jobscraper.pipeline as pipeline
    monkeypatch.setattr(pipeline, "ROOT", tmp_path)
    (tmp_path / "resume.md").write_text("# Resume")
    (tmp_path / "output").mkdir()

    mock_config = {"search_queries": ["q"]}
    mock_raw_jobs = [{"title": "Dev", "company": "Co", "location": "Remote",
                      "url": "https://example.com", "description": "",
                      "posted_date": "", "source": "example.com"}]
    mock_analyzed = [{"title": "Dev", "url": "https://example.com", "score": 85,
                      "verdict": "apply", "match_reasons": [], "red_flags": [],
                      "suggested_angle": ""}]

    with patch.object(pipeline, "build_search_config", return_value=mock_config), \
         patch.object(pipeline, "scrape_jobs", return_value=mock_raw_jobs), \
         patch.object(pipeline, "fetch_api_jobs", return_value=[]), \
         patch.object(pipeline, "analyze_jobs", return_value=mock_analyzed):
        result = pipeline.run_pipeline()

    run_dir = Path(result["run_dir"])
    assert run_dir.is_dir()
    assert run_dir.parent == tmp_path / "output" / "runs"
    assert json.loads((run_dir / "search_config.json").read_text()) == mock_config
    assert json.loads((run_dir / "raw_jobs.json").read_text()) == mock_raw_jobs
    assert json.loads((run_dir / "jobs.json").read_text()) == mock_analyzed


def test_run_pipeline_second_run_scores_zero_when_all_seen(tmp_path, monkeypatch):
    import jobscraper.llm as llm
    import jobscraper.pipeline as pipeline
    monkeypatch.setattr(pipeline, "ROOT", tmp_path)
    (tmp_path / "resume.md").write_text("# Resume")
    (tmp_path / "output").mkdir()
    (tmp_path / "prompts").mkdir()
    (tmp_path / "prompts/analyze.md").write_text("analyze")

    mock_config = {"search_queries": ["q"]}
    mock_raw_jobs = [{"title": "Dev", "company": "Co", "location": "Remote",
                      "url": "https://example.com", "description": "",
                      "posted_date": "", "source": "example.com"}]

    with patch.object(pipeline, "build_search_config", return_value=mock_config), \
         patch.object(pipeline, "scrape_jobs", return_value=mock_raw_jobs), \
         patch.object(pipeline, "fetch_api_jobs", return_value=[]), \
         patch.object(llm, "run_llm", return_value=json.dumps([{"title": "Dev", "score": 90}])) as mock_run_llm:
        first = pipeline.run_pipeline()
        second = pipeline.run_pipeline()

    assert first["new_count"] == 1
    assert second["new_count"] == 0
    assert second["total"] == 0
    assert second["above_threshold"] == 0
    assert mock_run_llm.call_count == 1  # the second run never calls the LLM to score anything
    # output/jobs.json must still hold the job scored during the first run —
    # a no-new-jobs run must not blank out the dashboard.
    assert json.loads((tmp_path / "output/jobs.json").read_text()) == [{"title": "Dev", "score": 90}]


def test_run_pipeline_accumulates_jobs_across_runs(tmp_path, monkeypatch):
    import jobscraper.llm as llm
    import jobscraper.pipeline as pipeline
    monkeypatch.setattr(pipeline, "ROOT", tmp_path)
    (tmp_path / "resume.md").write_text("# Resume")
    (tmp_path / "output").mkdir()
    (tmp_path / "prompts").mkdir()
    (tmp_path / "prompts/analyze.md").write_text("analyze")

    mock_config = {"search_queries": ["q"]}
    job1 = {"title": "Dev 1", "company": "Co", "location": "Remote",
            "url": "https://example.com/1", "description": "",
            "posted_date": "", "source": "example.com"}
    job2 = {"title": "Dev 2", "company": "Co", "location": "Remote",
            "url": "https://example.com/2", "description": "",
            "posted_date": "", "source": "example.com"}
    scored1 = {"title": "Dev 1", "url": "https://example.com/1", "score": 90}
    scored2 = {"title": "Dev 2", "url": "https://example.com/2", "score": 80}

    with patch.object(pipeline, "build_search_config", return_value=mock_config), \
         patch.object(pipeline, "scrape_jobs", side_effect=[[job1], [job2]]), \
         patch.object(pipeline, "fetch_api_jobs", return_value=[]), \
         patch.object(llm, "run_llm", side_effect=[json.dumps([scored1]), json.dumps([scored2])]):
        first = pipeline.run_pipeline()   # job1 is new
        second = pipeline.run_pipeline()  # job1 no longer scraped, job2 is new

    assert first["new_count"] == 1
    assert second["new_count"] == 1  # only job2

    jobs_json = json.loads((tmp_path / "output/jobs.json").read_text())
    assert scored1 in jobs_json
    assert scored2 in jobs_json
    assert len(jobs_json) == 2


def test_run_pipeline_merges_history_into_jobs_json(tmp_path, monkeypatch):
    """A job scored in a previous run that this run's scrape doesn't turn up
    again must survive in output/jobs.json as history, alongside this run's
    freshly-scored job — while the per-run snapshot stays this-run-only."""
    import jobscraper.pipeline as pipeline
    monkeypatch.setattr(pipeline, "ROOT", tmp_path)
    (tmp_path / "resume.md").write_text("# Resume")
    (tmp_path / "output").mkdir()

    old_job = {"title": "Old Dev", "url": "https://example.com/old", "score": 75,
               "verdict": "apply", "match_reasons": [], "red_flags": [], "suggested_angle": ""}
    (tmp_path / "output/jobs.json").write_text(json.dumps([old_job]))

    mock_config = {"search_queries": ["q"]}
    mock_raw_jobs = [{"title": "New Dev", "company": "Co", "location": "Remote",
                      "url": "https://example.com/new", "description": "",
                      "posted_date": "", "source": "example.com"}]
    new_scored = {"title": "New Dev", "url": "https://example.com/new", "score": 88,
                  "verdict": "apply", "match_reasons": [], "red_flags": [], "suggested_angle": ""}

    with patch.object(pipeline, "build_search_config", return_value=mock_config), \
         patch.object(pipeline, "scrape_jobs", return_value=mock_raw_jobs), \
         patch.object(pipeline, "fetch_api_jobs", return_value=[]), \
         patch.object(pipeline, "analyze_jobs", return_value=[new_scored]):
        result = pipeline.run_pipeline()

    jobs_json = json.loads((tmp_path / "output/jobs.json").read_text())
    assert old_job in jobs_json
    assert new_scored in jobs_json
    assert len(jobs_json) == 2

    # The per-run snapshot only ever reflects this run's newly-scored jobs.
    run_dir_jobs = json.loads((Path(result["run_dir"]) / "jobs.json").read_text())
    assert run_dir_jobs == [new_scored]


def test_run_pipeline_merges_api_jobs_with_scraped_jobs(tmp_path, monkeypatch):
    import jobscraper.pipeline as pipeline
    monkeypatch.setattr(pipeline, "ROOT", tmp_path)
    (tmp_path / "resume.md").write_text("# Resume")
    (tmp_path / "output").mkdir()

    mock_config = {"search_queries": ["q"]}
    scraped_job = {"title": "Scraped Dev", "company": "Co", "location": "Remote",
                   "url": "https://example.com/scraped", "description": "",
                   "posted_date": "", "source": "example.com"}
    api_job = {"title": "API Dev", "company": "ApiCo", "location": "Remote",
               "url": "https://remotive.com/remote-jobs/api-dev", "description": "",
               "posted_date": "", "source": "remotive.com"}

    with patch.object(pipeline, "build_search_config", return_value=mock_config), \
         patch.object(pipeline, "scrape_jobs", return_value=[scraped_job]), \
         patch.object(pipeline, "fetch_api_jobs", return_value=[api_job]), \
         patch.object(pipeline, "analyze_jobs", return_value=[]) as mock_analyze:
        pipeline.run_pipeline()

    # Merged into output/raw_jobs.json (and the per-run snapshot) exactly
    # like a scraped job.
    raw_jobs = json.loads((tmp_path / "output/raw_jobs.json").read_text())
    assert scraped_job in raw_jobs
    assert api_job in raw_jobs

    # And merged before the seen.json partition, so it's sent to the LLM
    # like any other newly-discovered job.
    sent_to_llm = mock_analyze.call_args[0][0]
    assert scraped_job in sent_to_llm
    assert api_job in sent_to_llm


def test_run_pipeline_only_sends_new_jobs_to_llm(tmp_path, monkeypatch):
    import jobscraper.llm as llm
    import jobscraper.pipeline as pipeline
    monkeypatch.setattr(pipeline, "ROOT", tmp_path)
    (tmp_path / "resume.md").write_text("# Resume")
    (tmp_path / "output").mkdir()
    (tmp_path / "prompts").mkdir()
    (tmp_path / "prompts/analyze.md").write_text("analyze")

    mock_config = {"search_queries": ["q"]}
    seen_job = {"title": "Old", "company": "Co", "location": "Remote",
                "url": "https://example.com/old", "description": "",
                "posted_date": "", "source": "example.com"}
    new_job = {"title": "New", "company": "Co", "location": "Remote",
               "url": "https://example.com/new", "description": "",
               "posted_date": "", "source": "example.com"}

    # Seed output/seen.json so `seen_job` is already known from a prior run.
    seen_record = {
        pipeline.dedup_key(seen_job["url"]): {
            "url": seen_job["url"], "title": seen_job["title"],
            "first_seen": "2024-01-01", "last_seen": "2024-01-01", "runs_seen": 1,
        }
    }
    (tmp_path / "output/seen.json").write_text(json.dumps(seen_record))

    with patch.object(pipeline, "build_search_config", return_value=mock_config), \
         patch.object(pipeline, "scrape_jobs", return_value=[seen_job, new_job]), \
         patch.object(pipeline, "fetch_api_jobs", return_value=[]), \
         patch.object(llm, "run_llm", return_value="[]") as mock_run_llm:
        result = pipeline.run_pipeline()

    assert result["new_count"] == 1
    mock_run_llm.assert_called_once()
    context = mock_run_llm.call_args[0][1]
    assert "https://example.com/new" in context
    assert "https://example.com/old" not in context


def test_run_pipeline_includes_raw_total_and_top_jobs(tmp_path, monkeypatch):
    import jobscraper.pipeline as pipeline
    monkeypatch.setattr(pipeline, "ROOT", tmp_path)
    (tmp_path / "resume.md").write_text("# Resume")
    (tmp_path / "output").mkdir()

    mock_config = {"search_queries": ["q"]}
    mock_raw_jobs = [
        {"title": "Dev A", "company": "Co", "location": "Remote",
         "url": "https://example.com/a", "description": "", "posted_date": "", "source": "example.com"},
        {"title": "Dev B", "company": "Co", "location": "Remote",
         "url": "https://example.com/b", "description": "", "posted_date": "", "source": "example.com"},
    ]
    mock_analyzed = [
        {"title": "Dev A", "company": "Co", "url": "https://example.com/a", "score": 60,
         "verdict": "maybe", "match_reasons": [], "red_flags": [], "suggested_angle": ""},
        {"title": "Dev B", "company": "Co", "url": "https://example.com/b", "score": 95,
         "verdict": "apply", "match_reasons": [], "red_flags": [], "suggested_angle": ""},
    ]

    with patch.object(pipeline, "build_search_config", return_value=mock_config), \
         patch.object(pipeline, "scrape_jobs", return_value=mock_raw_jobs), \
         patch.object(pipeline, "fetch_api_jobs", return_value=[]), \
         patch.object(pipeline, "analyze_jobs", return_value=mock_analyzed):
        result = pipeline.run_pipeline()

    assert result["raw_total"] == 2
    # Sorted by score descending, highest first.
    assert [j["url"] for j in result["top_jobs"]] == [
        "https://example.com/b", "https://example.com/a",
    ]
    assert result["top_jobs"][0] == {
        "title": "Dev B", "company": "Co", "score": 95, "url": "https://example.com/b",
    }


def test_run_pipeline_top_jobs_capped_at_five(tmp_path, monkeypatch):
    import jobscraper.pipeline as pipeline
    monkeypatch.setattr(pipeline, "ROOT", tmp_path)
    (tmp_path / "resume.md").write_text("# Resume")
    (tmp_path / "output").mkdir()

    mock_config = {"search_queries": ["q"]}
    mock_raw_jobs = [
        {"title": f"Dev {i}", "company": "Co", "location": "Remote",
         "url": f"https://example.com/{i}", "description": "", "posted_date": "", "source": "example.com"}
        for i in range(7)
    ]
    mock_analyzed = [
        {"title": f"Dev {i}", "company": "Co", "url": f"https://example.com/{i}", "score": i * 10,
         "verdict": "apply", "match_reasons": [], "red_flags": [], "suggested_angle": ""}
        for i in range(7)
    ]

    with patch.object(pipeline, "build_search_config", return_value=mock_config), \
         patch.object(pipeline, "scrape_jobs", return_value=mock_raw_jobs), \
         patch.object(pipeline, "fetch_api_jobs", return_value=[]), \
         patch.object(pipeline, "analyze_jobs", return_value=mock_analyzed):
        result = pipeline.run_pipeline()

    assert len(result["top_jobs"]) == 5
    assert [j["score"] for j in result["top_jobs"]] == [60, 50, 40, 30, 20]


def test_load_config_returns_defaults_without_file(tmp_path, monkeypatch):
    import jobscraper.config as config
    monkeypatch.setattr(config, "ROOT", tmp_path)
    cfg = config.load_config()
    assert "linkedin.com/jobs" in cfg["job_boards"]
    assert any(g["name"] == "Community" for g in cfg["reddit_groups"])
    assert cfg["api_sources"] == ["remotive.com", "remoteok.com", "arbeitnow.com", "sjs.co.nz", "greenhouse", "lever", "adzuna"]


def test_load_config_overrides_from_file(tmp_path, monkeypatch):
    import jobscraper.config as config
    monkeypatch.setattr(config, "ROOT", tmp_path)
    (tmp_path / "config.json").write_text(json.dumps({"job_boards": ["remoteok.com"]}))
    cfg = config.load_config()
    assert cfg["job_boards"] == ["remoteok.com"]
    assert cfg["reddit_groups"] == config.DEFAULT_CONFIG["reddit_groups"]


def test_sources_context_lists_boards_and_groups():
    import jobscraper.config as config
    ctx = config.sources_context({
        "job_boards": ["remoteok.com", "weworkremotely.com"],
        "reddit_groups": [
            {"name": "Dev", "subreddits": ["webdev", "cscareers"], "extra_terms": "hiring"},
            {"name": "Gigs", "subreddits": ["freelance"]},
        ],
    })
    assert "- remoteok.com" in ctx
    assert "- weworkremotely.com" in ctx
    assert "Dev: r/webdev, r/cscareers" in ctx
    assert '"hiring"' in ctx
    assert "Gigs: r/freelance" in ctx


def test_analyze_jobs_writes_jobs_json(tmp_path, monkeypatch):
    import jobscraper.pipeline as pipeline
    import jobscraper.llm as llm
    monkeypatch.setattr(pipeline, "ROOT", tmp_path)
    (tmp_path / "output").mkdir()
    (tmp_path / "prompts").mkdir()
    (tmp_path / "prompts/analyze.md").write_text("analyze")
    raw_jobs = [{"title": "Dev", "url": "https://example.com"}]
    (tmp_path / "output/raw_jobs.json").write_text(json.dumps(raw_jobs))

    analyzed = [{"title": "Dev", "score": 85, "verdict": "apply"}]
    with patch.object(llm, "run_llm", return_value=json.dumps(analyzed)):
        result = pipeline.analyze_jobs()

    assert result == analyzed
    assert json.loads((tmp_path / "output" / "jobs.json").read_text()) == analyzed


def test_analyze_jobs_sends_raw_jobs_as_context(tmp_path, monkeypatch):
    import jobscraper.pipeline as pipeline
    import jobscraper.llm as llm
    monkeypatch.setattr(pipeline, "ROOT", tmp_path)
    (tmp_path / "output").mkdir()
    (tmp_path / "prompts").mkdir()
    (tmp_path / "prompts/analyze.md").write_text("analyze")
    raw_jobs = [{"title": "Dev", "url": "https://example.com"}]
    (tmp_path / "output/raw_jobs.json").write_text(json.dumps(raw_jobs))

    with patch.object(llm, "run_llm", return_value="[]") as mock_run_llm:
        pipeline.analyze_jobs()

    context = mock_run_llm.call_args[0][1]
    assert "https://example.com" in context


def test_analyze_jobs_batches_in_groups_of_10(tmp_path, monkeypatch):
    import jobscraper.pipeline as pipeline
    import jobscraper.llm as llm
    monkeypatch.setattr(pipeline, "ROOT", tmp_path)
    (tmp_path / "output").mkdir()
    (tmp_path / "prompts").mkdir()
    (tmp_path / "prompts/analyze.md").write_text("analyze")
    raw_jobs = [{"title": f"Dev {i}", "url": f"https://example.com/{i}"} for i in range(25)]
    (tmp_path / "output/raw_jobs.json").write_text(json.dumps(raw_jobs))

    def fake_run_llm(prompt_file, context=""):
        # Echo back one scored job per job title found in this batch's context.
        n = context.count("https://example.com/")
        return json.dumps([{"title": "Dev", "score": 90} for _ in range(n)])

    with patch.object(llm, "run_llm", side_effect=fake_run_llm) as mock_run_llm:
        result = pipeline.analyze_jobs()

    assert mock_run_llm.call_count == 3  # 10 + 10 + 5
    assert len(result) == 25
