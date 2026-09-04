import json
from types import SimpleNamespace
from unittest.mock import patch
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
         patch.object(pipeline, "analyze_jobs", return_value=mock_analyzed):
        result = pipeline.run_pipeline(on_progress=lambda s, l, st: events.append((s, st)))

    step_statuses = {(s, st) for s, st in events}
    assert (1, "running") in step_statuses
    assert (1, "done") in step_statuses
    assert (2, "running") in step_statuses
    assert (2, "done") in step_statuses
    assert (3, "running") in step_statuses
    assert (3, "done") in step_statuses
    assert result == {"total": 1, "above_threshold": 1}


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
         patch.object(pipeline, "analyze_jobs", return_value=mock_analyzed):
        result = pipeline.run_pipeline()

    assert result["total"] == 1
    assert result["above_threshold"] == 0


def test_load_config_returns_defaults_without_file(tmp_path, monkeypatch):
    import jobscraper.config as config
    monkeypatch.setattr(config, "ROOT", tmp_path)
    cfg = config.load_config()
    assert "linkedin.com/jobs" in cfg["job_boards"]
    assert any(g["name"] == "Community" for g in cfg["reddit_groups"])


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
