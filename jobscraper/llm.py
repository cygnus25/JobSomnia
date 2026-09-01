# jobscraper/llm.py
"""LLM calls over HTTP via the 9Router gateway (stdlib urllib only).

Replaces the old Claude Code CLI subprocess: the gateway speaks an
OpenAI-compatible /chat/completions API, so the prompt file plus optional
inline context go as the user message, and CLAUDE.md + resume.md — which the
CLI used to auto-read via its Read tool — are inlined as the system message.
"""
import json
import logging
import os
import re
import urllib.error
import urllib.request
from pathlib import Path

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[1]

DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_MODEL = "gpt-4o-mini"
TIMEOUT_S = 300


def _system_context() -> str:
    """CLAUDE.md + resume.md inlined as the system message (the HTTP call has
    no Read tool). ROOT-anchored; missing files are skipped."""
    parts = []
    for name in ("CLAUDE.md", "resume.md"):
        path = ROOT / name
        if path.exists():
            parts.append(f"===== {name} =====\n{path.read_text(encoding='utf-8')}")
    return "\n\n".join(parts)


def run_llm(prompt_file: str, context: str = "") -> str:
    """POST the prompt file (+ appended context) to the gateway and return the
    assistant text. Raises RuntimeError on non-2xx or empty content."""
    base_url = os.environ.get("LLM_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
    model = os.environ.get("LLM_MODEL", DEFAULT_MODEL)
    api_key = os.environ.get("LLM_API_KEY", "")
    if not api_key:
        raise RuntimeError(
            "LLM_API_KEY not set — set your own key in .env "
            "(see .env.example for OpenAI-compatible options)."
        )

    prompt = Path(prompt_file).read_text(encoding="utf-8")
    if context:
        prompt = prompt + "\n" + context

    payload = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": _system_context()},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": 8192,
    }).encode("utf-8")

    req = urllib.request.Request(
        f"{base_url}/chat/completions",
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            body = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        log.error(f"LLM error {e.code}: {detail}")
        raise RuntimeError(f"LLM request failed: HTTP {e.code}") from e
    except OSError as e:
        raise RuntimeError(f"LLM request failed: {e}") from e

    # Gateway quirk (verified live): the body can be a JSON object followed by
    # trailing SSE text ("...}\ndata: [DONE]\n\n"); json.loads fails on it.
    try:
        data, _ = json.JSONDecoder().raw_decode(body.lstrip())
        content = (data["choices"][0]["message"]["content"] or "").strip()
    except (ValueError, KeyError, IndexError, TypeError) as e:
        raise RuntimeError(f"LLM returned unparseable response: {e}\n---\n{body[:300]}") from e
    if not content:
        raise RuntimeError(f"LLM returned empty content for {prompt_file}")
    return content


# Back-compat alias — the pipeline and tests still know this name.
run_claude = run_llm


def _parse_json_output(raw: str):
    """Parse the model's output as JSON, tolerating markdown fences and prose
    around the JSON payload (models sometimes add them despite instructions)."""
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-zA-Z]*\s*\n", "", raw)
        raw = re.sub(r"\n```\s*$", "", raw)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    # Fall back to the first parseable JSON value embedded in the text.
    decoder = json.JSONDecoder()
    for i, ch in enumerate(raw):
        if ch in "[{":
            try:
                value, _ = decoder.raw_decode(raw, i)
                return value
            except json.JSONDecodeError:
                continue
    raise json.JSONDecodeError("no JSON value found in output", raw, 0)


def run_claude_json(prompt_file: str, context: str = ""):
    """Run a prompt that must return JSON, and parse it."""
    raw = run_llm(prompt_file, context)
    if not raw:
        raise RuntimeError(f"LLM returned empty output for {prompt_file}")
    try:
        return _parse_json_output(raw)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"LLM returned invalid JSON for {prompt_file}: {e}\n---\n{raw[:300]}")
