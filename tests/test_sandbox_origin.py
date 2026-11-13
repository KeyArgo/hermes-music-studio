"""The framed studio is a sandboxed document with an opaque origin ("null").

Review item: `SandboxedFrame` gives the pane's page origin `null`, so every `/api` call it makes is
cross-origin: a write arrives with `Origin: null` and `Sec-Fetch-Site: cross-site`, and the old
middleware refused both. The API was unreachable from inside the desktop pane — the one place the
product is meant to live. A preflight also has to be answered, because `X-Studio-Token` is not a
simple header.

The rule these tests pin: **the token is the credential, the origin is not.** A request that carries
the token is served whatever document sent it, and the response carries the CORS headers the browser
needs to hand it back. A request without the token is still refused whatever origin it claims.
"""
import os
import sys
from pathlib import Path

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

TOKEN = "sandbox-token-123"


@pytest.fixture()
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("HEWN_STUDIO_DATA_DIR", str(tmp_path / "studio"))
    monkeypatch.setenv("HEWN_STUDIO_TOKEN", TOKEN)
    from standalone import app as host_app

    app = FastAPI()
    app.middleware("http")(host_app.same_origin)
    router = APIRouter()

    @router.get("/status")
    def status():
        return {"ok": True, "projects": ["a song"]}

    @router.post("/settings")
    def settings():
        return {"ok": True}

    app.include_router(router, prefix="/api")
    return TestClient(app, base_url="http://127.0.0.1:8775")


SANDBOX = {"origin": "null", "sec-fetch-site": "cross-site"}


def test_a_preflight_from_the_sandbox_is_answered_with_cors(client):
    answer = client.options(
        "/api/settings",
        headers={**SANDBOX,
                 "access-control-request-method": "POST",
                 "access-control-request-headers": "content-type,x-studio-token"},
    )
    assert answer.status_code == 204
    assert answer.headers["access-control-allow-origin"] == "null"
    assert "x-studio-token" in answer.headers["access-control-allow-headers"].lower()
    assert "POST" in answer.headers["access-control-allow-methods"]


def test_a_token_authorised_write_from_the_sandbox_is_accepted(client):
    """This is the blocker: Origin null + cross-site + POST used to be a 403 before the token counted."""
    answer = client.post("/api/settings", json={}, headers={**SANDBOX, "x-studio-token": TOKEN})
    assert answer.status_code == 200
    assert answer.headers["access-control-allow-origin"] == "null", (
        "the browser drops a cross-origin response without this header"
    )


def test_a_token_authorised_read_from_the_sandbox_is_readable(client):
    answer = client.get("/api/status", headers={**SANDBOX, "x-studio-token": TOKEN})
    assert answer.status_code == 200
    assert answer.json()["projects"] == ["a song"]
    assert answer.headers["access-control-allow-origin"] == "null"


def test_the_sandbox_gets_no_free_pass_without_the_token(client):
    assert client.get("/api/status", headers=SANDBOX).status_code == 401
    # A write without a token is refused by the origin check (403) before the token gate (401); either
    # way it is refused, which is the contract.
    assert client.post("/api/settings", json={}, headers=SANDBOX).status_code in (401, 403)
    assert client.post("/api/settings", json={},
                       headers={**SANDBOX, "x-studio-token": "wrong"}).status_code in (401, 403)


def test_a_page_on_a_stranger_origin_is_still_refused_when_it_has_no_token(client):
    """Opening the token path must not open the host to any web page the person happens to visit."""
    hostile = {"origin": "https://evil.example", "sec-fetch-site": "cross-site"}
    assert client.post("/api/settings", json={}, headers=hostile).status_code == 403
    refused = client.post("/api/settings", json={}, headers={**hostile, "x-studio-token": TOKEN})
    # With the token it is served, but no CORS header is handed to a stranger's origin.
    assert refused.headers.get("access-control-allow-origin") is None
