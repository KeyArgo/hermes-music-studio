"""Every model the owner's Hermes can actually reach, grouped by provider.

The pane's provider/model pickers used to be empty by design: `/studio/models`
returned `providers: []` because a plugin "cannot enumerate the owner's
providers any more". That left the creative partner on whatever model Hermes
happened to be configured with, with nothing to search.

This module answers the same question the way Hermes itself does — by asking each
configured provider for its own model list, with the credential Hermes already
stores:

* providers come from ``auth.json`` (``credential_pool``/``providers``) and the
  active ``model.provider`` in ``config.yaml``;
* the credential is read from ``auth.json`` or a ``<PROVIDER>_API_KEY`` style
  variable in ``.env``; it is used for the request and never returned, logged or
  cached in the payload;
* ``GET <base_url>/models`` is the source of truth, so the list is what the
  provider serves today, not a hand-maintained table;
* every failure is data: a provider that cannot be listed carries its own
  ``detail`` and the others still appear.

The result is cached briefly — a picker is opened far more often than a provider
changes its catalogue.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

CACHE_SECONDS = 600.0
TIMEOUT_SECONDS = 6.0
# Cloudflare in front of some providers rejects the default urllib agent (1010).
USER_AGENT = 'Hermes-Music-Studio/1.0'
FREE_MARK = re.compile(r'(^|[-_/])(free|trial)([-_/]|$)', re.I)

_cache: dict = {'at': 0.0, 'payload': None}
_lock = threading.Lock()


def hermes_home() -> Path:
    """The Hermes home the plugin storage lives under."""
    override = os.environ.get('HERMES_HOME')
    if override:
        return Path(override)
    for candidate in (Path.home() / '.hermes',
                      Path(os.environ.get('LOCALAPPDATA') or '') / 'hermes'):
        if candidate.is_dir():
            return candidate
    return Path.home() / '.hermes'


def _load_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _load_env(path: Path) -> dict:
    values = {}
    try:
        for line in path.read_text(encoding='utf-8', errors='replace').splitlines():
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            key, value = line.split('=', 1)
            values[key.strip()] = value.strip().strip('"').strip("'")
    except OSError:
        pass
    return values


def _load_yaml(path: Path) -> dict:
    try:
        import yaml  # noqa: PLC0415 - optional, Hermes itself ships it
        data = yaml.safe_load(path.read_text(encoding='utf-8'))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _first(entries, names):
    """First non-empty value among ``names`` in a credential entry."""
    for name in names:
        value = entries.get(name)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _credential(pool_entry: dict, provider_entry: dict, slug: str, env: dict) -> str | None:
    for entry in (pool_entry, provider_entry):
        found = _first(entry, ('api_key', 'key', 'agent_key', 'access_token', 'token'))
        if found:
            return found
    env_name = slug.upper().replace('-', '_') + '_API_KEY'
    return env.get(env_name) or None


def _base_url(pool_entry: dict, provider_entry: dict, slug: str, config: dict) -> str | None:
    for entry in (pool_entry, provider_entry):
        found = _first(entry, ('base_url', 'inference_base_url', 'api_base'))
        if found:
            return found.rstrip('/')
    model = config.get('model') or {}
    if isinstance(model, dict) and str(model.get('provider') or '') == slug:
        base = model.get('base_url')
        if isinstance(base, str) and base.strip():
            return base.strip().rstrip('/')
    return None


def _fetch(base: str, credential: str) -> tuple[list[str], str | None]:
    """The provider's own model ids, or ([], reason)."""
    request = urllib.request.Request(
        base + '/models',
        headers={'Authorization': f'Bearer {credential}',
                 'User-Agent': USER_AGENT,
                 'Accept': 'application/json'})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read() or b'{}')
    except urllib.error.HTTPError as exc:
        return [], f'the provider answered HTTP {exc.code}'
    except (urllib.error.URLError, TimeoutError, OSError):
        return [], 'the provider did not answer'
    except ValueError:
        return [], 'the provider sent something that is not JSON'
    rows = payload.get('data') if isinstance(payload, dict) else None
    if rows is None and isinstance(payload, dict):
        rows = payload.get('models')
    models = []
    for row in rows or []:
        if isinstance(row, dict):
            model_id = row.get('id') or row.get('name')
        else:
            model_id = row
        if isinstance(model_id, str) and model_id.strip():
            models.append(model_id.strip())
    models.sort()
    if not models:
        return [], 'the provider listed no models'
    return models, None


def _entry(slug: str, models: list[str], detail: str, source: str) -> dict:
    # Sorted here rather than only in _fetch: the order the picker shows must not
    # depend on which code path produced the list.
    models = sorted(models)
    return {'slug': slug,
            'name': slug.replace('-', ' ').replace('_', ' ').title(),
            'models': [{'id': model, 'free': bool(FREE_MARK.search(model))} for model in models],
            'count': len(models),
            'source': source,
            'detail': detail}


def discover(force: bool = False) -> dict:
    """Providers and their models. Cached; ``force`` re-asks every provider."""
    now = time.time()
    with _lock:
        if not force and _cache['payload'] and now - _cache['at'] < CACHE_SECONDS:
            return _cache['payload']

        home = hermes_home()
        auth = _load_json(home / 'auth.json')
        config = _load_yaml(home / 'config.yaml')
        env = _load_env(home / '.env')
        pool = auth.get('credential_pool') or {}
        known = auth.get('providers') or {}
        slugs = []
        for source in (pool, known):
            for slug in source:
                if slug not in slugs:
                    slugs.append(slug)
        model = config.get('model') or {}
        active = model.get('provider') if isinstance(model, dict) else None
        if isinstance(active, str) and active and active not in slugs:
            slugs.append(active)

        providers = []
        for slug in slugs:
            raw_pool = pool.get(slug) or {}
            pool_entry = raw_pool[0] if isinstance(raw_pool, list) and raw_pool else (
                raw_pool if isinstance(raw_pool, dict) else {})
            provider_entry = known.get(slug) if isinstance(known.get(slug), dict) else {}
            base = _base_url(pool_entry, provider_entry, slug, config)
            credential = _credential(pool_entry, provider_entry, slug, env)
            if not base:
                providers.append(_entry(slug, [], 'no endpoint is configured for this provider', 'none'))
                continue
            if not credential:
                providers.append(_entry(slug, [], 'no credential is stored for this provider', 'none'))
                continue
            models, problem = _fetch(base, credential)
            providers.append(_entry(slug, models, problem or 'listed by the provider',
                                    'live' if models else 'none'))

        providers.sort(key=lambda row: (row['count'] == 0, row['slug']))
        payload = {
            'providers': providers,
            'count': sum(row['count'] for row in providers),
            'active_provider': active if isinstance(active, str) else None,
            'active_model': model.get('default') if isinstance(model, dict) else None,
            'detail': ('Hermes has no credential for any provider yet.'
                       if not providers else 'Models are listed by each provider.'),
        }
        _cache.update({'at': now, 'payload': payload})
        return payload


def default_choice() -> dict:
    """What Hermes uses when the person does not pick."""
    state = discover()
    return {'provider': state.get('active_provider'), 'model': state.get('active_model')}


def invalidate() -> None:
    with _lock:
        _cache.update({'at': 0.0, 'payload': None})
