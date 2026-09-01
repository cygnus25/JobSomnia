import io
import json
from unittest.mock import patch
import pytest


def _fake_response(obj, trailing=""):
    """A urlopen()-like context manager returning `obj` serialized, plus any
    raw trailing bytes (the gateway sometimes appends SSE text)."""
    body = json.dumps(obj).encode() + trailing.encode()
    return io.BytesIO(body)


def test_run_llm_raises_without_api_key(tmp_path, monkeypatch):
    import jobscraper.llm as llm
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    (tmp_path / "prompt.md").write_text("hello")
    with pytest.raises(RuntimeError, match="LLM_API_KEY not set"):
        llm.run_llm("prompt.md")


def test_run_llm_posts_to_gateway(tmp_path, monkeypatch):
    import jobscraper.llm as llm
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    (tmp_path / "prompt.md").write_text("hello")
    resp = _fake_response({"choices": [{"message": {"content": "ok"}}]})
    with patch.object(llm.urllib.request, "urlopen", return_value=resp) as mock_open:
        out = llm.run_llm("prompt.md")
    assert out == "ok"
    req = mock_open.call_args[0][0]
    assert req.full_url.endswith("/chat/completions")
    assert req.get_header("Authorization") == "Bearer test-key"


def test_run_llm_parses_body_with_trailing_done(tmp_path, monkeypatch):
    """Gateway quirk: body is a JSON object followed by 'data: [DONE]'."""
    import jobscraper.llm as llm
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    (tmp_path / "prompt.md").write_text("hello")
    resp = _fake_response(
        {"choices": [{"message": {"content": "hello world"}}]},
        trailing="\ndata: [DONE]\n\n",
    )
    with patch.object(llm.urllib.request, "urlopen", return_value=resp):
        assert llm.run_llm("prompt.md") == "hello world"


def test_run_llm_raises_on_empty_content(tmp_path, monkeypatch):
    import jobscraper.llm as llm
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    (tmp_path / "prompt.md").write_text("hello")
    resp = _fake_response({"choices": [{"message": {"content": "   "}}]})
    with patch.object(llm.urllib.request, "urlopen", return_value=resp):
        with pytest.raises(RuntimeError, match="empty content"):
            llm.run_llm("prompt.md")


def test_run_llm_inlines_claude_md_and_resume(tmp_path, monkeypatch):
    import jobscraper.llm as llm
    monkeypatch.setattr(llm, "ROOT", tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    (tmp_path / "prompt.md").write_text("the prompt")
    (tmp_path / "CLAUDE.md").write_text("claude rules")
    (tmp_path / "resume.md").write_text("my resume")
    resp = _fake_response({"choices": [{"message": {"content": "ok"}}]})
    with patch.object(llm.urllib.request, "urlopen", return_value=resp) as mock_open:
        llm.run_llm("prompt.md")
    payload = json.loads(mock_open.call_args[0][0].data.decode())
    system = payload["messages"][0]["content"]
    assert "claude rules" in system and "my resume" in system
    assert payload["messages"][1]["content"] == "the prompt"


def test_run_claude_json_strips_markdown_fences():
    import jobscraper.llm as llm
    fenced = '```json\n{"target_roles": ["Virtual Assistant"]}\n```'
    with patch.object(llm, "run_llm", return_value=fenced):
        assert llm.run_claude_json("prompts/x.md") == {"target_roles": ["Virtual Assistant"]}


def test_run_claude_json_passes_plain_json_through():
    import jobscraper.llm as llm
    with patch.object(llm, "run_llm", return_value='[{"score": 88}]'):
        assert llm.run_claude_json("prompts/x.md") == [{"score": 88}]


def test_run_claude_json_extracts_json_from_surrounding_prose():
    import jobscraper.llm as llm
    chatty = (
        "I don't have write permission for `output/jobs.json`. Per the task "
        'instructions, here is the raw JSON array:\n\n[{"title": "Support Rep", '
        '"score": 72}]\n\nLet me know if you need anything else.'
    )
    with patch.object(llm, "run_llm", return_value=chatty):
        assert llm.run_claude_json("prompts/x.md") == [{"title": "Support Rep", "score": 72}]


def test_run_claude_json_still_fails_loudly_on_no_json():
    import jobscraper.llm as llm
    with patch.object(llm, "run_llm", return_value="Sorry, I cannot do that."):
        with pytest.raises(RuntimeError, match="invalid JSON"):
            llm.run_claude_json("prompts/x.md")
