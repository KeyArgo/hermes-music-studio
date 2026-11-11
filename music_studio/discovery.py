"""Find a ComfyUI renderer the person already has running.

"The MiniMax endpoint cannot be reached" is the most common first-run failure, and the
usual cause is not a broken install: the server is running on a port or host nobody typed
in. This module looks where renderers actually live — the loopback defaults, whatever the
person already configured, and any hostnames they list in MUSIC_STUDIO_SCAN_HOSTS — and
reports what answered, so the UI can offer it instead of an error.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

# Ports ComfyUI is launched on in practice: its own default, the next ones people pick
# when 8188 is taken, and the port a `--port 7860` Gradio-style launch uses.
PORTS = (8188, 8189, 8190, 7860)
MINIMAX_NODES = ('MiniMaxMusic3TextEncode', 'EmptyMiniMaxMusic3LatentAudio')


def candidates(saved: str = '', extra_hosts: str = '') -> list[str]:
    """Every URL worth trying, in the order a person would expect them."""
    urls: list[str] = []
    for value in (saved, os.environ.get('COMFYUI_URL', '')):
        value = (value or '').strip().rstrip('/')
        if value and value not in urls:
            urls.append(value)
    hosts = ['127.0.0.1', 'localhost', 'host.docker.internal']
    for host in (extra_hosts or os.environ.get('MUSIC_STUDIO_SCAN_HOSTS', '')).split(','):
        host = host.strip()
        if host and host not in hosts:
            hosts.append(host)
    for host in hosts:
        for port in PORTS:
            url = f'http://{host}:{port}'
            if url not in urls:
                urls.append(url)
    return urls


def _get(url: str, path: str, timeout: float):
    with urllib.request.urlopen(f'{url}{path}', timeout=timeout) as response:
        return json.loads(response.read(4_000_000).decode('utf-8', 'replace'))


def probe(url: str, timeout: float = 0.8) -> dict:
    """One endpoint: does ComfyUI answer, and can it run MiniMax Music 3?"""
    try:
        stats = _get(url, '/system_stats', timeout)
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError, TimeoutError) as exc:
        return {'url': url, 'reachable': False, 'comfyui': False, 'minimax': False,
                'detail': f'{type(exc).__name__}: {str(exc)[:120]}'}
    info = {}
    try:
        info = _get(url, '/object_info', timeout)
    except Exception:  # noqa: BLE001 - a reachable server with a slow object_info is still usable
        pass
    has_minimax = all(node in info for node in MINIMAX_NODES) if isinstance(info, dict) and info else False
    return {'url': url, 'reachable': True, 'comfyui': isinstance(stats, dict),
            'minimax': has_minimax,
            'detail': ('MiniMax Music 3 nodes present' if has_minimax else
                       'ComfyUI answers but MiniMax Music 3 nodes are not installed')}


def scan(saved: str = '', extra_hosts: str = '', timeout: float = 0.8) -> dict:
    """Probe every candidate at once; return the ones that answered."""
    urls = candidates(saved, extra_hosts)
    with ThreadPoolExecutor(max_workers=min(8, len(urls))) as pool:
        results = list(pool.map(lambda item: probe(item, timeout), urls))
    found = [item for item in results if item['reachable']]
    return {'success': True, 'tested': len(results), 'found': found,
            'best': (next((item['url'] for item in found if item['minimax']), None)
                     or (found[0]['url'] if found else None))}
