"""Slash commands for the studio, shaped so a chat can drive it without blocking.

The rule this module encodes: **a command may show, and a command may queue — it never waits for a
render.** `service.generate` already returns the take the moment the worker is spawned, so the command
can answer in milliseconds and the pane/worker reports the finish. Everything here returns a plain
string, because that is what a Hermes command handler gives back to the transcript.

The approval gate is kept, and it is *two-step on purpose*: the first `/song go` prints the draft the
person is about to render (title, caption, engine, lyric count, the token) and queues nothing; the
second `/song go` — after they have read it — approves that exact version and queues it. A gate that
approves on the first word is not a gate.
"""

from __future__ import annotations

import time

USAGE = ('/song                     what is in the library\n'
         '/song change <id> <words> change the draft in plain words ("add a fiddle", "go dark")\n'
         '/song keep <id>           remember the newest finished take as this song\'s keeper\n'
         '/song check <id> [retry] did the take do what you asked? facts only\n'
         '/song save [label]         save your spot; /song saves, /song back <id>\n'
         '/song go [project]        show the draft, then confirm before anything renders\n'
         '/song go [project] now    approve the draft you were shown and queue one take\n'
         '/song takes [project]     the takes and their states\n'
         '/song play [project]      the newest finished take, ready to play in the studio\n'
         '/song export [project]    copy the newest finished take into your music folder')

# Projects whose draft has been shown in this process, and the version shown. Keyed by project id.
_shown: dict[str, float] = {}


def _short(project_id):
    return str(project_id or '')[:8]


def _newest(projects):
    """The most recently touched project — what an unqualified `/song ...` means."""
    if not projects:
        return None
    return max(projects, key=lambda p: (p.get('updated_at') or 0, p.get('created_at') or 0))


def _pick(projects, wanted):
    if not wanted:
        return _newest(projects)
    wanted = str(wanted)
    for project in projects:
        if str(project.get('project_id')) == wanted or _short(project.get('project_id')) == wanted:
            return project
    return None


def _lines(text, limit=2):
    rows = [line.strip() for line in str(text or '').splitlines() if line.strip()]
    return rows[:limit]


def describe(project):
    """The draft as the person will see it before it renders — the thing the gate is protecting."""
    takes = project.get('takes') or []
    finished = [t for t in takes if t.get('state') == 'succeeded']
    head = f"{project.get('title') or 'Untitled'}  [{_short(project.get('project_id'))}]"
    body = [head,
            f"  engine: {project.get('engine') or 'unset'}"
            f" · {project.get('duration_seconds') or '?'} s"
            f" · approved: {'yes' if project.get('approved') else 'no'}",
            f"  caption: {project.get('caption') or '(empty)'}"]
    lyrics = _lines(project.get('lyrics'), 2)
    if project.get('instrumental'):
        body.append('  lyrics: instrumental (none)')
    elif lyrics:
        body.append('  lyrics: ' + ' / '.join(lyrics) + (' …' if len(lyrics) == 2 else ''))
    else:
        body.append('  lyrics: (empty — an instrumental needs the instrumental flag)')
    body.append(f"  takes: {len(takes)} ({len(finished)} finished)")
    memory = project.get('memory') or {}
    events = memory.get('events') or []
    if events:
        last = events[-1]
        body.append(f"  memory: {len(events)} note(s); last {last.get('kind')}: "
                    f"{(last.get('text') or '')[:60]}")
    if memory.get('kept_take_id'):
        body.append(f"  keeper: {_short(memory['kept_take_id'])}")
    return '\n'.join(body)


def library(projects):
    if not projects:
        return ('No songs yet. Open Music Studio and press Create, or ask a chat to make one with the '
                'music_studio tool (create → approve → queue).')
    rows = ['Your songs (newest first):']
    for project in sorted(projects, key=lambda p: p.get('updated_at') or 0, reverse=True)[:8]:
        takes = project.get('takes') or []
        done = sum(1 for t in takes if t.get('state') == 'succeeded')
        rows.append(f"  {_short(project.get('project_id'))}  {project.get('title') or 'Untitled'}"
                    f"  · {project.get('engine') or 'no engine'}"
                    f"  · approved: {'yes' if project.get('approved') else 'no'}"
                    f"  · takes {len(takes)} ({done} finished)")
    rows.append('Say /song go <id> to hear about one before it renders.')
    return '\n'.join(rows)


def takes_text(project):
    takes = project.get('takes') or []
    if not takes:
        return f"{project.get('title') or 'Untitled'} has no takes yet. /song go {_short(project.get('project_id'))} now"
    rows = [f"{project.get('title') or 'Untitled'} — {len(takes)} take(s):"]
    for take in takes[-6:]:
        seconds = take.get('audio_seconds') or (take.get('qc') or {}).get('file_s')
        length = f"{round(float(seconds), 1)} s" if seconds else '—'
        rows.append(f"  {_short(take.get('take_id'))}  {take.get('state')}"
                    f"  · {take.get('engine_id') or '?'}  · {length}"
                    + (f"  · {take.get('error')}" if take.get('error') else ''))
    return '\n'.join(rows)


def parse(text):
    """Split a command line into (verb, project token, words). No argparse: one honest shape."""
    words = str(text or '').split()
    verb = (words[0].lower() if words else '')
    rest = words[1:]
    project = None
    if rest and not rest[0].lower() in ('now', 'confirm'):
        project = rest[0]
        rest = rest[1:]
    return verb, project, rest


def handle(text, client, *, now=None):
    """Answer one `/song ...` line. `client` is the studio, injected so tests need no host."""
    now = time.time() if now is None else now
    verb, project_token, rest = parse(text)

    if verb in ('', 'list'):
        return library(client.status())

    project = _pick(client.status(), project_token)
    if project is None:
        return (f"No song matches {project_token!r}. {library(client.status())}" if project_token
                else library(client.status()))

    if verb == 'takes':
        return takes_text(project)

    if verb == 'play':
        take = _newest_finished(project)
        if take is None:
            return (f"{project.get('title') or 'Untitled'} has nothing finished to play yet. "
                    f"/song takes {_short(project.get('project_id'))}")
        url = client.play_url(project['project_id'], take['take_id'])
        return (f"Playing {project.get('title') or 'Untitled'} — {_short(take['take_id'])} "
                f"({take.get('engine_id') or '?'}).\n  {url}")

    if verb == 'export':
        take = _newest_finished(project)
        if take is None:
            return (f"{project.get('title') or 'Untitled'} has nothing finished to export yet.")
        result = client.export(project['project_id'], take['take_id'])
        return (f"Exported to {result.get('path')}"
                f" ({round((result.get('bytes') or 0) / 1e6, 1)} MB).")

    if verb == 'change':
        words = ' '.join(rest).strip()
        if not words:
            return ('Say what to change: /song change ' + _short(project.get('project_id'))
                    + ' add a fiddle')
        result = client.change(project['project_id'], words)
        changed = '\n'.join('  ' + line for line in (result.get('changed') or []))
        notes = '\n'.join('  · ' + note for note in (result.get('notes') or []))
        return (f"Changed {project.get('title') or 'Untitled'}:\n{changed}\n{notes}\n"
                f"  {result.get('note') or ''}\n"
                f"  The approval was revoked — /song go {_short(project.get('project_id'))} to review it.")

    if verb == 'snippet':
        # `/song snippet <id> <from>-<to> <words>` — the range is parsed here so a chat never has to
        # remember two flags.
        if not rest or rest[0].count('-') != 1:
            return 'Give the range like this: /song snippet ' + _short(project.get('project_id')) + ' 10-18 make it more intense'
        try:
            start_s, end_s = (float(part) for part in rest[0].split('-'))
        except ValueError:
            return 'The range looks like 10-18 (seconds).'
        words = ' '.join(rest[1:]).strip()
        if not words:
            return 'Say what that part should become: /song snippet <id> 10-18 go darker'
        take = _newest_finished(project)
        if take is None:
            return f"{project.get('title') or 'Untitled'} has nothing finished to edit yet."
        result = client.snippet(project['project_id'], take['take_id'], start_s, end_s, words)
        child = _short((result.get('take') or {}).get('take_id'))
        return (f"Repainting {start_s:g}–{end_s:g}s as take {child} on {take.get('engine_id')}."
                f"\n  The rest of the song is untouched. When it finishes:"
                f"\n  /song splice {_short(project.get('project_id'))} {child}")

    if verb == 'splice':
        child = rest[0] if rest else ''
        if not child:
            return 'Name the take to splice back: /song splice <id> <take>'
        result = client.splice(project['project_id'], child)
        merged = _short((result.get('take') or {}).get('take_id'))
        return (f"Spliced into a new take {merged}: only that range changed, the rest is the "
                f"parent's own audio.")

    if verb == 'save':
        label = ' '.join(rest).strip() or None
        result = client.save_spot(project['project_id'], label)
        return (f"Saved {result.get('saved')} spot(s)"
                + (f", dropped the oldest {len(result.get('pruned') or [])}" if result.get('pruned') else '')
                + (f" — keeper kept by {result.get('fidelity')}" if result.get('fidelity') else '')
                + f". /song back {_short(project.get('project_id'))} to return here.")

    if verb == 'saves':
        rows = client.spots(project['project_id']).get('checkpoints') or []
        if not rows:
            return f"{project.get('title') or 'Untitled'} has no saved spots yet. /song save"
        lines = [f"{len(rows)} saved spot(s):"]
        for row in rows[-8:]:
            when = time.strftime('%d %b %H:%M', time.localtime(row.get('at') or 0))
            lines.append(f"  {_short(row.get('checkpoint_id'))}  {when}"
                         f"  {row.get('label') or '(no label)'}"
                         f"  · keeper {_short(row.get('kept_take_id')) or '—'}"
                         f"  · {row.get('fidelity') or ''}")
        return '\n'.join(lines)

    if verb == 'back':
        wanted = rest[0] if rest else ''
        if not wanted:
            return 'Name the spot: /song back <id> (see /song saves)'
        rows = client.spots(project['project_id']).get('checkpoints') or []
        hit = next((r for r in rows if str(r.get('checkpoint_id', '')).startswith(wanted)), None)
        if hit is None:
            return f"No saved spot starts with {wanted!r}. /song saves"
        result = client.back_to(project['project_id'], hit['checkpoint_id'])
        return (f"Back to {result.get('restored')}. The draft is restored and the approval is revoked —"
                f" review it, then /song go to render again.")

    if verb == 'check':
        take = _newest_finished(project)
        if take is None:
            return f"{project.get('title') or 'Untitled'} has nothing finished to check yet."
        result = client.check(project['project_id'], take['take_id'],
                              retry=any(w in ('retry', 'again') for w in rest))
        lines = [f"{result.get('state', 'unknown')}: {result.get('summary', '')}"]
        for row in result.get('checks') or []:
            mark = {True: 'ok', False: 'FAILED', None: 'not measured'}[row.get('ok')]
            lines.append(f"  [{mark}] {row.get('name')} — {row.get('detail')}")
        if result.get('retried'):
            lines.append(f"  retried with seed {result.get('seed')} because: "
                         f"{', '.join(result.get('because') or [])}")
        return '\n'.join(lines)

    if verb == 'keep':
        take = _newest_finished(project)
        if take is None:
            return (f"{project.get('title') or 'Untitled'} has nothing finished to keep yet.")
        result = client.keep(project['project_id'], take['take_id'])
        return (f"Kept {_short(result.get('kept_take_id') or take['take_id'])} as the take this song "
                f"remembers. It survives a reload; nothing was re-rendered.")

    if verb == 'go':
        confirm = any(word.lower() in ('now', 'confirm') for word in rest)
        if not confirm:
            _shown[project['project_id']] = project.get('updated_at') or 0
            return ('This is what will be rendered:\n' + describe(project)
                    + f"\n\nNothing has been queued. To approve this version and render one take, say:"
                    f"\n  /song go {_short(project.get('project_id'))} now")

        token = project.get('updated_at') or 0
        if _shown.get(project['project_id']) != token:
            return ('That draft changed since I showed it, so it is not the version you reviewed.'
                    '\nRun /song go ' + _short(project.get('project_id')) + ' to see it again.')
        if not project.get('approved'):
            client.approve(project['project_id'], token)
        queued = client.queue(project['project_id'])
        take_id = (queued.get('take') or {}).get('take_id')
        return (f"Queued {_short(take_id)} on {queued.get('engine') or project.get('engine') or 'the engine'}"
                f" — the render runs in the background; this chat stays usable."
                f"\n  /song takes {_short(project.get('project_id'))} to check on it")

    return USAGE


def _newest_finished(project):
    finished = [t for t in (project.get('takes') or []) if t.get('state') == 'succeeded']
    return finished[-1] if finished else None
