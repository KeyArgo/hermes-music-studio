"""The model picker reads only the public provider registry, and never a secret.

The brief's catalog rules are hard: a plugin may use ``providers.list_providers()`` and may check
whether an environment variable *name* is present, but it must never read a value, a base URL, a
config file or another provider's key. These tests hold the line: a fake ``providers`` module, a
guarded ``os.environ`` that raises on value reads, and a route that still answers on the standalone
host where ``providers`` does not exist.
"""

from __future__ import annotations

import collections.abc
import json
import os
import sys
import types

import pytest

from music_studio import model_choices


class FakeProfile:
    """The shape of ``providers.base.ProviderProfile`` that the picker is allowed to read."""

    def __init__(
        self,
        name: str,
        *,
        display_name: str = "",
        hidden: bool = False,
        fallback_models: tuple = (),
        supports_model_listing: bool = False,
        fetched: list | None = None,
        fetch_error: Exception | None = None,
        env_vars: tuple = (),
        auth_type: str = "api_key",
        base_url: str = "",
        models_url: str = "",
    ):
        self.name = name
        self.display_name = display_name
        self.hidden = hidden
        self.fallback_models = fallback_models
        self.supports_model_listing = supports_model_listing
        self.env_vars = env_vars
        self.auth_type = auth_type
        self.base_url = base_url
        self.models_url = models_url
        self._fetched = fetched
        self._fetch_error = fetch_error
        self.fetch_calls: list[dict] = []

    def fetch_models(self, **kwargs):
        self.fetch_calls.append(kwargs)
        if self._fetch_error is not None:
            raise self._fetch_error
        return self._fetched


class GuardedEnviron(collections.abc.MutableMapping):
    """An ``os.environ`` look-alike that raises if a guarded value is read.

    Membership (``in``) works normally, so ``configured`` can be answered without touching the
    secret. ``__getitem__`` and ``get`` raise for the guarded names, which is exactly what a
    careless implementation would call.
    """

    def __init__(self, real, guarded):
        self._real = real
        self._guarded = set(guarded)

    def __getitem__(self, key):
        if key in self._guarded:
            raise AssertionError(f"read the value of {key}; membership only is allowed")
        return self._real[key]

    def get(self, key, default=None):
        if key in self._guarded:
            raise AssertionError(f"read the value of {key}; membership only is allowed")
        return self._real.get(key, default)

    def __setitem__(self, key, value):
        self._real[key] = value

    def __delitem__(self, key):
        del self._real[key]

    def __iter__(self):
        return iter(self._real)

    def __len__(self):
        return len(self._real)

    def __contains__(self, key):
        return key in self._real


@pytest.fixture(autouse=True)
def _clean_cache():
    model_choices.reset_cache()
    yield
    model_choices.reset_cache()


def _install_providers(monkeypatch, profiles):
    module = types.ModuleType("providers")
    calls: list[list] = []

    def list_providers():
        calls.append(list(profiles))
        return list(profiles)

    module.list_providers = list_providers
    monkeypatch.setitem(sys.modules, "providers", module)
    return calls


def _guarded_environ(monkeypatch, guarded):
    monkeypatch.setattr(os, "environ", GuardedEnviron(os.environ, guarded))


def test_default_and_providers_are_sorted_configured_first_then_name(monkeypatch):
    _install_providers(monkeypatch, [
        FakeProfile("zeta", display_name="Zeta", env_vars=("STUDIO_TEST_ZETA_KEY",),
                    fallback_models=("zeta-1",)),
        FakeProfile("alpha", display_name="Alpha", fallback_models=("alpha-1",)),
        FakeProfile("mid", display_name="Mid", fallback_models=("mid-1",)),
    ])
    monkeypatch.setenv("STUDIO_TEST_ZETA_KEY", "not-a-real-secret")

    result = model_choices.list_choices()

    assert result["default"] == {"label": "Your Hermes model"}
    assert [provider["slug"] for provider in result["providers"]] == ["zeta", "alpha", "mid"]
    assert result["providers"][0]["configured"] is True
    assert result["providers"][1]["configured"] is False


def test_hidden_profiles_are_skipped(monkeypatch):
    _install_providers(monkeypatch, [
        FakeProfile("visible", display_name="Visible", fallback_models=("v-1",)),
        FakeProfile("secret-launch", display_name="Secret Launch", hidden=True),
    ])

    result = model_choices.list_choices()

    assert [provider["slug"] for provider in result["providers"]] == ["visible"]


def test_configured_is_membership_only_and_never_reads_the_value(monkeypatch):
    _install_providers(monkeypatch, [
        FakeProfile("acme", display_name="Acme", env_vars=("STUDIO_TEST_ACME_KEY",)),
        FakeProfile("quiet", display_name="Quiet", env_vars=("STUDIO_TEST_QUIET_KEY",)),
    ])
    monkeypatch.setenv("STUDIO_TEST_ACME_KEY", "sk-do-not-read-me")
    monkeypatch.delenv("STUDIO_TEST_QUIET_KEY", raising=False)
    _guarded_environ(monkeypatch, {"STUDIO_TEST_ACME_KEY", "STUDIO_TEST_QUIET_KEY"})

    by_slug = {p["slug"]: p for p in model_choices.list_choices()["providers"]}

    assert by_slug["acme"]["configured"] is True
    assert by_slug["quiet"]["configured"] is False


def test_external_process_configured_is_unknown(monkeypatch):
    _install_providers(monkeypatch, [
        FakeProfile("agent-cli", display_name="Agent CLI", auth_type="external_process",
                    env_vars=("STUDIO_TEST_CLI_TOKEN",)),
    ])
    monkeypatch.setenv("STUDIO_TEST_CLI_TOKEN", "unused")

    provider = model_choices.list_choices()["providers"][0]

    assert provider["configured"] is None


def test_fetch_models_is_called_without_api_key_or_base_url(monkeypatch):
    profile = FakeProfile(
        "acme",
        display_name="Acme",
        fallback_models=("built-in-a",),
        supports_model_listing=True,
        fetched=["live-b", "live-a"],
    )
    _install_providers(monkeypatch, [profile])

    provider = model_choices.list_choices()["providers"][0]

    assert provider["models"] == ["built-in-a", "live-b", "live-a"]
    assert provider["source"] == "public catalog"
    assert profile.fetch_calls, "a listing-capable provider should be asked for its catalog"
    call = profile.fetch_calls[0]
    assert "api_key" not in call and "base_url" not in call
    assert call.get("timeout") == 4.0


def test_a_failed_catalog_fetch_falls_back_to_the_built_in_list(monkeypatch):
    profile = FakeProfile(
        "acme",
        display_name="Acme",
        fallback_models=("built-in-a",),
        supports_model_listing=True,
        fetch_error=RuntimeError("network down"),
    )
    _install_providers(monkeypatch, [profile])

    provider = model_choices.list_choices()["providers"][0]

    assert provider["models"] == ["built-in-a"]
    assert provider["source"] == "built-in list"


def test_profiles_that_do_not_support_listing_are_not_probed(monkeypatch):
    profile = FakeProfile(
        "acme",
        display_name="Acme",
        fallback_models=("built-in-a",),
        supports_model_listing=False,
        fetched=["never"],
    )
    _install_providers(monkeypatch, [profile])

    provider = model_choices.list_choices()["providers"][0]

    assert provider["models"] == ["built-in-a"]
    assert provider["source"] == "built-in list"
    assert profile.fetch_calls == []


def test_output_has_no_base_url_env_names_or_config(monkeypatch):
    _install_providers(monkeypatch, [
        FakeProfile(
            "acme",
            display_name="Acme",
            fallback_models=("acme-1",),
            env_vars=("STUDIO_TEST_ACME_SECRET",),
            base_url="https://private-endpoint.invalid/v1",
            models_url="https://private-endpoint.invalid/v1/models",
        ),
    ])

    provider = model_choices.list_choices()["providers"][0]
    text = json.dumps(model_choices.list_choices())

    assert set(provider) == {"slug", "name", "configured", "models", "source"}
    assert "private-endpoint.invalid" not in text
    assert "STUDIO_TEST_ACME_SECRET" not in text
    assert "base_url" not in text and "env_vars" not in text


def test_standalone_returns_the_default_choice_only(monkeypatch):
    monkeypatch.setitem(sys.modules, "providers", None)

    result = model_choices.list_choices()

    assert result == {"default": {"label": "Your Hermes model"}, "providers": []}


def test_choices_are_cached_for_ten_minutes(monkeypatch):
    calls = _install_providers(monkeypatch, [
        FakeProfile("acme", display_name="Acme", fallback_models=("acme-1",)),
    ])
    clock = [1000.0]
    monkeypatch.setattr(model_choices.time, "monotonic", lambda: clock[0])

    first = model_choices.list_choices()
    clock[0] = 1000.0 + model_choices.CACHE_SECONDS - 1
    second = model_choices.list_choices()
    assert len(calls) == 1, "a fresh answer must be reused"
    assert first == second

    clock[0] = 1000.0 + model_choices.CACHE_SECONDS + 1
    model_choices.list_choices()
    assert len(calls) == 2, "the cache must expire after ten minutes"

    model_choices.reset_cache()
    model_choices.list_choices()
    assert len(calls) == 3


def test_the_route_returns_the_choices(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from standalone.ideas import router

    _install_providers(monkeypatch, [
        FakeProfile("acme", display_name="Acme", fallback_models=("acme-1",)),
    ])
    app = FastAPI()
    app.include_router(router)

    payload = TestClient(app).get("/studio/models").json()

    assert payload == model_choices.list_choices()
    assert payload["default"] == {"label": "Your Hermes model"}
    assert payload["providers"][0]["slug"] == "acme"
