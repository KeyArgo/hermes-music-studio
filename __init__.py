"""Hermes agent plugin entrypoint.

Two ways a chat can drive the studio:

- the `music_studio` tool (below), for any Hermes session with this plugin enabled;
- `scripts/ms.py`, a small HTTP client for shells, cron jobs and agents that are not Hermes.

Both end at the same place: the library in this profile's plugin data. Nothing here renders on its
own; `render` queues the detached worker that does, and a draft must be approved first.
"""
import json
import sys
import time
from pathlib import Path

# Hermes may import a plugin as a standalone file; dashboard does the same.
_ROOT = str(Path(__file__).resolve().parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
from music_studio import service

ACTIONS = ('status', 'create', 'update', 'change', 'keep', 'snippet', 'snippet_splice', 'check_take',
           'checkpoint', 'checkpoints', 'restore', 'doctor', 'approve', 'render', 'queue', 'takes',
           'export', 'wait', 'compare', 'compare_status')

SCHEMA = {
    'name': 'music_studio',
    'description': (
        'The local song studio: draft, approve, render and export takes. Call `status` first; '
        '`create`/`update` to draft; `approve` only after the person has said yes in this chat; '
        '`render` to queue a take and wait for it; `compare` to render the same approved draft on every '
        'ready engine as separate takes; `export` to write the finished WAV into their music folder. '
        'An unapproved draft never renders.'),
    'parameters': {
        'type': 'object',
        'properties': {
            'action': {'type': 'string', 'enum': list(ACTIONS)},
            'project_id': {'type': 'string', 'description': 'Project id from status or create'},
            'take_id': {'type': 'string', 'description': 'Take id from status or takes'},
            'draft': {'type': 'object', 'description': 'Draft fields: title, caption, lyrics, '
                      'duration_seconds, instrumental, engine, seed, bpm, keyscale, vocal_language'},
            'seed': {'type': 'integer'},
            'updated_at': {'type': 'string',
                           'description': 'The draft version the person reviewed, from create or '
                                          'status. An approval attaches to exactly this version.'},
            'folder': {'type': 'string', 'description': 'Export folder; defaults to the setting'},
            'engines': {'type': 'array', 'items': {'type': 'string'},
                        'description': 'For compare: engine ids, in order. Defaults to every ready engine.'},
            'instruction': {'type': 'string',
                            'description': 'For change: plain words such as "add a fiddle" or "go dark"'},
            'timeout_s': {'type': 'integer', 'description': 'How long to wait, max 240'},
        },
        'required': ['action'],
    },
}


def _takes_of(project):
    """A compact view of a project's takes, with the state a chat has to act on."""
    return [{
        'take_id': take.get('take_id'),
        'state': take.get('state'),
        'engine_id': take.get('engine_id'),
        'requested_seed': take.get('requested_seed'),
        # `audio_seconds` is only written by some engines; the quality check knows the real
        # length of the file, and a chat asking "how long is it" should get a number.
        'seconds': take.get('audio_seconds') or (take.get('qc') or {}).get('file_s'),
        'error': take.get('error'),
    } for take in project.get('takes', [])]


def _find_take(project, take_id=None):
    takes = project.get('takes', [])
    if take_id:
        for take in takes:
            if take.get('take_id') == take_id:
                return take
        raise ValueError(f'no take {take_id} in this project')
    finished = [t for t in takes if t.get('state') == 'succeeded']
    if finished:
        return finished[-1]
    if takes:
        return takes[-1]
    raise ValueError('this project has no takes yet — call render first')


def _render(args):
    """Queue a take and wait for it, so a chat gets a result rather than a job id."""
    project_id = args.get('project_id')
    if not project_id:
        raise ValueError('project_id is required')
    result = service.generate(project_id, args.get('seed'))
    take_id = (result.get('take') or {}).get('take_id')
    limit = min(int(args.get('timeout_s') or 180), 240)
    started = time.time()
    deadline = started + limit
    while time.time() < deadline:
        project = service.store().get(project_id)
        take = next((t for t in project.get('takes', [])
                     if not take_id or t.get('take_id') == take_id), None)
        if take and take.get('state') in ('succeeded', 'failed', 'cancelled'):
            result['take'] = {k: take.get(k) for k in
                              ('take_id', 'state', 'engine_id', 'audio_seconds', 'error',
                               'requested_seed')}
            result['waited_s'] = round(time.time() - started, 1)
            if take.get('state') != 'succeeded':
                result['hint'] = ('the take did not finish; call doctor for the engine and '
                                  'takes for the recorded error')
            return result
        time.sleep(2)
    result['take'] = {'take_id': take_id, 'state': 'running'}
    result['hint'] = (f'still rendering after {limit}s. That is normal for a long ACE take; call '
                      'wait in a minute instead of starting a second render')
    return result


def _queue(args):
    """Queue a take and return at once — the non-blocking half of `render`.

    `render` waits so a chat can answer with a file; this one is for commands and for any caller that
    wants the job id in milliseconds and will check later with `takes`/`wait`. Both go through
    `service.generate`, so the approval gate and the one-render-at-a-time lease are unchanged.
    """
    project_id = args.get('project_id')
    if not project_id:
        raise ValueError('project_id is required')
    result = service.generate(project_id, args.get('seed'))
    take = result.get('take') or {}
    return {'success': True,
            'take': {k: take.get(k) for k in
                     ('take_id', 'state', 'engine_id', 'requested_seed') if take.get(k) is not None},
            'engine': take.get('engine_id'),
            'note': ('queued; the worker renders in the background. Check with takes, or wait.' )}


def _handler(args):
    if not isinstance(args, dict):
        raise ValueError('arguments must be an object')
    action = args.get('action')
    store = service.store()

    if action == 'status':
        return service.status()
    if action == 'doctor':
        return {'success': True, 'engines': service.doctor()}
    if action == 'create':
        return {'success': True, 'project': store.create(**args.get('draft', {}))}
    if action == 'update':
        return {'success': True,
                'project': store.update(args.get('project_id'), **args.get('draft', {}))}
    if action == 'change':
        # Plain words → a draft patch. Both engines take ONE caption, so the words become style text
        # rather than a knob; the reply says that, and every change lands in the song's journal.
        from music_studio import intent

        project_id = args.get('project_id')
        if not project_id:
            raise ValueError('project_id is required')
        project = store.get(project_id)
        result = intent.apply(args.get('instruction'), project)
        updated = store.update(project_id, **result['patch'])
        store.remember(project_id, kind='change', text=args.get('instruction'),
                       patch=result['patch'])
        return {'success': True, 'project': updated, 'changed': intent.describe(result['patch']),
                'notes': result['notes'], 'section': result['section'],
                'note': intent.REQUESTS_NOTE}
    if action == 'keep':
        # Liking a take is bookkeeping, not a draft edit: it must not revoke an approval.
        project_id, take_id = args.get('project_id'), args.get('take_id')
        if not project_id or not take_id:
            raise ValueError('project_id and take_id are required')
        store.get(project_id)
        memory = store.remember(project_id, kind='keep', text='', take_id=take_id, bump=False)
        return {'success': True, 'kept_take_id': memory.get('kept_take_id')}
    if action == 'checkpoint':
        from music_studio import checkpoints

        if not args.get('project_id'):
            raise ValueError('project_id is required')
        return checkpoints.save(store, args['project_id'], label=args.get('label'),
                                playhead_s=args.get('playhead_s'))
    if action == 'checkpoints':
        from music_studio import checkpoints

        if not args.get('project_id'):
            raise ValueError('project_id is required')
        return checkpoints.listing(store, args['project_id'])
    if action == 'restore':
        from music_studio import checkpoints

        if not args.get('project_id') or not args.get('checkpoint_id'):
            raise ValueError('project_id and checkpoint_id are required')
        return checkpoints.restore(store, args['project_id'], args['checkpoint_id'])
    if action == 'check_take':
        from music_studio import takecheck

        if not args.get('project_id') or not args.get('take_id'):
            raise ValueError('project_id and take_id are required')
        if args.get('retry'):
            return takecheck.retry(args['project_id'], args['take_id'], store,
                                   lambda pid, seed: service.generate(pid, seed))
        return takecheck.report(args['project_id'], args['take_id'], store)
    if action == 'snippet':
        from music_studio import intent

        for field in ('project_id', 'take_id', 'start_s', 'end_s', 'instruction'):
            if args.get(field) in (None, ''):
                raise ValueError(f'{field} is required')
        return service.snippet(args['project_id'], args['take_id'], args['start_s'], args['end_s'],
                               args['instruction'])
    if action == 'snippet_splice':
        if not args.get('project_id') or not args.get('take_id'):
            raise ValueError('project_id and take_id are required')
        return service.splice_snippet(args['project_id'], args['take_id'])
    if action == 'approve':
        project = store.approve(args.get('project_id'), base_updated_at=args.get('updated_at'))
        return {'success': True, 'project': project,
                'note': 'approved for exactly the draft version the person reviewed'}
    if action == 'render':
        return _render(args)
    if action == 'queue':
        return _queue(args)
    if action == 'takes':
        project = store.get(args.get('project_id'))
        return {'success': True, 'project_id': project['project_id'],
                'title': project.get('title'), 'approved': project.get('approved'),
                'updated_at': project.get('updated_at'), 'takes': _takes_of(project)}
    if action == 'export':
        project_id = args.get('project_id')
        if not project_id:
            raise ValueError('project_id is required')
        take = _find_take(store.get(project_id), args.get('take_id'))
        result = service.export_take(project_id, take['take_id'],
                                     folder=args.get('folder') or None)
        return {'success': True, 'export': result}
    if action == 'compare':
        from music_studio import compare

        project_id = args.get('project_id')
        if not project_id:
            raise ValueError('project_id is required')
        return {'success': True, 'plan': compare.start(project_id, args.get('engines'),
                                                       args.get('seed'))}
    if action == 'compare_status':
        from music_studio import compare

        project_id = args.get('project_id')
        if not project_id:
            raise ValueError('project_id is required')
        return {'success': True, 'plan': compare.status(project_id)}
    if action == 'wait':
        project_id = args.get('project_id')
        if not project_id:
            raise ValueError('project_id is required')
        limit = min(int(args.get('timeout_s') or 120), 240)
        deadline = time.time() + limit
        while time.time() < deadline:
            take = _find_take(store.get(project_id), args.get('take_id'))
            if take.get('state') in ('succeeded', 'failed', 'cancelled'):
                return {'success': True, 'take': {k: take.get(k) for k in
                        ('take_id', 'state', 'engine_id', 'audio_seconds', 'error')}}
            time.sleep(2)
        return {'success': True, 'take': {'state': 'running'},
                'hint': f'nothing finished within {limit}s'}
    raise ValueError(f'unknown action {action!r}; expected one of {", ".join(ACTIONS)}')


class _ChatClient:
    """What `/song` needs from the studio, with no host and no HTTP: the same `_handler` the tool uses.

    Reads the running host's URL from its state file instead of starting one — a chat command must not
    spawn a server just to print a link.
    """

    def status(self):
        return _handler({'action': 'status'}).get('projects') or []

    def approve(self, project_id, token):
        return _handler({'action': 'approve', 'project_id': project_id, 'updated_at': token})

    def save_spot(self, project_id, label=None):
        return _handler({'action': 'checkpoint', 'project_id': project_id, 'label': label})

    def spots(self, project_id):
        return _handler({'action': 'checkpoints', 'project_id': project_id})

    def back_to(self, project_id, checkpoint_id):
        return _handler({'action': 'restore', 'project_id': project_id,
                         'checkpoint_id': checkpoint_id})

    def check(self, project_id, take_id, retry=False):
        return _handler({'action': 'check_take', 'project_id': project_id, 'take_id': take_id,
                         'retry': retry})

    def snippet(self, project_id, take_id, start_s, end_s, instruction):
        return _handler({'action': 'snippet', 'project_id': project_id, 'take_id': take_id,
                         'start_s': start_s, 'end_s': end_s, 'instruction': instruction})

    def splice(self, project_id, take_id):
        return _handler({'action': 'snippet_splice', 'project_id': project_id, 'take_id': take_id})

    def change(self, project_id, instruction):
        return _handler({'action': 'change', 'project_id': project_id,
                         'instruction': instruction})

    def keep(self, project_id, take_id):
        return _handler({'action': 'keep', 'project_id': project_id, 'take_id': take_id})

    def queue(self, project_id):
        return _handler({'action': 'queue', 'project_id': project_id})

    def export(self, project_id, take_id):
        return _handler({'action': 'export', 'project_id': project_id,
                         'take_id': take_id}).get('export') or {}

    def play_url(self, project_id, take_id):
        from music_studio import host_launcher

        base = 'http://127.0.0.1:8775'
        try:
            state = host_launcher._read_state(host_launcher.data_dir()) or {}
            base = state.get('url') or base
        except Exception:
            pass
        return f'{base}/api/projects/{project_id}/takes/{take_id}/audio'


def register(ctx):
    def handler(args, **kwargs):
        try:
            return json.dumps(_handler(args))
        except FileNotFoundError:
            return json.dumps({'success': False, 'error': 'project not found'})
        except KeyError as exc:
            return json.dumps({'success': False, 'error': f'missing field: {exc}'})
        except (ValueError, TypeError, RuntimeError) as exc:
            return json.dumps({'success': False, 'error': str(exc)})
        except Exception:
            import logging
            logging.getLogger(__name__).exception('music_studio tool failed')
            return json.dumps({'success': False, 'error': 'studio operation failed'})

    ctx.register_tool(name='music_studio', toolset='music_studio',
                      schema=SCHEMA, handler=handler)

    # Slash commands: `/song ...` in any chat. Same gate as the tool — the first `/song go` only
    # shows the draft, and the render is queued (never waited on) after the person confirms it.
    if callable(getattr(ctx, 'register_command', None)):
        def on_song(raw='', **kwargs):
            # A Hermes command handler receives the typed text as a string (see any other plugin's
            # register_command). Accepting a dict too keeps a tool-style caller working, but the
            # string is the real contract — assuming a dict crashed every `/song` with
            # "'str' object has no attribute 'get'".
            from music_studio import chat

            if isinstance(raw, str):
                text = raw
            elif isinstance(raw, dict):
                text = str(raw.get('text') or raw.get('args') or '')
            else:
                text = str(raw or '')
            try:
                return chat.handle(text, _ChatClient())
            except FileNotFoundError:
                return 'That song is not in this library any more.'
            except (ValueError, TypeError, RuntimeError) as exc:
                return f'The studio refused: {exc}'

        try:
            ctx.register_command(
                'song', on_song,
                'Music Studio. /song lists your songs; /song go [id] shows a draft, '
                'then /song go [id] now approves it and queues one take.',
                args_hint='go | go <id> now | takes <id> | play <id> | export <id>',
                argument_mode='text')
        except Exception:
            import logging
            logging.getLogger(__name__).exception('Unable to register the /song command')

    # Bundled guidance skill: interview the user, compile caption+lyrics, and
    # stop at the approval gate. Optional support surface — a skill
    # registry problem must never prevent the tool from loading.
    try:
        ctx.register_skill(
            'music-studio', Path(_ROOT) / 'skills' / 'music-studio' / 'SKILL.md',
            description='Interview a song idea, draft lyrics and style, then render.')
    except Exception:
        import logging
        logging.getLogger(__name__).exception('Unable to register the music-studio skill')
