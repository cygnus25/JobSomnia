import io
import json
from unittest.mock import patch


def _fake_response(obj):
    """A urlopen()-like context manager returning `obj` serialized as JSON
    (mirrors tests/test_llm.py's _fake_response)."""
    return io.BytesIO(json.dumps(obj).encode())


def test_fetch_remotive_parses_jobs():
    import jobscraper.sources_api as sources_api
    payload = {
        "jobs": [
            {
                "title": "Senior Backend Engineer",
                "company_name": "Acme Corp",
                "candidate_required_location": "Worldwide",
                "url": "https://remotive.com/remote-jobs/senior-backend-engineer-123",
                "description": "<p>We need a backend engineer.</p>",
                "publication_date": "2024-05-01T12:00:00",
            }
        ]
    }
    with patch.object(sources_api.urllib.request, "urlopen", return_value=_fake_response(payload)):
        jobs = sources_api.fetch_remotive()

    assert jobs == [{
        "title": "Senior Backend Engineer",
        "company": "Acme Corp",
        "location": "Worldwide",
        "url": "https://remotive.com/remote-jobs/senior-backend-engineer-123",
        "description": "<p>We need a backend engineer.</p>",
        "posted_date": "2024-05-01T12:00:00",
        "source": "remotive.com",
        "salary": "",
    }]


def test_fetch_remoteok_skips_legal_notice_and_strips_html():
    import jobscraper.sources_api as sources_api
    payload = [
        {"legal": "By using this API you agree to our terms.", "warning": "..."},
        {
            "position": "Frontend Developer",
            "company": "Beta LLC",
            "location": "Anywhere",
            "url": "https://remoteok.com/remote-jobs/frontend-developer-456",
            "date": "2024-05-02T08:00:00+00:00",
            "description": "<p>Great backend role.</p><br><p>Remote-first team.</p>",
        },
    ]
    with patch.object(sources_api.urllib.request, "urlopen", return_value=_fake_response(payload)):
        jobs = sources_api.fetch_remoteok()

    assert jobs == [{
        "title": "Frontend Developer",
        "company": "Beta LLC",
        "location": "Anywhere",
        "url": "https://remoteok.com/remote-jobs/frontend-developer-456",
        "description": "Great backend role. Remote-first team.",
        "posted_date": "2024-05-02T08:00:00+00:00",
        "source": "remoteok.com",
        "salary": "",
    }]


def test_fetch_arbeitnow_parses_jobs():
    import jobscraper.sources_api as sources_api
    payload = {
        "data": [
            {
                "title": "DevOps Engineer",
                "company_name": "Gamma Inc",
                "location": "Remote",
                "url": "https://www.arbeitnow.com/view/devops-engineer-789",
                "description": "Looking for a DevOps engineer to join our remote team.",
                "created_at": 1714608000,
            }
        ]
    }
    with patch.object(sources_api.urllib.request, "urlopen", return_value=_fake_response(payload)):
        jobs = sources_api.fetch_arbeitnow()

    assert jobs == [{
        "title": "DevOps Engineer",
        "company": "Gamma Inc",
        "location": "Remote",
        "url": "https://www.arbeitnow.com/view/devops-engineer-789",
        "description": "Looking for a DevOps engineer to join our remote team.",
        "posted_date": "1714608000",
        "source": "arbeitnow.com",
    }]


def test_fetch_remotive_returns_empty_list_on_failure():
    import jobscraper.sources_api as sources_api
    with patch.object(sources_api.urllib.request, "urlopen", side_effect=OSError("boom")):
        assert sources_api.fetch_remotive() == []


def test_fetch_remoteok_returns_empty_list_on_failure():
    import jobscraper.sources_api as sources_api
    with patch.object(sources_api.urllib.request, "urlopen", side_effect=OSError("boom")):
        assert sources_api.fetch_remoteok() == []


def test_fetch_arbeitnow_returns_empty_list_on_failure():
    import jobscraper.sources_api as sources_api
    with patch.object(sources_api.urllib.request, "urlopen", side_effect=OSError("boom")):
        assert sources_api.fetch_arbeitnow() == []


def test_fetch_arbeitnow_returns_empty_list_on_malformed_json():
    import jobscraper.sources_api as sources_api
    bad_response = io.BytesIO(b"not json")
    with patch.object(sources_api.urllib.request, "urlopen", return_value=bad_response):
        assert sources_api.fetch_arbeitnow() == []


def test_fetch_api_jobs_disabled_by_empty_api_sources():
    import jobscraper.sources_api as sources_api
    with patch.object(sources_api, "load_config", return_value={"api_sources": []}), \
         patch.object(sources_api, "fetch_remotive") as mock_remotive, \
         patch.object(sources_api, "fetch_remoteok") as mock_remoteok, \
         patch.object(sources_api, "fetch_arbeitnow") as mock_arbeitnow:
        result = sources_api.fetch_api_jobs()

    assert result == []
    mock_remotive.assert_not_called()
    mock_remoteok.assert_not_called()
    mock_arbeitnow.assert_not_called()


def test_fetch_api_jobs_queries_every_configured_source():
    import jobscraper.sources_api as sources_api
    cfg = {"api_sources": ["remotive.com", "remoteok.com", "arbeitnow.com"]}
    with patch.object(sources_api, "load_config", return_value=cfg), \
         patch.object(sources_api, "fetch_remotive", return_value=[{"url": "a"}]), \
         patch.object(sources_api, "fetch_remoteok", return_value=[{"url": "b"}]), \
         patch.object(sources_api, "fetch_arbeitnow", return_value=[{"url": "c"}]):
        result = sources_api.fetch_api_jobs()

    assert result == [{"url": "a"}, {"url": "b"}, {"url": "c"}]


def test_fetch_api_jobs_caps_each_source_at_50():
    import jobscraper.sources_api as sources_api
    many_jobs = [{"url": f"https://remotive.com/{i}"} for i in range(75)]
    with patch.object(sources_api, "load_config", return_value={"api_sources": ["remotive.com"]}), \
         patch.object(sources_api, "fetch_remotive", return_value=many_jobs):
        result = sources_api.fetch_api_jobs()

    assert len(result) == 50
    assert result == many_jobs[:50]


def test_fetch_greenhouse_unescapes_content_and_prefers_company_name():
    import jobscraper.sources_api as sources_api
    payload = {"jobs": [{
        "title": "Propulsion Engineer",
        "company_name": "Rocket Lab",
        "absolute_url": "https://job-boards.greenhouse.io/rocketlab/jobs/1",
        "location": {"name": "Auckland, NZ"},
        "content": "&lt;p&gt;Build &amp; launch rockets&lt;/p&gt;",
        "first_published": "2026-08-20T17:55:10-04:00",
        "updated_at": "2026-09-01T10:00:00-04:00",
    }]}
    with patch.object(sources_api, "load_config",
                      return_value={"ats_boards": {"greenhouse": ["rocketlab"]}}), \
         patch.object(sources_api.urllib.request, "urlopen",
                      return_value=_fake_response(payload)):
        jobs = sources_api.fetch_greenhouse()

    assert jobs == [{
        "title": "Propulsion Engineer",
        "company": "Rocket Lab",
        "location": "Auckland, NZ",
        "url": "https://job-boards.greenhouse.io/rocketlab/jobs/1",
        "description": "Build & launch rockets",
        "posted_date": "2026-08-20T17:55:10-04:00",
        "source": "greenhouse",
        "salary": "",
    }]


def test_fetch_greenhouse_falls_back_to_token_and_updated_at():
    import jobscraper.sources_api as sources_api
    payload = {"jobs": [{
        "title": "Technician",
        "absolute_url": "https://job-boards.greenhouse.io/acme/jobs/2",
        "location": None,
        "content": "",
        "updated_at": "2026-09-02T10:00:00Z",
    }]}
    with patch.object(sources_api, "load_config",
                      return_value={"ats_boards": {"greenhouse": ["acme"]}}), \
         patch.object(sources_api.urllib.request, "urlopen",
                      return_value=_fake_response(payload)):
        jobs = sources_api.fetch_greenhouse()

    assert jobs[0]["company"] == "acme"
    assert jobs[0]["location"] == ""
    assert jobs[0]["posted_date"] == "2026-09-02T10:00:00Z"


def test_fetch_greenhouse_sorts_newest_first_and_caps_per_board():
    import jobscraper.sources_api as sources_api
    payload = {"jobs": [
        {"title": "Old", "absolute_url": "https://g.io/j/1",
         "first_published": "2026-01-01T00:00:00Z"},
        {"title": "New", "absolute_url": "https://g.io/j/2",
         "first_published": "2026-09-01T00:00:00Z"},
    ]}
    with patch.object(sources_api, "load_config",
                      return_value={"ats_boards": {"greenhouse": ["acme"]}}), \
         patch.object(sources_api.urllib.request, "urlopen",
                      return_value=_fake_response(payload)):
        jobs = sources_api.fetch_greenhouse()

    assert [j["title"] for j in jobs] == ["New", "Old"]


def test_fetch_greenhouse_survives_one_dead_board():
    import jobscraper.sources_api as sources_api
    payload = {"jobs": [{"title": "OK", "absolute_url": "https://g.io/j/1"}]}
    responses = iter([OSError("dead board"), _fake_response(payload)])
    with patch.object(sources_api, "load_config",
                      return_value={"ats_boards": {"greenhouse": ["dead", "alive"]}}), \
         patch.object(sources_api.urllib.request, "urlopen",
                      side_effect=lambda req, timeout=None: next(responses)):
        jobs = sources_api.fetch_greenhouse()

    assert [j["title"] for j in jobs] == ["OK"]


def test_fetch_lever_parses_postings_and_epoch_dates():
    import jobscraper.sources_api as sources_api
    from datetime import datetime, timezone
    created_ms = "1786319437378"
    expected_date = datetime.fromtimestamp(
        int(created_ms) / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
    payload = [{
        "text": "Business Assurance Manager",
        "hostedUrl": "https://jobs.lever.co/nzte/abc-123",
        "applyUrl": "https://jobs.lever.co/nzte/abc-123/apply",
        "createdAt": created_ms,
        "categories": {"location": "Auckland, Wellington", "commitment": "Full Time"},
        "descriptionBodyPlain": "This role can be based in our Wellington office.",
        "descriptionPlain": "",
    }]
    with patch.object(sources_api, "load_config",
                      return_value={"ats_boards": {"lever": ["nzte"]}}), \
         patch.object(sources_api.urllib.request, "urlopen",
                      return_value=_fake_response(payload)):
        jobs = sources_api.fetch_lever()

    assert jobs == [{
        "title": "Business Assurance Manager",
        "company": "nzte",
        "location": "Auckland, Wellington",
        "url": "https://jobs.lever.co/nzte/abc-123",
        "description": "This role can be based in our Wellington office.",
        "posted_date": expected_date,
        "source": "lever",
        "salary": "",
    }]


def test_fetch_lever_sorts_newest_first():
    import jobscraper.sources_api as sources_api
    payload = [
        {"text": "Old", "hostedUrl": "https://l.co/a", "createdAt": "1000000000000"},
        {"text": "New", "hostedUrl": "https://l.co/b", "createdAt": "1700000000000"},
    ]
    with patch.object(sources_api, "load_config",
                      return_value={"ats_boards": {"lever": ["acme"]}}), \
         patch.object(sources_api.urllib.request, "urlopen",
                      return_value=_fake_response(payload)):
        jobs = sources_api.fetch_lever()

    assert [j["title"] for j in jobs] == ["New", "Old"]


def test_fetch_lever_interleaves_boards():
    import jobscraper.sources_api as sources_api

    def board_response(urls):
        return _fake_response([
            {"text": f"{u}-title", "hostedUrl": u, "createdAt": str(i)}
            for i, u in enumerate(urls)
        ])

    responses = iter([board_response(["https://l.co/a1", "https://l.co/a2"]),
                      board_response(["https://l.co/b1", "https://l.co/b2"])])
    with patch.object(sources_api, "load_config",
                      return_value={"ats_boards": {"lever": ["one", "two"]}}), \
         patch.object(sources_api.urllib.request, "urlopen",
                      side_effect=lambda req, timeout=None: next(responses)):
        jobs = sources_api.fetch_lever()

    assert [j["url"] for j in jobs] == [
        "https://l.co/a1", "https://l.co/b1", "https://l.co/a2", "https://l.co/b2"]


def test_fetch_api_jobs_dispatches_to_ats_fetchers():
    import jobscraper.sources_api as sources_api
    cfg = {"api_sources": ["greenhouse", "lever"]}
    with patch.object(sources_api, "load_config", return_value=cfg), \
         patch.object(sources_api, "fetch_greenhouse", return_value=[{"url": "g"}]) as mock_gh, \
         patch.object(sources_api, "fetch_lever", return_value=[{"url": "l"}]) as mock_lv:
        result = sources_api.fetch_api_jobs()

    assert result == [{"url": "g"}, {"url": "l"}]
    mock_gh.assert_called_once()
    mock_lv.assert_called_once()


def test_fetch_adzuna_parses_postings_and_marks_predicted_salaries():
    import jobscraper.sources_api as sources_api
    payload = {"results": [
        {
            "title": "Sous Chef",
            "company": {"display_name": "Minimal Bar"},
            "location": {"display_name": "Auckland Central"},
            "redirect_url": "https://adzuna.co.nz/r/1",
            "description": "Busy Ponsonby kitchen seeks a sous chef.",
            "created": "2026-09-01T12:00:00Z",
            "salary_min": 55000.0,
            "salary_max": 65000.0,
            "salary_is_predicted": 0,
        },
        {
            "title": "Barista",
            "company": {"display_name": "Corner Coffee"},
            "location": {"display_name": "Wellington"},
            "redirect_url": "https://adzuna.co.nz/r/2",
            "description": "Weekend barista wanted.",
            "created": "2026-09-02T12:00:00Z",
            "salary_min": 48000.0,
            "salary_max": 52000.0,
            "salary_is_predicted": 1,
        },
    ]}
    calls = []

    def fake_get_json(url):
        calls.append(url)
        return payload

    with patch.object(sources_api, "load_config", return_value={}), \
         patch.dict("os.environ", {"ADZUNA_APP_ID": "id1", "ADZUNA_APP_KEY": "key1"}), \
         patch.object(sources_api, "_get_json", side_effect=fake_get_json):
        jobs = sources_api.fetch_adzuna()

    assert "app_id=id1" in calls[0] and "app_key=key1" in calls[0]
    assert "jobs/nz/search/1" in calls[0]
    assert jobs == [
        {
            "title": "Sous Chef",
            "company": "Minimal Bar",
            "location": "Auckland Central",
            "url": "https://adzuna.co.nz/r/1",
            "description": "Busy Ponsonby kitchen seeks a sous chef.",
            "posted_date": "2026-09-01T12:00:00Z",
            "source": "adzuna",
            "salary": "$55,000-$65,000",
        },
        {
            "title": "Barista",
            "company": "Corner Coffee",
            "location": "Wellington",
            "url": "https://adzuna.co.nz/r/2",
            "description": "Weekend barista wanted.",
            "posted_date": "2026-09-02T12:00:00Z",
            "source": "adzuna",
            "salary": "$48,000-$52,000 (est.)",
        },
    ]


def test_fetch_adzuna_returns_empty_list_without_keys(monkeypatch):
    import jobscraper.sources_api as sources_api
    monkeypatch.delenv("ADZUNA_APP_ID", raising=False)
    monkeypatch.delenv("ADZUNA_APP_KEY", raising=False)
    with patch.object(sources_api, "_get_json") as mock_get:
        assert sources_api.fetch_adzuna() == []
    mock_get.assert_not_called()


def test_fetch_adzuna_returns_empty_list_on_failure(monkeypatch):
    import jobscraper.sources_api as sources_api
    monkeypatch.setenv("ADZUNA_APP_ID", "id1")
    monkeypatch.setenv("ADZUNA_APP_KEY", "key1")
    with patch.object(sources_api, "_get_json", side_effect=OSError("boom")):
        assert sources_api.fetch_adzuna() == []
