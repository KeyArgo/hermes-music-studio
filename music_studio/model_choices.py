"""What models Hermes can use, read only from the public ``providers`` module.

A plugin cannot read the owner's credentials or configuration. It can, however, ask the public
provider registry which providers exist, which models each one advertises, and whether the process
environment *mentions* a provider's variable — membership only, never the value. That is enough for
a picker that shows what Hermes knows without leaking a key, a base URL or a config file.

The answer is cached for ten minutes: the registry changes rarely, and a page load must not probe
every provider's catalog on every request. Failures to import ``providers`` (the standalone host has
no Hermes) degrade to the default choice instead of raising.
"""

from __future__ import annotations

import os
import time
from typing import Any

CACHE_SECONDS = 600.0

_DEFAULT_LABEL = "Your Hermes model"

_cache: tuple[float, dict[str, Any]] | None = None


def reset_cache() -> None:
    """Forget the cached answer (a refresh request, or a test)."""
    global _cache
    _cache = None


def _providers_module() -> Any | None:
    """Import the public registry lazily; ``None`` when this is the standalone host."""
    try:
        import providers  # public API: website/docs/developer-guide/plugins/public-api.md
    except Exception:
        return None
    return providers


def _configured(profile: Any) -> bool | None:
    """Whether the provider looks configured, by environment *membership* only.

    ``name in os.environ`` checks the key, never the value, so no secret is read. External-process
    providers keep their login inside a CLI, which the environment cannot answer, so the answer is
    honestly unknown (``None``) rather than a guess.
    """
    if getattr(profile, "auth_type", "") == "external_process":
        return None
    env_vars = getattr(profile, "env_vars", ()) or ()
    try:
        return any(name in os.environ for name in env_vars)
    except Exception:
        return False


def _merge(primary: list[str], extra: list[str]) -> list[str]:
    merged = list(primary)
    for model in extra:
        if model not in merged:
            merged.append(model)
    return merged


def _choice(profile: Any) -> dict[str, Any]:
    slug = str(getattr(profile, "name", "") or "")
    name = str(getattr(profile, "display_name", "") or slug)

    models = [
        model
        for model in (getattr(profile, "fallback_models", ()) or ())
        if isinstance(model, str)
    ]
    source = "built-in list"

    if getattr(profile, "supports_model_listing", False):
        try:
            fetched = profile.fetch_models(timeout=4.0)
        except Exception:
            fetched = None
        live = [model for model in (fetched or ()) if isinstance(model, str)]
        if live:
            models = _merge(models, live)
            source = "public catalog"

    return {
        "slug": slug,
        "name": name,
        "configured": _configured(profile),
        "models": models,
        "source": source,
    }


def _choices(providers: Any) -> list[dict[str, Any]]:
    choices: list[dict[str, Any]] = []
    for profile in providers.list_providers():
        if getattr(profile, "hidden", False):
            continue
        choices.append(_choice(profile))
    choices.sort(key=lambda choice: (
        0 if choice["configured"] is True else 1,
        choice["name"].casefold(),
        choice["slug"].casefold(),
    ))
    return choices


def _copy(choices: dict[str, Any]) -> dict[str, Any]:
    return {
        "default": dict(choices["default"]),
        "providers": [
            {**choice, "models": list(choice["models"])}
            for choice in choices["providers"]
        ],
    }


def list_choices() -> dict[str, Any]:
    """The picker's choices: a default option plus every public provider and its model IDs."""
    global _cache
    now = time.monotonic()
    if _cache is not None and now - _cache[0] < CACHE_SECONDS:
        return _copy(_cache[1])

    providers = _providers_module()
    choices: dict[str, Any] = {
        "default": {"label": _DEFAULT_LABEL},
        "providers": [] if providers is None else _choices(providers),
    }
    _cache = (now, choices)
    return _copy(choices)
