# jobscraper/sjs.py — Student Job Search (sjs.co.nz) API source.
#
# SJS is a Next.js SPA whose data comes from an AWS API Gateway
# ("sjs-be-jobs-stack") fronting a private API: every request must be
# SigV4-signed with Cognito credentials. The site's identity pool allows
# guest access (allowGuestAccess: true — same config its own JS bundles
# ship), so an anonymous GetId + GetCredentialsForIdentity flow yields
# working credentials. No API key or login involved.
#
# Endpoints were read from the site's public JS bundles (config in
# /_next/static/chunks/app/layout-*.js, GET /jobs call in chunk 5277-*.js).
import json
import logging
import urllib.request
from urllib.parse import urlparse

from .sources_api import MAX_JOBS_PER_SOURCE, _strip_html

log = logging.getLogger(__name__)

REGION = "ap-southeast-1"
IDENTITY_POOL_ID = "ap-southeast-1:4e8d8204-4c69-41cc-95d9-e3c94024d462"
API_BASE = "https://8e0xo7eina.execute-api.ap-southeast-1.amazonaws.com/prod"
COGNITO_ENDPOINT = f"https://cognito-identity.{REGION}.amazonaws.com/"
JOB_URL = "https://www.sjs.co.nz/job-seeker/jobs/{uuid}"

TIMEOUT_S = 30


def _cognito(target: str, body: dict) -> dict:
    """Call one AWSCognitoIdentityService operation (JSON 1.1 protocol)."""
    req = urllib.request.Request(
        COGNITO_ENDPOINT,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/x-amz-json-1.1",
            "X-Amz-Target": "AWSCognitoIdentityService." + target,
        },
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def _guest_credentials() -> dict:
    """Anonymous Cognito credentials for the site's guest identity pool."""
    ident = _cognito("GetId", {"IdentityPoolId": IDENTITY_POOL_ID})
    creds = _cognito(
        "GetCredentialsForIdentity", {"IdentityId": ident["IdentityId"]}
    )["Credentials"]
    return {
        "access_key": creds["AccessKeyId"],
        "secret_key": creds["SecretKey"],
        "session_token": creds["SessionToken"],
    }


def _signed_get(url: str, creds: dict) -> list | dict:
    """SigV4-signed GET of `url` (execute-api scope, query string unsigned —
    GET /jobs takes none). Stdlib re-implementation of what aws-amplify's
    API.get(authMode: 'iam') does for the site."""
    import datetime
    import hashlib
    import hmac

    p = urlparse(url)
    amz_date = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    date_stamp = amz_date[:8]
    payload_hash = hashlib.sha256(b"").hexdigest()
    canonical_headers = (
        f"host:{p.netloc}\n"
        f"x-amz-date:{amz_date}\n"
        f"x-amz-security-token:{creds['session_token']}\n"
    )
    signed_headers = "host;x-amz-date;x-amz-security-token"
    canonical_request = (
        f"GET\n{p.path}\n\n{canonical_headers}\n{signed_headers}\n{payload_hash}"
    )
    scope = f"{date_stamp}/{REGION}/execute-api/aws4_request"
    string_to_sign = (
        f"AWS4-HMAC-SHA256\n{amz_date}\n{scope}\n"
        f"{hashlib.sha256(canonical_request.encode()).hexdigest()}"
    )

    def _h(key: bytes, msg: str) -> bytes:
        return hmac.new(key, msg.encode(), hashlib.sha256).digest()

    key = _h(("AWS4" + creds["secret_key"]).encode(), date_stamp)
    for part in (REGION, "execute-api", "aws4_request"):
        key = _h(key, part)
    signature = hmac.new(key, string_to_sign.encode(), hashlib.sha256).hexdigest()

    req = urllib.request.Request(url, headers={
        "Authorization": (
            f"AWS4-HMAC-SHA256 Credential={creds['access_key']}/{scope}, "
            f"SignedHeaders={signed_headers}, Signature={signature}"
        ),
        "x-amz-date": amz_date,
        "x-amz-security-token": creds["session_token"],
    })
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def _format_pay(job: dict) -> str:
    """'23.95'/'28.00'/'hourly' -> '$23.95-$28.00/hr'; '' when no real pay."""
    pmin, pmax = float(job.get("payMin") or 0), float(job.get("payMax") or 0)
    ptype = (job.get("payType") or "").strip().lower()
    if pmin <= 0 and pmax <= 0:
        return ""
    unit = {"hourly": "/hr", "annually": "/yr", "monthly": "/mo"}.get(ptype, "")
    money = lambda v: f"${v:,.0f}" if ptype in ("annually", "monthly") else f"${v:,.2f}"
    if pmin > 0 and pmax > 0 and pmin != pmax:
        return f"{money(pmin)}-{money(pmax)}{unit}"
    value = pmax if pmax > 0 else pmin
    return f"{money(value)}{unit}"


def _location(job: dict) -> str:
    area, region = (job.get("areaName") or "").strip(), (job.get("regionName") or "").strip()
    if area and area.lower() != region.lower():
        return f"{area}, {region}" if region else area
    return region or area or "New Zealand"


def _normalize(job: dict) -> dict | None:
    uuid = (job.get("uuid") or "").strip()
    title = (job.get("title") or "").strip()
    if not uuid or not title:
        return None
    description = _strip_html(job.get("description") or "")
    summary = (job.get("summary") or "").strip()
    return {
        "title": title,
        "company": (job.get("businessName") or "").strip(),
        "location": _location(job),
        "url": (job.get("externalApplyUrl") or job.get("applyUrl")
                or JOB_URL.format(uuid=uuid)),
        "description": summary + (" — " + description if description else ""),
        "posted_date": job.get("advertStartDateNz") or "",
        "source": "sjs.co.nz",
        "salary": _format_pay(job),
    }


def fetch_sjs_jobs() -> list[dict]:
    """Fetch live postings from SJS, newest first. Best-effort: any failure
    returns [] like the other API sources."""
    try:
        jobs = _signed_get(API_BASE + "/jobs", _guest_credentials())
        if not isinstance(jobs, list):
            log.info(f"  SJS API returned {type(jobs).__name__}, expected list")
            return []
        normalized = [j for j in (_normalize(raw) for raw in jobs) if j]
        # Feed order is jumbled — newest-first keeps the cap below meaningful.
        normalized.sort(key=lambda j: j["posted_date"], reverse=True)
        return normalized
    except Exception as e:
        log.info(f"  SJS API failed: {e}")
        return []


def fetch_sjs_capped() -> list[dict]:
    """fetch_sjs_jobs() trimmed to the shared per-source cap (fetch_api_jobs
    applies the same cap to the other sources; SJS pre-sorts so the cap
    keeps the newest)."""
    return fetch_sjs_jobs()[:MAX_JOBS_PER_SOURCE]
