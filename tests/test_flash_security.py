"""Signed flash notices prevent attacker-controlled messages on real app pages."""
import time
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import pytest
from starlette.requests import Request

from app.deps import _notice_token, _verified_notice, back, render, templates


def _context(url: str):
    parts = urlsplit(url)
    scope = {
        "type": "http", "method": "GET", "scheme": "https",
        "path": parts.path or "/", "query_string": parts.query.encode(),
        "headers": [(b"host", b"example.test")], "server": ("example.test", 443),
        "client": ("127.0.0.1", 1234),
    }
    request = Request(scope)
    with patch.object(templates, "TemplateResponse", side_effect=lambda req, name, ctx, **kw: ctx):
        return render(request, "error.html")


def test_unsigned_url_success_and_error_messages_are_ignored():
    ctx = _context("/login?ok=Fake+account+verified&err=Pay+fraudster+now")
    assert ctx["flash"] == ""
    assert ctx["flash_error"] == ""


def test_legitimate_back_preserves_signed_notice_and_existing_query():
    response = back("/app/inquiries?tab=open", ok="Saved successfully")
    url = response.headers["location"]
    assert response.status_code == 303
    assert "tab=open&mf_notice=" in url
    assert _context(url)["flash"] == "Saved successfully"
    assert _context(url)["flash_error"] == ""


def test_signed_error_renders_only_on_its_own_side():
    ctx = _context(back("/app", err="Please correct the form.").headers["location"])
    assert ctx["flash_error"] == "Please correct the form."
    assert ctx["flash"] == ""


def test_modified_signature_cannot_forge_message():
    url = back("/app", ok="Saved").headers["location"]
    token = parse_qs(urlsplit(url).query)["mf_notice"][0]
    last = "0" if token[-1] != "0" else "1"
    ctx = _context("/app?mf_notice=" + token[:-1] + last)
    assert ctx["flash"] == ""
    assert ctx["flash_error"] == ""


def test_old_and_future_notices_are_rejected():
    assert _verified_notice(_notice_token("ok", "Expired", issued=int(time.time()) - 301)) == ("", "")
    assert _verified_notice(_notice_token("err", "Not issued", issued=int(time.time()) + 31)) == ("", "")


def test_malformed_and_unsigned_notices_do_not_render():
    for token in ("", "not-a-token", "a.b.c", "a.1234", "..", "x" * 2000):
        assert _verified_notice(token) == ("", "")


def test_redirect_destination_cannot_leave_the_platform():
    for destination in ("https://evil.invalid", "//evil.invalid", r"/\\evil.invalid"):
        with pytest.raises(ValueError):
            back(destination, ok="Saved")


def test_templates_escape_signed_but_hostile_text():
    text = "<script>alert(1)</script>"
    ctx = _context(back("/app", err=text).headers["location"])
    assert ctx["flash_error"] == text
    assert templates.env.from_string("{{flash_error}}").render(**ctx).startswith("&lt;script&gt;")


def test_auth_success_redirects_no_longer_use_unsigned_queries():
    from pathlib import Path
    source = (Path(__file__).resolve().parents[1] / "app" / "routers" / "auth.py").read_text()
    assert "/app?ok=" not in source
    assert "/login?ok=" not in source
    assert 'response = back("/app", ok="Welcome to your workspace")' in source
    assert "_set_cookie(response, session_token)" in source


def test_signed_notice_cannot_be_replayed_on_another_page():
    url = back("/login", ok="Password updated").headers["location"]
    token = parse_qs(urlsplit(url).query)["mf_notice"][0]
    assert _context("/app?mf_notice=" + token)["flash"] == ""
    assert _context("/login?mf_notice=" + token)["flash"] == "Password updated"


def test_fragments_and_existing_notice_parameters_preserve_new_message():
    url = back("/app/settings?tab=team&mf_notice=bogus#roles", ok="Updated").headers["location"]
    parts = urlsplit(url)
    params = parse_qs(parts.query)
    assert parts.fragment == "roles"
    assert params["tab"] == ["team"]
    assert len(params["mf_notice"]) == 1
    assert _context(url)["flash"] == "Updated"


def test_control_char_and_encoded_slash_redirects_are_refused():
    from app.routers.auth import _safe_next
    cases = ["/\\t/evil.example", "/\\r/evil.example", "/\\n/evil.example",
             "/%09/evil.example", "/%0d/evil.example", "/%5cevil.example",
             "/%2f/evil.example", "//evil.example"]
    cases = [value.replace("\\t", "\t").replace("\\r", "\r").replace("\\n", "\n")
             for value in cases]
    for bad in cases:
        assert _safe_next(bad) == "/app"
        with pytest.raises(ValueError):
            back(bad, ok="Saved")


def test_long_unicode_message_still_displays():
    message = "✓" * 200
    assert _context(back("/app", ok=message).headers["location"])["flash"] == message
