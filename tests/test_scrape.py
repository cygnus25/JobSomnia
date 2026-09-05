import json
from types import SimpleNamespace

import pytest

import jobscraper.scrape as scrape
from firecrawl.v2.utils.error_handler import FirecrawlError


class _Resp:
    def __init__(self, status_code, headers=None):
        self.status_code = status_code
        self.headers = headers or {}


def _err(status, retry_after=None):
    headers = {"Retry-After": str(retry_after)} if retry_after is not None else {}
    return FirecrawlError(f"HTTP {status}", status_code=status,
                          response=_Resp(status, headers))


class _App:
    """Minimal FirecrawlApp stand-in: raises queued errors in order, then
    returns a doc containing `postings`."""

    def __init__(self, postings=(), errors=()):
        self.errors = list(errors)
        self.postings = list(postings)
        self.calls = 0

    def scrape(self, url, **kwargs):
        self.calls += 1
        if self.errors:
            raise self.errors.pop(0)
        return SimpleNamespace(json={"jobs": list(self.postings)})


PAGE = {"url": "https://jobs.example.co.nz/listing", "title": "Listing",
        "description": "A listing page"}
SNIPPET = [{
    "title": "Listing",
    "company": "",
    "location": "Remote",
    "url": "https://jobs.example.co.nz/listing",
    "description": "A listing page",
    "posted_date": "",
    "source": "jobs.example.co.nz",
}]


@pytest.fixture(autouse=True)
def _isolated_failed_hosts(tmp_path, monkeypatch):
    monkeypatch.setattr(scrape, "FAILED_HOSTS_FILE", tmp_path / "failed_hosts.json")


def _failures_file():
    if not scrape.FAILED_HOSTS_FILE.exists():
        return {}
    return json.loads(scrape.FAILED_HOSTS_FILE.read_text(encoding="utf-8"))


def test_402_aborts_run_instead_of_degrading():
    app = _App(errors=[_err(402)])

    with pytest.raises(RuntimeError, match="credits exhausted"):
        scrape.extract_postings(app, PAGE)

    assert app.calls == 1
    # The run is aborting; the host itself isn't at fault — no demotion.
    assert _failures_file() == {}


def test_429_sleeps_retry_after_then_retries_once(monkeypatch):
    slept = []
    monkeypatch.setattr(scrape.time, "sleep", lambda s: slept.append(s))
    app = _App(postings=[{"title": "Real Job", "url": "https://jobs.example.co.nz/j/1"}],
               errors=[_err(429, retry_after=9)])

    jobs = scrape.extract_postings(app, PAGE)

    assert app.calls == 2
    assert slept == [9.0]
    assert jobs[0]["title"] == "Real Job"
    assert _failures_file() == {}


def test_429_without_retry_after_uses_default(monkeypatch):
    slept = []
    monkeypatch.setattr(scrape.time, "sleep", lambda s: slept.append(s))
    app = _App(errors=[_err(429)])

    jobs = scrape.extract_postings(app, PAGE)

    assert slept == [scrape.RETRY_AFTER_DEFAULT_S]
    assert app.calls == 2
    assert jobs == SNIPPET  # retry succeeded but the page yielded no postings


def test_429_retry_clamps_huge_retry_after(monkeypatch):
    slept = []
    monkeypatch.setattr(scrape.time, "sleep", lambda s: slept.append(s))
    app = _App(errors=[_err(429, retry_after=9999)])

    scrape.extract_postings(app, PAGE)

    assert slept == [scrape.RETRY_AFTER_MAX_S]


def test_429_retry_failing_with_402_still_aborts(monkeypatch):
    monkeypatch.setattr(scrape.time, "sleep", lambda s: None)
    app = _App(errors=[_err(429), _err(402)])

    with pytest.raises(RuntimeError, match="credits exhausted"):
        scrape.extract_postings(app, PAGE)


def test_429_retry_failing_generic_records_host(monkeypatch):
    monkeypatch.setattr(scrape.time, "sleep", lambda s: None)
    app = _App(errors=[_err(429), _err(500)])

    assert scrape.extract_postings(app, PAGE) == SNIPPET
    assert _failures_file()["jobs.example.co.nz"]["consecutive_failures"] == 1


def test_generic_failure_falls_back_and_records_host():
    app = _App(errors=[_err(500)])

    assert scrape.extract_postings(app, PAGE) == SNIPPET
    record = _failures_file()["jobs.example.co.nz"]
    assert record["consecutive_failures"] == 1
    assert record["last_failure"]


def test_message_only_exception_still_classified_by_status_regex():
    assert scrape._firecrawl_status(Exception("Request failed with HTTP 429")) == 429
    assert scrape._firecrawl_status(Exception("weird error")) is None


def test_host_demoted_after_threshold_skips_scrape():
    scrape.FAILED_HOSTS_FILE.write_text(json.dumps(
        {"jobs.example.co.nz": {"consecutive_failures": 3, "last_failure": "2026-09-06"}}))
    app = _App()

    assert scrape.extract_postings(app, PAGE) == SNIPPET
    assert app.calls == 0


def test_successful_scrape_clears_failure_record():
    scrape.FAILED_HOSTS_FILE.write_text(json.dumps(
        {"jobs.example.co.nz": {"consecutive_failures": 2, "last_failure": "2026-09-05"}}))
    app = _App(postings=[{"title": "Real Job", "url": "https://jobs.example.co.nz/j/1"}])

    jobs = scrape.extract_postings(app, PAGE)

    assert jobs[0]["title"] == "Real Job"
    assert _failures_file() == {}
