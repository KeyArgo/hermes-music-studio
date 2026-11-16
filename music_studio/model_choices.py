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
from concurrent.futures import ThreadPoolExecutor, wait
from typing import Any

CACHE_SECONDS = 600.0
# Public catalogs are asked in parallel and the whole list waits at most this long; a slow provider
# keeps its built-in list instead of holding the picker up (47 providers one by one took 18 s).
CATALOG_BUDGET_SECONDS = 6.0

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


def _owner_providers() -> list[dict[str, Any]]:
    """The providers the owner defined in Hermes, by name and model ids only.

    ``hermes_cli.config.load_config`` is on the public plugin API. Each entry under ``providers`` can
    hold an ``api_key`` and a ``base_url``; this copies only ``name``, ``models`` and
    ``default_model`` into fresh strings and drops the rest, so no key or address reaches the page.
    """
    try:
        from hermes_cli.config import load_config  # public API (public-api.md)
        section = (load_config() or {}).get("providers") or {}
    except Exception:
        return []
    rows: list[dict[str, Any]] = []
    if not isinstance(section, dict):
        return rows
    for slug, entry in section.items():
        if not isinstance(entry, dict):
            continue
        listed = entry.get("models")
        if isinstance(listed, dict):
            listed = list(listed)
        models = [str(m) for m in (listed or []) if isinstance(m, str)]
        default = entry.get("default_model")
        if isinstance(default, str) and default and default not in models:
            models.insert(0, default)
        rows.append({"slug": str(slug), "name": str(entry.get("name") or slug),
                     "configured": True, "models": models, "source": "your Hermes setup"})
    return rows


def _active_model() -> str:
    """The model Hermes uses by default, for the default option's label (name only)."""
    try:
        from hermes_cli.config import load_config
        model = (load_config() or {}).get("model") or {}
        value = model.get("default") if isinstance(model, dict) else model
        return str(value) if isinstance(value, str) else ""
    except Exception:
        return ""


def _choices(providers: Any) -> list[dict[str, Any]]:
    profiles = [p for p in providers.list_providers() if not getattr(p, "hidden", False)]
    choices: list[dict[str, Any]] = []
    pool = ThreadPoolExecutor(max_workers=16)
    try:
        futures = {pool.submit(_choice, profile): profile for profile in profiles}
        done, _ = wait(futures, timeout=CATALOG_BUDGET_SECONDS)
        for future, profile in futures.items():
            if future in done and future.exception() is None:
                choices.append(future.result())
            else:
                choices.append(_choice_without_catalog(profile))
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    # A provider with no model ids and no setup is a row nobody can use; keep the useful ones.
    choices = [c for c in choices if c["models"] or c["configured"] is True]
    choices.sort(key=lambda choice: (
        0 if choice["configured"] is True else 1,
        choice["name"].casefold(),
        choice["slug"].casefold(),
    ))
    return choices


def _choice_without_catalog(profile: Any) -> dict[str, Any]:
    slug = str(getattr(profile, "name", "") or "")
    return {"slug": slug, "name": str(getattr(profile, "display_name", "") or slug),
            "configured": _configured(profile),
            "models": [m for m in (getattr(profile, "fallback_models", ()) or ()) if isinstance(m, str)],
            "source": "built-in list"}


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
    active = _active_model()
    owner = _owner_providers()
    public = [] if providers is None else _choices(providers)
    seen = {row["slug"] for row in owner}
    choices: dict[str, Any] = {
        "default": {"label": f"{_DEFAULT_LABEL} ({active})" if active else _DEFAULT_LABEL},
        # The owner's own Hermes providers first: they are what "what is in Hermes" means.
        "providers": owner + [row for row in public if row["slug"] not in seen],
    }
    _cache = (now, choices)
    return _copy(choices)
