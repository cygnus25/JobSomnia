import urllib.error
from unittest.mock import patch

import pytest

import jobscraper.notify as notify


@pytest.fixture(autouse=True)
def _clean_topic(monkeypatch):
    monkeypatch.delenv("NTFY_TOPIC", raising=False)


def _capturing_urlopen(calls):
    class _Resp:
        def read(self):
            return b""

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def fake_urlopen(req, timeout=None):
        calls.append({"url": req.full_url, "data": req.data,
                      "headers": dict(req.header_items())})
        return _Resp()

    return fake_urlopen


def test_send_posts_message_and_title_to_topic(monkeypatch):
    calls = []
    monkeypatch.setenv("NTFY_TOPIC", "jobs-test-topic")
    monkeypatch.setattr(notify.urllib.request, "urlopen", _capturing_urlopen(calls))

    notify.send("3 new jobs", title="JobScraper: run finished")

    assert calls[0]["url"] == "https://ntfy.sh/jobs-test-topic"
    assert calls[0]["data"] == b"3 new jobs"
    assert calls[0]["headers"].get("Title") == "JobScraper: run finished"


def test_send_omits_title_header_when_empty(monkeypatch):
    calls = []
    monkeypatch.setenv("NTFY_TOPIC", "jobs-test-topic")
    monkeypatch.setattr(notify.urllib.request, "urlopen", _capturing_urlopen(calls))

    notify.send("body only")

    assert "Title" not in calls[0]["headers"]


def test_send_raises_when_topic_unset():
    with pytest.raises(ValueError):
        notify.send("no topic configured")


def test_run_summary_includes_counts_and_top_job():
    result = {
        "new_count": 3,
        "above_threshold": 1,
        "top_jobs": [{"title": "Barista", "company": "Mojo", "score": 85,
                      "url": "https://example.com/j"}],
    }
    title, body = notify.run_summary(result, 70)

    assert title == "JobScraper: run finished"
    assert "3 new job(s)" in body
    assert "1 scored >= 70" in body
    assert "Top: Barista @ Mojo (85)" in body


def test_run_summary_without_top_jobs():
    title, body = notify.run_summary({"new_count": 0, "above_threshold": 0}, 70)

    assert title == "JobScraper: run finished"
    assert "0 new job(s)" in body
    assert "Top:" not in body


def test_notify_run_result_swallows_send_failure():
    with patch.object(notify, "send", side_effect=urllib.error.URLError("down")):
        notify.notify_run_result({"new_count": 1, "above_threshold": 0}, 70)


def test_notify_run_result_sends_summary(monkeypatch):
    calls = []
    monkeypatch.setenv("NTFY_TOPIC", "jobs-test-topic")
    monkeypatch.setattr(notify.urllib.request, "urlopen", _capturing_urlopen(calls))

    notify.notify_run_result(
        {"new_count": 2, "above_threshold": 1,
         "top_jobs": [{"title": "Chef", "company": "El Fuego", "score": 91}]},
        70,
    )

    assert len(calls) == 1
    assert "Top: Chef @ El Fuego (91)" in calls[0]["data"].decode()


def test_notify_run_failed_sends_error_and_swallows_failure():
    with patch.object(notify, "send") as mock_send:
        notify.notify_run_failed(RuntimeError("boom"))
    assert "boom" in mock_send.call_args.args[0]
    assert "FAILED" in mock_send.call_args.kwargs["title"]

    with patch.object(notify, "send", side_effect=urllib.error.URLError("down")):
        notify.notify_run_failed(RuntimeError("boom"))
