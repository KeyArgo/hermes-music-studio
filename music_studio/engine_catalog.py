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
from .engines.minimax_m3_comfy import MODEL_FILES, MINIMAX_NODES, _combo_options
from .network import TargetStore
from .workflows import WorkflowStore, engine_id, validate_workflow

TEMPLATES_DIR = Path(__file__).resolve().parent / 'templates'
TEMPLATE_FILES = {'ace15_turbo': 'ace15-turbo.api.json'}

ACE_NODES = ('TextEncodeAceStepAudio1.5', 'EmptyAceStep1.5LatentAudio',
             'DualCLIPLoader', 'UNETLoader', 'VAELoader')

SIGNATURES = {
    'minimax_m3': {
        'label': 'MiniMax Music 3',
        'nodes': list(MINIMAX_NODES),
        'files': [
            ('UNETLoader', 'unet_name', MODEL_FILES['unet'].rsplit('/', 1)[-1]),
            ('CLIPLoader', 'clip_name', MODEL_FILES['clip'].rsplit('/', 1)[-1]),
            ('VAELoader', 'vae_name', MODEL_FILES['vae'].rsplit('/', 1)[-1]),
        ],
        'clip_type': ('CLIPLoader', 'type', 'minimax'),
    },
    'ace15_turbo': {
        'label': 'ACE-Step 1.5',
        'nodes': list(ACE_NODES),
        'files': [
            ('UNETLoader', 'unet_name', 'acestep_v1.5_turbo.safetensors'),
            ('DualCLIPLoader', 'clip_name1', 'qwen_0.6b_ace15.safetensors'),
            ('DualCLIPLoader', 'clip_name2', 'qwen_4b_ace15.safetensors'),
            ('VAELoader', 'vae_name', 'ace_1.5_vae.safetensors'),
        ],
        'clip_type': ('DualCLIPLoader', 'type', 'ace'),
    },
}

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
    """One row per known model: what this server has, and what it is missing."""
    info = object_info if isinstance(object_info, dict) else {}
    rows = []
    for signature_id, signature in SIGNATURES.items():
        missing_nodes = [node for node in signature['nodes'] if node not in info]
        missing_files: list[str] = []
        status = MISSING_NODES if missing_nodes else READY

        if not missing_nodes:
            for node, field, filename in signature['files']:
                options = _combo_options(info, node, field)
                if options is None:
                    status = UNKNOWN
                    break
                listed = {_basename(option) for option in options}
                wanted = _basename(filename)
                if wanted not in listed:
                    missing_files.append(wanted)

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


def test_server(kind, url) -> dict:
    """Ask one address what it is, and which music models it can run.

    ``kind`` is ``comfyui``, ``ace_server`` or ``hosted``. Nothing is saved. A
    failure carries a plain troubleshooting key in ``problem`` so the page can
    show the matching help card.
    """
    if kind == 'hosted':
        return _problem(
            'hosted', url, 'use_hosted_form',
            'Hosted services are set up with an address and an API key, not by testing.')

    if kind not in ('comfyui', 'ace_server'):
        raise ValueError(f'unknown engine kind: {kind}')

    try:
        base = setup.normalise_url(url)
    except ValueError as exc:
        return _problem(
            kind, (url or '').strip(), 'bad_address',
            'That address is not valid. Include http:// and the port, like '
            f'http://192.168.1.50:8188. ({exc})')

    checks: list[dict] = []
    if kind == 'ace_server':
        return _test_ace_server(base, checks)
    return _test_comfyui(base, checks)


def _test_comfyui(base, checks) -> dict:
    try:
        stats = json.loads(setup._get(f'{base}/system_stats'))
    except urllib.error.HTTPError as exc:
        checks.append(_check('server answers', False, f'HTTP {exc.code}'))
        return _problem(
            'comfyui', base, 'not_comfy',
            'That address answered, but it is not ComfyUI. Check the port and the path.',
            checks)
    except (urllib.error.URLError, OSError, TimeoutError, ValueError) as exc:
        checks.append(_check('server answers', False, exc))
        return _no_answer('comfyui', base, checks)

    if not isinstance(stats, dict):
        checks.append(_check('server answers', False, 'unexpected /system_stats body'))
        return _problem(
            'comfyui', base, 'not_comfy',
            'That address answered, but it is not ComfyUI. Check the port and the path.',
            checks)
    version = (stats.get('system') or {}).get('comfyui_version') or 'ComfyUI'
    checks.append(_check('server answers', True, version))

    try:
        info = json.loads(setup._get(f'{base}/object_info'))
    except Exception as exc:
        checks.append(_check('node list', False, exc))
        return _problem(
            'comfyui', base, 'not_comfy',
            'That address answered, but it did not list its nodes as ComfyUI does. '
            'Check that this is ComfyUI and not another service.',
            checks)
    if not isinstance(info, dict) or not info:
        checks.append(_check('node list', False, 'unexpected /object_info body'))
        return _problem(
            'comfyui', base, 'not_comfy',
            'That address answered, but it did not list its nodes as ComfyUI does. '
            'Check that this is ComfyUI and not another service.',
            checks)
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


def add_engine(data_dir, *, name, url, models) -> dict:
    """Save one server and register a workflow for each ready model it offers.

    MiniMax Music 3 needs no workflow: its graph is built by the engine. ACE-Step
    1.5 runs the proven template, saved as a workflow on this target so the song
    page lists a model name, never a file.
    """
    if not isinstance(models, (list, tuple)):
        raise ValueError('models must be a list of music model ids')
    unknown = [model for model in models if model not in SIGNATURES]
    if unknown:
        raise ValueError(f'unknown music model: {unknown[0]}')

    target = TargetStore(data_dir).create(name, url)
    workflows = []
    seen = set()
    for model in models:
        if model in seen:
            continue
        seen.add(model)
        if model == 'ace15_turbo':
            record = WorkflowStore(data_dir).create(
                'ACE-Step 1.5', target['id'], load_template('ace15_turbo'))
            workflows.append(_workflow_summary(record))
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


def remove_engine(data_dir, target_id) -> dict:
    """Delete an engine's workflows and then the engine itself."""
    target = TargetStore(data_dir).get(target_id)
    store = WorkflowStore(data_dir)
    removed = []
    for record in store.list():
        if record['target_id'] == target['id']:
            store.delete(record['id'])
            removed.append(record['id'])
    TargetStore(data_dir).delete(target['id'])
    return {'ok': True, 'target_id': target['id'], 'workflows': removed}
