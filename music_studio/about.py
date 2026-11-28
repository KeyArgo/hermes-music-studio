"""About screen data: companion plugin, contribution paths, diagnostics.

Three honest jobs:

- Point at REVamp (the listening side of this studio) with its real install
  command, and say whether this machine already has it running.
- Say where to contribute and where to report a problem. The repository is
  private, so the issue link only works for people the owner invited — that is
  stated instead of pretending a 404 is a bug tracker.
- Build a bug report from what this process actually knows (version, platform,
  library, the newest failed take) so a report is one paste, not an interview.
"""
from __future__ import annotations

import platform
import socket
import sys
from pathlib import Path

REPO = 'https://github.com/KeyArgo/hermes-music-studio'
REPO_VISIBILITY = 'public'

REVAMP = {
    'name': 'REVamp',
    'tagline': 'The listening side of the studio: a Winamp-style player with playlists, '
               'radio, synced lyrics and a MilkDrop visualizer.',
    'repo': 'https://github.com/KeyArgo/hermes-revamp',
    'install': 'hermes plugins install revamp',
    'install_from_git': 'hermes plugins install https://github.com/KeyArgo/hermes-revamp',
    'requires': 'Hermes Agent 0.22.5 or newer; enable both halves in Capabilities → Plugins.',
    'port': 8114,
}

PATHS = {
    'contribute': f'{REPO}/blob/HEAD/CONTRIBUTING.md',
    'issues': f'{REPO}/issues/new',
    'issues_list': f'{REPO}/issues',
    'readme': f'{REPO}/blob/HEAD/README.md',
    'limits': f'{REPO}/blob/HEAD/LIMITS.md',
    'verify': f'{REPO}/blob/HEAD/VERIFY.md',
    'tests': 'python3.13 -m pytest tests -q',
}


def _port_open(port: int, host: str = '127.0.0.1', timeout: float = 0.4) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        return sock.connect_ex((host, port)) == 0


def revamp_status() -> dict:
    """Is REVamp running here, or installed beside this plugin?

    The home comes from Hermes' own public helper, never from a guessed path or the owner's config file.
    """
    from .hermes_home import active_home
    home = active_home()
    installed = home is not None and (any((home / 'plugins').glob('revamp*')) or any(
        (home / 'desktop-plugins').glob('revamp*')))
    running = _port_open(REVAMP['port'])
    if running:
        state = 'running'
    elif installed:
        state = 'installed'
    else:
        state = 'absent'
    return {'installed': installed, 'running': running, **REVAMP, 'state': state}


def _version() -> str:
    try:
        manifest = Path(__file__).resolve().parents[1] / 'plugin.yaml'
        for line in manifest.read_text(encoding='utf-8').splitlines():
            if line.startswith('version:'):
                return line.split(':', 1)[1].strip().strip('"\'')
    except OSError:
        pass
    return 'unknown'


def about() -> dict:
    """Cheap facts for the About/companions panel.

    The version line is the build's own identity (version · commit · date · dirty), read from
    BUILD_INFO.json when installed and from live git in a checkout, so what the panel shows cannot
    drift from the code that is running.
    """
    try:
        from .version_stamp import as_dict, chip_line
        stamp = as_dict()
        stamp['line'] = chip_line(stamp)
    except Exception:  # pragma: no cover - the stamp is optional, the panel is not
        stamp = {'version': _version(), 'commit': '', 'commit_short': '', 'commit_date': '',
                 'built_at': '', 'channel': 'dev', 'dirty': None, 'source': 'manifest'}
        stamp['line'] = 'v' + str(stamp['version'] or 'unknown')
    return {'success': True, 'version': stamp.get('version') or _version(), 'repo': REPO,
            'visibility': REPO_VISIBILITY, 'paths': dict(PATHS),
            'revamp': revamp_status(), 'build': stamp, 'ui': _ui_stamp()}


def _ui_stamp() -> str:
    """The stamp of the page files this server would serve now.

    An open page compares it with the stamp it was loaded with: a page loaded from an older server
    (an update, or a host replaced while the pane was opening) keeps its old code in memory while the
    version chip already reads the new server, and reloads instead of acting on old code.
    """
    try:
        from .ui_stamp import build_token
        return build_token(Path(__file__).resolve().parents[1] / 'standalone' / 'static')
    except Exception:  # pragma: no cover - no stamp means no reload, never a broken panel
        return ''



def diagnostics() -> dict:
    """A paste-ready bug report built from this process, not from questions."""
    from . import service

    library = service.plugin_data_dir(service.PLUGIN_ID)
    store = service.store()
    projects = store.all()
    failures = []
    for project in projects:
        for take in project.get('takes') or []:
            if take.get('state') == 'failed' and take.get('error'):
                failures.append((take.get('finished_at') or 0, take.get('error')))
    failures.sort(reverse=True)
    lines = [
        f'Music Studio {_version()}',
        f'Platform: {platform.platform()}',
        f'Python: {sys.version.split()[0]}',
        f'Library: {library}',
        f'Projects: {len(projects)} · takes: {sum(len(p.get("takes") or []) for p in projects)}',
        f'REVamp: {revamp_status()["state"]}',
    ]
    if failures:
        lines.append(f'Most recent failed take: {failures[0][1]}')
    else:
        lines.append('Most recent failed take: none')
    text = '\n'.join(lines)
    return {'success': True, 'text': text, 'version': _version(),
            'paths': {'issues': PATHS['issues'], 'issues_list': PATHS['issues_list']}}
