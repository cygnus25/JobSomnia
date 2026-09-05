"""Tests for the Student Job Search (sjs.co.nz) API source.

The Cognito guest flow + SigV4 signing run over urllib, so tests patch
jobscraper.sjs.urllib.request.urlopen with a side_effect that inspects each
request: Cognito calls are identified by their X-Amz-Target header, the
API Gateway GET is everything else.
"""
import io
import json
from unittest.mock import patch

import pytest

import jobscraper.sjs as sjs


class _FakeResp:
    """urlopen()-like context manager returning pre-encoded bytes."""

    def __init__(self, payload):
        self.data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()

    def read(self):
        return self.data

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _sjs_job(**overrides):
    job = {
        "uuid": "uuid-1",
        "jobId": "job-1",
        "title": "Checkout Operator",
        "businessName": "Supermarket Ltd",
        "areaName": "Auckland CBD",
        "regionName": "Auckland City",
        "payMin": "23.95",
        "payMax": "28.00",
        "payType": "hourly",
        "summary": "Serve customers.",
        "description": "<p>Full training given.</p>",
        "advertStartDateNz": "2026-09-05T00:00:00.000Z",
        "externalApplyUrl": None,
        "applyUrl": None,
    }
    job.update(overrides)
    return job


def _fake_urlopen(jobs_payload):
    """side_effect for urlopen: answer the two Cognito ops, then the API GET."""
    def handler(req, timeout=None):
        target = next(
            (v for k, v in req.header_items() if k.lower() == "x-amz-target"), "")
        if target.endswith("GetId"):
            return _FakeResp({"IdentityId": "ap-southeast-1:test-id"})
        if target.endswith("GetCredentialsForIdentity"):
            return _FakeResp({"Credentials": {
                "AccessKeyId": "AKIDEXAMPLE", "SecretKey": "secret",
                "SessionToken": "token", "Expiration": 9999999999.0,
            }})
        assert "execute-api" in req.host, "unexpected third request"
        return _FakeResp(jobs_payload)
    return handler


def test_fetch_sjs_jobs_normalizes_and_sorts():
    payload = [
        _sjs_job(uuid="old", advertStartDateNz="2026-08-01T00:00:00.000Z"),
        _sjs_job(uuid="new", advertStartDateNz="2026-09-05T00:00:00.000Z"),
    ]
    with patch.object(sjs.urllib.request, "urlopen", side_effect=_fake_urlopen(payload)):
        jobs = sjs.fetch_sjs_jobs()

    assert [j["url"] for j in jobs] == [
        "https://www.sjs.co.nz/job-seeker/jobs/new",
        "https://www.sjs.co.nz/job-seeker/jobs/old",
    ]
    top = jobs[0]
    assert top == {
        "title": "Checkout Operator",
        "company": "Supermarket Ltd",
        "location": "Auckland CBD, Auckland City",
        "url": "https://www.sjs.co.nz/job-seeker/jobs/new",
        "description": "Serve customers. — Full training given.",
        "posted_date": "2026-09-05T00:00:00.000Z",
        "source": "sjs.co.nz",
        "salary": "$23.95-$28.00/hr",
    }


def test_fetch_sjs_jobs_prefers_external_apply_url():
    payload = [_sjs_job(externalApplyUrl="https://employer.example/apply")]
    with patch.object(sjs.urllib.request, "urlopen", side_effect=_fake_urlopen(payload)):
        jobs = sjs.fetch_sjs_jobs()
    assert jobs[0]["url"] == "https://employer.example/apply"


def test_fetch_sjs_jobs_skips_entries_without_uuid_or_title():
    payload = [_sjs_job(uuid=""), _sjs_job(title=""), _sjs_job()]
    with patch.object(sjs.urllib.request, "urlopen", side_effect=_fake_urlopen(payload)):
        jobs = sjs.fetch_sjs_jobs()
    assert len(jobs) == 1


def test_fetch_sjs_jobs_returns_empty_on_failure():
    with patch.object(sjs.urllib.request, "urlopen", side_effect=OSError("boom")):
        assert sjs.fetch_sjs_jobs() == []


def test_fetch_sjs_jobs_returns_empty_on_non_list_payload():
    with patch.object(sjs.urllib.request, "urlopen",
                      side_effect=_fake_urlopen({"error": "nope"})):
        assert sjs.fetch_sjs_jobs() == []


@pytest.mark.parametrize("pmin,pmax,ptype,expected", [
    ("23.95", "28.00", "hourly", "$23.95-$28.00/hr"),
    ("24.00", "24.00", "hourly", "$24.00/hr"),
    ("28.00", None, "hourly", "$28.00/hr"),
    ("60000", "80000", "annually", "$60,000-$80,000/yr"),
    ("0.00", "0.00", "hourly", ""),          # SJS encodes "no pay shown" as 0
    (None, None, "voluntary", ""),
])
def test_format_pay(pmin, pmax, ptype, expected):
    assert sjs._format_pay({"payMin": pmin, "payMax": pmax, "payType": ptype}) == expected


def test_location_prefers_area_but_collapses_duplicates():
    assert sjs._location({"areaName": "Auckland CBD", "regionName": "Auckland City"}) \
        == "Auckland CBD, Auckland City"
    assert sjs._location({"areaName": "Dunedin", "regionName": "Dunedin"}) == "Dunedin"
    assert sjs._location({"areaName": "", "regionName": ""}) == "New Zealand"
