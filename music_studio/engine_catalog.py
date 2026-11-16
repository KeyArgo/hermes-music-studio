"""Detect which music models a machine can run, without rendering anything.

A server is not "ready" just because it answers: the node pack and the weight
files it names both have to be there. This module holds one signature per music
model the studio ships a proven graph for, reads ComfyUI's ``/object_info``
(node schemas *and* the option lists its loaders carry) and reports, per model,
``ready``, ``missing_nodes``, ``missing_files`` or ``unknown`` when a loader's
option list cannot be read at all.

Only two models have a verified signature. YuE 2 and Stable Audio have no class
names in this repo, so this studio says it cannot tell rather than guessing.

Nothing here downloads or installs anything, and every address comes from the
caller. Detection is one GET after another to that address and nothing else.
"""
from __future__ import annotations

import json
import urllib.error
from pathlib import Path

from . import setup
from .engines.minimax_m3_comfy import (LOADER_NODES, MINIMAX_NODES, MODEL_FILES,
                                       SAMPLER_NODES, _combo_options)
from .network import TargetStore, validate_base_url
from .store import Store
from .workflows import WorkflowStore, engine_id, validate_workflow

TEMPLATES_DIR = Path(__file__).resolve().parent / 'templates'
TEMPLATE_FILES = {'ace15_turbo': 'ace15-turbo.api.json'}

ACE_NODES = ('TextEncodeAceStepAudio1.5', 'EmptyAceStep1.5LatentAudio',
             'DualCLIPLoader', 'UNETLoader', 'VAELoader')
# Every class_type the shipped ACE template submits. ACE_NODES is the
# model-specific head of it; a server with the weights and the node pack but
# no SaveAudio still fails every render, so readiness covers the whole graph.
# ``test_the_ace_signature_is_exactly_the_shipped_template`` keeps this and
# ``templates/ace15-turbo.api.json`` from drifting apart.
ACE_GRAPH_NODES = (*ACE_NODES, 'ModelSamplingAuraFlow', 'ConditioningZeroOut',
                   'KSampler', 'VAEDecodeAudio', 'SaveAudio')
# Same rule for the graph the MiniMax engine builds (untiled decode: the tiled
# variant is an operator option, not what a stock server needs).
MINIMAX_GRAPH_NODES = (*MINIMAX_NODES, *LOADER_NODES, *SAMPLER_NODES)

SIGNATURES = {
    'minimax_m3': {
        'label': 'MiniMax Music 3',
        'nodes': list(MINIMAX_GRAPH_NODES),
        'files': [
            ('UNETLoader', 'unet_name', MODEL_FILES['unet'].rsplit('/', 1)[-1]),
            ('CLIPLoader', 'clip_name', MODEL_FILES['clip'].rsplit('/', 1)[-1]),
            ('VAELoader', 'vae_name', MODEL_FILES['vae'].rsplit('/', 1)[-1]),
        ],
        'clip_type': ('CLIPLoader', 'type', 'minimax'),
    },
    'ace15_turbo': {
        'label': 'ACE-Step 1.5',
        'nodes': list(ACE_GRAPH_NODES),
        'files': [
            ('UNETLoader', 'unet_name', 'acestep_v1.5_turbo.safetensors'),
            ('DualCLIPLoader', 'clip_name1', 'qwen_0.6b_ace15.safetensors'),
            ('DualCLIPLoader', 'clip_name2', 'qwen_4b_ace15.safetensors'),
            ('VAELoader', 'vae_name', 'ace_1.5_vae.safetensors'),
        ],
        'clip_type': ('DualCLIPLoader', 'type', 'ace'),
    },
}

# The one model id that needs a saved workflow, and the name ``add_engine``
# gives it. Export uses both to tell "this target can run ACE-Step 1.5".
ACE_WORKFLOW_MODEL = 'ace15_turbo'
ACE_WORKFLOW_NAME = 'ACE-Step 1.5'
DEFAULT_MODEL = 'minimax_m3'
EXPORT_VERSION = 1
MAX_IMPORT_ENGINES = 50
OBJECT_INFO_TIMEOUT_S = 30
BAD_EXPORT_MESSAGE = 'That text is not a list of music engines.'
_WRONG_NODE_LIST = ('That address answered, but it did not list its nodes as ComfyUI does. '
                    'Check that this is ComfyUI and not another service.')

UNDETECTABLE_NOTE = 'This studio cannot tell yet if YuE 2 or Stable Audio is installed.'

READY = 'ready'
MISSING_NODES = 'missing_nodes'
MISSING_FILES = 'missing_files'
UNKNOWN = 'unknown'


def _basename(name) -> str:
    """The file name a loader lists, whichever folder prefix it carries.

    ComfyUI shows ``vae/ace_1.5_vae.safetensors`` on one machine and a bare
    name on another, and Windows may write the separator the other way round.
    """
    return str(name).replace('\\', '/').rsplit('/', 1)[-1]


def detect(object_info) -> list[dict]:
    """One row per known model: what this server has, and what it is missing.

    ``resolved`` maps a bare file name to the exact loader option that matched
    it, but only when the server lists it under a folder (``sub/qwen_…`` or a
    Windows path). The template names files bare, and a bare name is not a
    valid option when the file lives in a subfolder, so the caller needs the
    exact string to write into the graph.
    """
    info = object_info if isinstance(object_info, dict) else {}
    rows = []
    for signature_id, signature in SIGNATURES.items():
        missing_nodes = [node for node in signature['nodes'] if node not in info]
        missing_files: list[str] = []
        resolved: dict[str, str] = {}
        status = MISSING_NODES if missing_nodes else READY

        if not missing_nodes:
            for node, field, filename in signature['files']:
                options = _combo_options(info, node, field)
                if options is None:
                    status = UNKNOWN
                    break
                wanted = _basename(filename)
                match = next((option for option in options
                              if _basename(option) == wanted), None)
                if match is None:
                    missing_files.append(wanted)
                elif match != wanted:
                    resolved[wanted] = match

            clip_type = signature.get('clip_type')
            if status != UNKNOWN and clip_type:
                node, field, value = clip_type
                options = _combo_options(info, node, field)
                if options is None:
                    status = UNKNOWN
                elif value not in options:
                    missing_files.append(f'{node}.{field}={value}')

            if status != UNKNOWN:
                status = MISSING_FILES if missing_files else READY

        rows.append({
            'id': signature_id,
            'label': signature['label'],
            'status': status,
            'missing_nodes': missing_nodes,
            'missing_files': missing_files,
            'resolved': resolved,
        })
    return rows


def _model_sentence(row: dict) -> str:
    label = row['label']
    if row['status'] == READY:
        return f'{label} is ready.'
    if row['status'] == MISSING_NODES:
        count = len(row['missing_nodes'])
        word = 'node' if count == 1 else 'nodes'
        return f'{label} is missing {count} {word}.'
    if row['status'] == MISSING_FILES:
        names = row['missing_files']
        if len(names) == 1:
            return f'{label} is missing {names[0]}.'
        return f'{label} is missing {len(names)} files: {", ".join(names)}.'
    return f'This studio cannot tell whether {label} is installed.'


def _problem(kind, url, problem, message, checks=None, models=None) -> dict:
    return {'ok': False, 'kind': kind, 'url': url, 'problem': problem,
            'message': message, 'models': models or [], 'checks': checks or []}


def _check(name, ok, detail='') -> dict:
    return setup._check(name, ok, detail)


def _no_answer(kind, url, checks):
    return _problem(
        kind, url, 'no_answer',
        f'Nothing answered at {url}. Check the address, the other computer\'s firewall, '
        'and that the server was started so other machines can connect (--listen).',
        checks)


def _not_comfy(base, checks, message=None) -> dict:
    return _problem(
        'comfyui', base, 'not_comfy',
        message or ('That address answered, but it is not ComfyUI. Check the port and the path.'),
        checks)


def test_server(kind, url) -> dict:
    """Ask one address what it is, and which music models it can run.

    ``kind`` is ``comfyui``, ``ace_server`` or ``hosted``. Nothing is saved. A
    failure carries a plain troubleshooting key in ``problem`` so the page can
    show the matching help card.

    The address passes the same rule ``add_engine`` applies before any request
    is made, so a test never probes something Add would then refuse — a
    link-local address least of all, since that is where metadata services live.
    """
    if kind == 'hosted':
        return _problem(
            'hosted', url, 'use_hosted_form',
            'Hosted services are set up with an address and an API key, not by testing.')

    if kind not in ('comfyui', 'ace_server'):
        raise ValueError(f'unknown engine kind: {kind}')

    try:
        base = validate_base_url(url)
    except ValueError as exc:
        return _problem(
            kind, (url or '').strip() if isinstance(url, str) else '', 'bad_address',
            'That address is not valid. Include http:// and the port, like '
            f'http://192.168.1.50:8188. ({exc})')

    checks: list[dict] = []
    if kind == 'ace_server':
        return _test_ace_server(base, checks)
    return _test_comfyui(base, checks)


def _test_comfyui(base, checks) -> dict:
    """A body that is not JSON, or has no ``system`` object, means the wrong service.

    Only a timeout or a connection error is "nothing answered": a server that
    replied with HTML is answering, just not as ComfyUI.
    """
    try:
        raw = setup._get(f'{base}/system_stats')
    except urllib.error.HTTPError as exc:
        checks.append(_check('server answers', False, f'HTTP {exc.code}'))
        return _not_comfy(base, checks)
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        checks.append(_check('server answers', False, exc))
        return _no_answer('comfyui', base, checks)

    try:
        stats = json.loads(raw)
    except (TypeError, ValueError):
        checks.append(_check('server answers', False, 'unexpected /system_stats body'))
        return _not_comfy(base, checks)
    if not isinstance(stats, dict) or not isinstance(stats.get('system'), dict):
        checks.append(_check('server answers', False, 'unexpected /system_stats body'))
        return _not_comfy(base, checks)
    version = stats['system'].get('comfyui_version') or 'ComfyUI'
    checks.append(_check('server answers', True, version))

    try:
        # A big install lists thousands of nodes; 8 s called a good server bad.
        raw = setup._get(f'{base}/object_info', timeout=OBJECT_INFO_TIMEOUT_S)
    except urllib.error.HTTPError as exc:
        checks.append(_check('node list', False, f'HTTP {exc.code}'))
        return _not_comfy(base, checks, _WRONG_NODE_LIST)
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        checks.append(_check('node list', False, exc))
        return _no_answer('comfyui', base, checks)
    try:
        info = json.loads(raw)
    except (TypeError, ValueError):
        checks.append(_check('node list', False, 'unexpected /object_info body'))
        return _not_comfy(base, checks, _WRONG_NODE_LIST)
    if not isinstance(info, dict) or not info:
        checks.append(_check('node list', False, 'unexpected /object_info body'))
        return _not_comfy(base, checks, _WRONG_NODE_LIST)
    checks.append(_check('node list', True, f'{len(info)} nodes'))

    models = detect(info)
    for row in models:
        checks.append(_check(row['label'], row['status'] == READY,
                             row['status'] if row['status'] != READY else 'ready'))
    message = 'This is ComfyUI. ' + ' '.join(_model_sentence(row) for row in models)
    return {'ok': True, 'kind': 'comfyui', 'url': base,
            'message': message, 'models': models, 'checks': checks}


def _test_ace_server(base, checks) -> dict:
    try:
        payload = json.loads(setup._get(f'{base}/health'))
    except urllib.error.HTTPError as exc:
        checks.append(_check('/health', False, f'HTTP {exc.code}'))
        return _problem(
            'ace_server', base, 'not_ace',
            'That address answered, but it is not an ACE-Step server (its /health did not '
            'answer).', checks)
    except (urllib.error.URLError, OSError, TimeoutError, ValueError) as exc:
        checks.append(_check('/health', False, exc))
        return _no_answer('ace_server', base, checks)

    if not isinstance(payload, dict):
        checks.append(_check('/health', False, 'unexpected /health body'))
        return _problem(
            'ace_server', base, 'not_ace',
            'That address answered, but it did not answer like an ACE-Step server.', checks)

    data = payload.get('data') if isinstance(payload.get('data'), dict) else {}
    checks.append(_check('server answers', True, data.get('loaded_model') or 'ACE-Step'))
    checks.append(_check('/health', True, 'JSON'))
    message = 'This is an ACE-Step server.'
    if data.get('loaded_model'):
        message += f' It has {data["loaded_model"]} loaded.'
    return {'ok': True, 'kind': 'ace_server', 'url': base,
            'message': message, 'models': [], 'checks': checks}


def load_template(model_id) -> dict:
    """The proven API graph for one bundled model, as a validated workflow."""
    try:
        filename = TEMPLATE_FILES[model_id]
    except KeyError:
        raise ValueError(f'unknown music model: {model_id}') from None
    path = TEMPLATES_DIR / filename
    try:
        graph = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise ValueError(f'the {model_id} template could not be read: {exc}') from None
    return validate_workflow(graph)


def _workflow_summary(record: dict) -> dict:
    return {'id': record['id'], 'name': record['name'],
            'engine_id': engine_id(record['id'])}


def _ace_template(resolved) -> dict:
    """The shipped ACE graph, with the loader options this server actually lists.

    ``resolved`` maps a bare file name to the exact option that matched it
    (``{'qwen_0.6b_ace15.safetensors': 'sub/qwen_0.6b_ace15.safetensors'}``).
    A bare name is not a valid option when the file sits in a subfolder, and the
    server would refuse the prompt.
    """
    graph = load_template(ACE_WORKFLOW_MODEL)
    lookup = {}
    if isinstance(resolved, dict):
        for bare, exact in resolved.items():
            # Only a subfolder spelling of the same file: never a different file.
            if (isinstance(bare, str) and isinstance(exact, str) and exact.strip()
                    and exact.replace('\\', '/').rsplit('/', 1)[-1] == bare):
                lookup[bare] = exact
    if not lookup:
        return graph
    for node in graph.values():
        if not isinstance(node, dict) or 'Loader' not in str(node.get('class_type')):
            continue
        inputs = node.get('inputs')
        if not isinstance(inputs, dict):
            continue
        for field, value in list(inputs.items()):
            if isinstance(value, str):
                exact = lookup.get(_basename(value))
                if exact:
                    inputs[field] = exact
    return graph


def _engine_models(data_dir) -> dict[str, list[str]]:
    """Which music models each saved target can run, from what was saved for it.

    MiniMax needs no record (the engine builds its graph). ACE-Step 1.5 is
    known by the workflow ``add_engine`` created for it.
    """
    workflows = WorkflowStore(data_dir).all()
    models = {}
    for target in TargetStore(data_dir).list():
        offered = []
        for record in workflows:
            if record['target_id'] != target['id'] or not _is_ace_workflow(record):
                continue
            if ACE_WORKFLOW_MODEL not in offered:
                offered.append(ACE_WORKFLOW_MODEL)
        offered.append(DEFAULT_MODEL)
        models[target['id']] = offered
    return models


def _is_ace_workflow(record: dict) -> bool:
    """True for the workflow ``add_engine`` writes, not a user's lookalike.

    The name alone is not enough (a person may name their own graph that), so
    the class types must match the shipped template too.
    """
    if record.get('name') != ACE_WORKFLOW_NAME:
        return False
    graph = record.get('workflow')
    if not isinstance(graph, dict):
        return False
    classes = {node.get('class_type') for node in graph.values() if isinstance(node, dict)}
    return classes == set(SIGNATURES[ACE_WORKFLOW_MODEL]['nodes'])


def add_engine(data_dir, *, name, url, models, kind='comfyui', resolved=None) -> dict:
    """Save one server and register a workflow for each ready model it offers.

    MiniMax Music 3 needs no workflow: its graph is built by the engine. ACE-Step
    1.5 runs the proven template, saved as a workflow on this target so the song
    page lists a model name, never a file.

    Only a ComfyUI server is added here: an ACE-Step server and a hosted service
    have their own forms (and a hosted key is never part of this record). If the
    workflow cannot be written the new target is removed again, so a failed Add
    leaves nothing behind for the retry to duplicate.
    """
    if kind != 'comfyui':
        raise ValueError('Use the ACE-Step or hosted form.')
    if not isinstance(models, (list, tuple)) or not models:
        raise ValueError('models must be a non-empty list of music model ids')
    for model in models:
        if not isinstance(model, str):
            raise ValueError('models must be a non-empty list of music model ids')
    unknown = [model for model in models if model not in SIGNATURES]
    if unknown:
        raise ValueError(f'unknown music model: {unknown[0]}')

    targets = TargetStore(data_dir)
    target = targets.create(name, url)
    workflows = []
    seen = set()
    try:
        for model in models:
            if model in seen:
                continue
            seen.add(model)
            if model == ACE_WORKFLOW_MODEL:
                record = WorkflowStore(data_dir).create(
                    ACE_WORKFLOW_NAME, target['id'], _ace_template(resolved))
                workflows.append(_workflow_summary(record))
    except Exception:
        # The target is saved before the graph because the store needs its id.
        # A half-added engine would show up as a workflow-less server that a
        # retry duplicates, so it goes back out.
        try:
            targets.delete(target['id'])
        except (KeyError, ValueError):
            pass
        raise
    return {'ok': True, 'target': target, 'workflows': workflows}


def list_engines(data_dir) -> dict:
    """Every saved engine, plus the ACE and hosted endpoints. Never probes."""
    targets = TargetStore(data_dir).list()
    saved = WorkflowStore(data_dir).list()
    engines = []
    for target in targets:
        engines.append({
            'id': target['id'],
            'kind': 'comfyui',
            'name': target['name'],
            'url': target['url'],
            'workflows': [_workflow_summary(record) for record in saved
                          if record['target_id'] == target['id']],
        })
    return {'engines': engines,
            'ace_remote_url': setup._stored_endpoint(data_dir)['url'],
            'hosted': setup.list_cloud(data_dir)}


def _engine_is_being_used(data_dir, workflow_engines, target_id) -> bool:
    """True when a queued or running take still needs this engine.

    A take pins two things: the workflow engine id it was queued on
    (``comfy_workflow:<id>``, which the worker resolves from the store) and, in
    its snapshot, the target it was preflighted against. Deleting either record
    under a live take turns a render into "unknown workflow" / "unknown target".
    """
    for project in Store(data_dir).all():
        for take in project.get('takes') or []:
            if not isinstance(take, dict) or take.get('state') not in ('queued', 'running'):
                continue
            if take.get('engine_id') in workflow_engines:
                return True
            snapshot = take.get('snapshot')
            if take.get('comfy_target_id') == target_id:
                return True
            if isinstance(snapshot, dict) and snapshot.get('comfy_target_id') == target_id:
                return True
    return False


def remove_engine(data_dir, target_id) -> dict:
    """Delete an engine's workflows and then the engine itself.

    The workflows go first because the target cannot be deleted while a
    workflow still names it, and the target is deleted last so a failure
    half-way leaves a readable record rather than orphans.
    """
    target = TargetStore(data_dir).get(target_id)
    store = WorkflowStore(data_dir)
    records = [record for record in store.all() if record['target_id'] == target['id']]
    if _engine_is_being_used(data_dir, {engine_id(r['id']) for r in records}, target['id']):
        raise ValueError('This engine is used by a song that is still being made.')
    removed = []
    for record in records:
        store.delete(record['id'])
        removed.append(record['id'])
    TargetStore(data_dir).delete(target['id'])
    return {'ok': True, 'target_id': target['id'], 'workflows': removed}


# -- carrying the engines to another computer ------------------------------
# Export is a handover, not a backup: only what another machine needs to reach
# the same servers. Keys live in Hermes' credential store, hosted endpoints are
# set up per machine, and no local folder is meaningful on the other side.

def _save_ace_server(data_dir, url) -> None:
    """Save the ACE server address the way ``POST /studio/setup/ace`` does.

    That route ends at ``settings.write`` (through ``service.save_settings``);
    this writes the same key into the data dir the caller named, so an import
    into a fresh profile does not reach for the running profile's settings.
    """
    from . import settings as studio_settings

    studio_settings.write(Path(data_dir), {'ace_remote_url': url})


def export_engines(data_dir) -> dict:
    """The saved engines as pretty JSON text, for another computer."""
    models = _engine_models(data_dir)
    engines = [{'name': target['name'], 'url': target['url'],
                'models': models[target['id']]}
               for target in TargetStore(data_dir).list()]
    payload = {'music_studio_engines': EXPORT_VERSION,
               'engines': engines,
               'ace_server': setup._stored_endpoint(data_dir)['url'] or None}
    return {'text': json.dumps(payload, indent=2)}


def _parse_export(text) -> dict:
    """The engines an export lists, with every address already validated.

    Validating the whole list before anything is saved is what keeps a bad
    entry from leaving half an import behind.
    """
    if not isinstance(text, str) or not text.strip():
        raise ValueError(BAD_EXPORT_MESSAGE)
    try:
        data = json.loads(text)
    except ValueError:
        raise ValueError(BAD_EXPORT_MESSAGE) from None
    if not isinstance(data, dict) or not isinstance(data.get('engines'), list):
        raise ValueError(BAD_EXPORT_MESSAGE)
    if len(data['engines']) > MAX_IMPORT_ENGINES:
        raise ValueError(f'that text lists more than {MAX_IMPORT_ENGINES} engines; '
                         'import them in smaller groups')
    engines = []
    for entry in data['engines']:
        if not isinstance(entry, dict) or not isinstance(entry.get('url'), str):
            raise ValueError(BAD_EXPORT_MESSAGE)
        name = entry.get('name')
        models = entry.get('models')
        engines.append({
            'name': name.strip() if isinstance(name, str) and name.strip() else entry['url'],
            'url': validate_base_url(entry['url']),
            'models': [model for model in models if isinstance(model, str)]
                      if isinstance(models, list) else [],
        })
    ace = data.get('ace_server')
    return {'engines': engines,
            'ace_server': validate_base_url(ace) if isinstance(ace, str) and ace.strip() else None}


def import_engines(data_dir, text) -> dict:
    """Rebuild the engines an export lists; never overwrite what is already here.

    An engine is "already here" when its normalised address is saved, whatever
    it is called. The ACE server address is only written when this machine has
    none, so importing a handover cannot silently retarget a working setup.
    """
    parsed = _parse_export(text)
    existing = set()
    for target in TargetStore(data_dir).list():
        try:
            existing.add(validate_base_url(target['url']))
        except ValueError:
            existing.add(target['url'])
    added, skipped, errors = [], [], []
    for entry in parsed['engines']:
        if entry['url'] in existing:
            skipped.append(entry['name'])
            continue
        models = [model for model in entry['models'] if model in SIGNATURES] or [DEFAULT_MODEL]
        try:
            add_engine(data_dir, name=entry['name'], url=entry['url'], models=models)
        except (ValueError, KeyError, OSError) as exc:
            errors.append(f'{entry["name"]}: {exc}')
            continue
        existing.add(entry['url'])
        added.append(entry['name'])
    if parsed['ace_server'] and not setup._stored_endpoint(data_dir)['url']:
        try:
            _save_ace_server(data_dir, parsed['ace_server'])
        except (ValueError, OSError) as exc:
            errors.append(f'ACE server: {exc}')
    return {'added': added, 'skipped': skipped, 'errors': errors}
