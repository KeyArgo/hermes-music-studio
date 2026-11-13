"""Shared test setup for the studio host.

Review item 3 made the token gate closed by default: a host without `HEWN_STUDIO_TOKEN` refuses every
`/api` route instead of serving it to any local process. Tests that exercise the API therefore need a
token and a client that carries it — `studio_client()` — rather than each file inventing one.

The environment variable is set for every test here, so a fixture that wants to prove the fail-closed
behaviour must delete it explicitly (`monkeypatch.delenv('HEWN_STUDIO_TOKEN')`).
"""
import os

import pytest

TEST_TOKEN = 'test-token-abc123'


@pytest.fixture(autouse=True)
def _studio_token_in_the_environment(monkeypatch):
    monkeypatch.setenv('HEWN_STUDIO_TOKEN', TEST_TOKEN)


def studio_client(app, base_url='http://127.0.0.1'):
    """A TestClient for the studio app that carries the token the host requires."""
    from fastapi.testclient import TestClient

    client = TestClient(app, base_url=base_url)
    client.headers['X-Studio-Token'] = os.environ.get('HEWN_STUDIO_TOKEN') or TEST_TOKEN
    return client
