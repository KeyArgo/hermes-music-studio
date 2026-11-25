"""Shared validation and model response handling for studio drafts."""

import threading

from music_studio import llm_bridge


# Handler registration can be repeated while background drafts are still running.
rpc_jobs = {}
rpc_jobs_lock = threading.Lock()
rpc_slots = threading.BoundedSemaphore(2)


class DraftError(Exception):
    def __init__(self, status, detail, commands=None):
        super().__init__(detail)
        self.status = status
        self.detail = detail
        self.commands = commands


def draft_song(body, llm_complete):
    """Validate a draft request, ask the model, and return the route's answer body.

    ``standalone.ideas`` is imported per call, as the route always did: Hermes shares one module table
    across profiles, so an older profile's copy can be the one loaded, and the ImportError that
    produces is what the route turns into "update this copy" instead of a bare 500.
    """
    from standalone.ideas import MAX_REPLY_CHARS, build_request, parse_draft

    prompt = str(body.get('prompt') or '').strip()
    if not prompt:
        raise DraftError(400, 'prompt is required')
    # The frame asks for 'chat' from "Discuss idea" and 'draft' from "Fill my draft"; the pane
    # forwards the choice. Anything else keeps the draft behaviour this route always had.
    action = 'chat' if body.get('action') == 'chat' else 'draft'
    try:
        # int() on a container or a word is the caller's mistake, not a crash: it raised TypeError
        # out of int({}) and escaped the ValueError branch below as a 500.
        style = int(body.get('style', 50))
    except (TypeError, ValueError, OverflowError):
        raise DraftError(422, 'style must be a whole number between 0 and 100') from None
    try:
        request = build_request(body.get('provider'), body.get('model'),
                                [{'role': 'user', 'content': prompt}], action,
                                body.get('tone'), style)
    except ValueError as exc:
        raise DraftError(422, str(exc)) from None
    try:
        content = llm_complete(request['messages'], provider=request['provider'],
                               model=request['model'], max_tokens=request['max_tokens'],
                               temperature=request['temperature'])
    except llm_bridge.LlmUnavailable as exc:
        raise DraftError(503, str(exc)) from None
    except llm_bridge.LlmTrustError as exc:
        raise DraftError(403, str(exc), commands=list(exc.commands)) from None
    except llm_bridge.LlmModelMissing as exc:
        # A setting the owner changes, not a passing fault: the page shows the command to change it.
        raise DraftError(502, str(exc), commands=list(exc.commands)) from None
    except llm_bridge.LlmFailed as exc:
        # The bridge wrote the sentence (a timeout, or the provider's own error), so it is the detail.
        raise DraftError(502, str(exc)) from None
    except RuntimeError as exc:
        # An empty or unusable host reply is an upstream failure, not a crash.
        raise DraftError(502, str(exc)) from None
    if action == 'chat':
        # The shape the standalone /studio/ideas route answers with: app.js reads `content` as
        # the chat reply and never expects a draft from it.
        if not content.strip():
            raise DraftError(502, 'Hermes returned no content')
        if len(content) > MAX_REPLY_CHARS:
            raise DraftError(502, 'Hermes returned an oversized response')
        return {'success': True, 'action': 'chat', 'content': content, 'written_by': 'ctx.llm'}
    try:
        draft = parse_draft(content)
    except ValueError:
        raise DraftError(502, 'Model returned an invalid draft') from None
    project = None
    if body.get('create'):
        from music_studio import service
        project = service.store().create(**draft)
    return {'success': True, 'draft': draft, 'written_by': 'ctx.llm', 'project': project}

