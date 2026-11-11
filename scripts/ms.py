#!/usr/bin/env python3
"""Drive Music Studio from any shell, cron job or agent — a thin HTTP client.

The studio already owns the library, the engine selection and one render at a time. This script adds
no second writer: it finds the loopback host the desktop pane frames (or the one on --base) and asks
it to do the work, so a chat that is not Hermes can still draft, approve, render and export.

    python3 scripts/ms.py status
    python3 scripts/ms.py doctor
    python3 scripts/ms.py create --draft '{"title": "Night drive", "caption": "synthwave", ...}'
    python3 scripts/ms.py approve --project <id> --updated-at <token>
    python3 scripts/ms.py render  --project <id> --timeout 240
    python3 scripts/ms.py takes   --project <id>
    python3 scripts/ms.py export  --project <id> [--take <id>] [--folder DIR]
    python3 scripts/ms.py verify

Every command prints JSON. Exit 0 on success, 1 when the studio answers with an error, 2 when it
cannot be reached at all (that is a different problem from a refused request).
"""
import argparse
import json
import os
import random
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_PORT = 8775
DATA_DIR = Path(os.environ.get('HEWN_STUDIO_DATA_DIR')
                or Path.home() / '.hermes' / 'plugin-data' / 'hermes-music-studio')


def discover_base():
    """The port the pane's host is actually on, from its state file; 8775 is the first choice."""
    state = DATA_DIR / 'studio-host.json'
    try:
        port = int(json.loads(state.read_text())['port'])
    except Exception:
        port = DEFAULT_PORT
    return f'http://127.0.0.1:{port}'


def call(base, path, payload=None, method=None, timeout=30):
    url = base + path
    data = None
    headers = {'Accept': 'application/json'}
    if payload is not None:
        data = json.dumps(payload).encode()
        headers['Content-Type'] = 'application/json'
    request = urllib.request.Request(url, data=data, headers=headers,
                                     method=method or ('POST' if data else 'GET'))
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode('utf-8', 'replace')
        try:
            detail = json.loads(detail).get('detail', detail)
        except Exception:
            pass
        print(json.dumps({'success': False, 'status': exc.code, 'error': detail}, indent=2))
        sys.exit(1)
    except (urllib.error.URLError, OSError) as exc:
        print(json.dumps({
            'success': False, 'base': base, 'error': f'no studio host at {base}: {exc}',
            'hint': 'open the Music Studio pane once (or run scripts/deploy.sh) so the loopback '
                    'host starts, then retry; --base points this script at another port',
        }, indent=2))
        sys.exit(2)
    return json.loads(body) if body else {}


def wait_for_take(base, project, take_id=None, limit=240):
    deadline = time.time() + limit
    while time.time() < deadline:
        status = call(base, '/api/status')
        projects = status.get('projects') or []
        match = next((p for p in projects if p.get('project_id') == project), None)
        if match:
            takes = match.get('takes') or []
            take = next((t for t in takes if not take_id or t.get('take_id') == take_id), None)
            if take and take.get('state') in ('succeeded', 'failed', 'cancelled'):
                return take
        time.sleep(2)
    return {'state': 'running', 'take_id': take_id}


def main(argv=None):
    parser = argparse.ArgumentParser(description='Drive Music Studio over its loopback host.')
    parser.add_argument('command', choices=['status', 'doctor', 'create', 'update', 'approve',
                                            'render', 'queue', 'takes', 'export', 'verify', 'host',
                                            'compare', 'compare-status'])
    parser.add_argument('--base', default=os.environ.get('MUSIC_STUDIO_BASE') or discover_base())
    parser.add_argument('--project', help='project id')
    parser.add_argument('--take', help='take id')
    parser.add_argument('--draft', help='JSON object of draft fields')
    parser.add_argument('--updated-at', dest='updated_at', help='draft version the person reviewed')
    parser.add_argument('--folder', help='export folder')
    parser.add_argument('--seed', type=int)
    parser.add_argument('--timeout', type=int, default=180)
    parser.add_argument('--engines', help='comma-separated engine ids for compare')
    args = parser.parse_args(argv)
    base = args.base.rstrip('/')

    if args.command == 'status':
        print(json.dumps(call(base, '/api/status'), indent=2))
    elif args.command == 'doctor':
        print(json.dumps(call(base, '/api/doctor'), indent=2))
    elif args.command == 'verify':
        print(json.dumps(call(base, '/api/verify.json'), indent=2))
    elif args.command == 'host':
        print(json.dumps({'base': base, 'data_dir': str(DATA_DIR)}, indent=2))
    elif args.command == 'create':
        # The route takes {'draft': {...}}: a bare object is rejected as an invalid draft.
        print(json.dumps(call(base, '/api/create',
                              {'draft': json.loads(args.draft or '{}')}), indent=2))
    elif args.command == 'update':
        payload = {'project_id': args.project, 'draft': json.loads(args.draft or '{}')}
        print(json.dumps(call(base, '/api/update', payload), indent=2))
    elif args.command == 'approve':
        payload = {'project_id': args.project}
        if args.updated_at:
            # The route names the reviewed version `base_updated_at`; a missing token is a 409 by
            # design, so an approval can never attach to text nobody read.
            token = args.updated_at
            try:  # the store keeps a numeric token; a shell passes it as text
                token = float(token)
            except (TypeError, ValueError):
                pass
            payload['base_updated_at'] = token
        print(json.dumps(call(base, '/api/approve', payload), indent=2))
    elif args.command == 'takes':
        print(json.dumps(call(base, f'/api/status'), indent=2))
    elif args.command == 'queue':
        # Same route as render, but no waiting: prints the take id and exits, so a shell or a chat
        # stays free while the worker renders.
        seed = args.seed if args.seed is not None else random.randrange(1, 2 ** 31 - 1)
        queued = call(base, '/api/generate', {'project_id': args.project, 'seed': seed})
        print(json.dumps({'queued': queued,
                          'take': (queued.get('take') or {}).get('take_id'),
                          'note': 'rendering in the background; poll with takes'}, indent=2))
        sys.exit(0)
    elif args.command == 'render':
        # The route requires an integer seed. A chat does not care which one, but the take records
        # it, so it must be chosen once and reported rather than left absent.
        seed = args.seed if args.seed is not None else random.randrange(1, 2 ** 31 - 1)
        payload = {'project_id': args.project, 'seed': seed}
        queued = call(base, '/api/generate', payload)
        take_id = (queued.get('take') or {}).get('take_id')
        finished = wait_for_take(base, args.project, take_id, limit=min(args.timeout, 240))
        print(json.dumps({'queued': queued, 'take': finished}, indent=2))
        sys.exit(0 if finished.get('state') == 'succeeded' else 1)
    elif args.command == 'compare':
        engines = [e.strip() for e in (args.engines or '').split(',') if e.strip()] or None
        plan = call(base, '/api/compare', {'project_id': args.project, 'engines': engines})
        print(json.dumps(plan, indent=2))
        deadline = time.time() + max(args.timeout, 60) * 4
        while time.time() < deadline:
            state = call(base, f'/api/compare/{args.project}').get('plan') or {}
            rows = state.get('engines') or []
            print(json.dumps({'state': state.get('state'),
                              'engines': [{k: r.get(k) for k in
                                           ('engine_id', 'state', 'seconds', 'peak_dbfs', 'error')}
                                          for r in rows]}, indent=2))
            if state.get('state') != 'running':
                break
            time.sleep(10)
    elif args.command == 'compare-status':
        print(json.dumps(call(base, f'/api/compare/{args.project}'), indent=2))
    elif args.command == 'export':
        take_id = args.take
        if not take_id:
            # The route demands a take id; pick the newest finished one rather than making the
            # caller look it up, and say which one was chosen.
            status = call(base, '/api/status')
            project = next((p for p in status.get('projects', [])
                            if p.get('project_id') == args.project), None)
            if not project:
                print(json.dumps({'success': False, 'error': 'project not found'}, indent=2))
                sys.exit(1)
            finished = [t for t in project.get('takes', []) if t.get('state') == 'succeeded']
            if not finished:
                print(json.dumps({'success': False,
                                  'error': 'no finished take to export — render first'}, indent=2))
                sys.exit(1)
            take_id = finished[-1]['take_id']
        payload = {'project_id': args.project, 'take_id': take_id}
        if args.folder:
            payload['folder'] = args.folder
        print(json.dumps(call(base, '/api/export', payload), indent=2))


if __name__ == '__main__':
    main()
