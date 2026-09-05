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
