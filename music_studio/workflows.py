"""User-supplied ComfyUI workflows ("bring your own graph").

A workflow record is a ComfyUI **Export (API)** graph plus a name and the saved
endpoint (``comfy-targets.json`` target id) it runs on. The store lives beside
``comfy-targets.json`` and uses the same lock + atomic-write pattern, because the
dashboard process, the detached worker and the standalone server all read it.

The graph is data, never code: it must be a dict of nodes that each carry a
``class_type`` and ``inputs`` dict, and exactly one node must be an audio save
node (``class_type`` starting with ``SaveAudio``). Placeholders are the only way
a take's values reach the graph.
"""
from __future__ import annotations

import copy
import json
import os
import re
import tempfile
import time
import uuid
from pathlib import Path

from .network import _lock_ex, _lock_un, _valid_id, validate_name
from .store import replace_with_retry

WORKFLOWS_FILE = 'comfy-workflows.json'
MAX_WORKFLOW_BYTES = 1024 * 1024

ENGINE_PREFIX = 'comfy_workflow:'
PLACEHOLDERS = ('lyrics', 'style', 'duration', 'seed', 'title', 'instrumental')
PLACEHOLDER_TOKENS = tuple('{{' + name + '}}' for name in PLACEHOLDERS)
REQUIRED_ONE_OF = ('style', 'lyrics')

# An arbitrary graph does not declare a model window. Keep the studio's broad
# single-take window; the graph decides what it actually renders.
WORKFLOW_LIMITS = (10, 360, 120)


def engine_id(workflow_id: str) -> str:
    return ENGINE_PREFIX + str(workflow_id)


def is_workflow_engine(value) -> bool:
    return isinstance(value, str) and value.startswith(ENGINE_PREFIX)


def workflow_id_from_engine(value: str) -> str:
    if not is_workflow_engine(value):
        raise ValueError('not a workflow engine id')
    return value[len(ENGINE_PREFIX):]


def valid_engine_id(value) -> bool:
    """True for ``comfy_workflow:<uuid>`` with a real UUID."""
    if not is_workflow_engine(value):
        return False
    try:
        raw = workflow_id_from_engine(value)
        return str(uuid.UUID(raw)) == raw
    except (ValueError, AttributeError, TypeError):
        return False


def _is_link(value) -> bool:
    """True for a ComfyUI link: exactly ``[node_id: str, output_index: int]``."""
    return (isinstance(value, list) and len(value) == 2
            and isinstance(value[0], str) and type(value[1]) is int)


def _walk_strings(value):
    """Yield every string value inside a graph input (including nested lists)."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        if _is_link(value):
            return
        for item in value:
            yield from _walk_strings(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from _walk_strings(item)


def find_placeholders(graph: dict) -> tuple[str, ...]:
    """The placeholder names that appear anywhere in the graph's inputs.

    A placeholder is found when it is an entire string value or part of a longer
    one; both are replaced at render time.
    """
    found: set[str] = set()
    for node in (graph or {}).values():
        if not isinstance(node, dict):
            continue
        for text in _walk_strings(node.get('inputs')):
            for name, token in zip(PLACEHOLDERS, PLACEHOLDER_TOKENS):
                if token in text:
                    found.add(name)
    return tuple(name for name in PLACEHOLDERS if name in found)


def _save_nodes(graph: dict) -> list[tuple[str, dict]]:
    return [(node_id, node) for node_id, node in graph.items()
            if isinstance(node, dict)
            and isinstance(node.get('class_type'), str)
            and node['class_type'].startswith('SaveAudio')]


def validate_workflow(graph) -> dict:
    """Validate an uploaded API graph; return it unchanged on success."""
    if not isinstance(graph, dict) or not graph:
        raise ValueError('workflow must be a non-empty dict of nodes')
    for node_id, node in graph.items():
        if not isinstance(node, dict):
            raise ValueError(f'workflow node {node_id!r} must be an object')
        if not isinstance(node.get('class_type'), str) or not node['class_type'].strip():
            raise ValueError(f'workflow node {node_id!r} has no class_type')
        if not isinstance(node.get('inputs'), dict):
            raise ValueError(f'workflow node {node_id!r} has no inputs object')
    saves = _save_nodes(graph)
    if not saves:
        raise ValueError('workflow has no SaveAudio node')
    if len(saves) > 1:
        raise ValueError('workflow has more than one SaveAudio node')
    found = find_placeholders(graph)
    if not any(name in found for name in REQUIRED_ONE_OF):
        shown = ', '.join('{{' + name + '}}' for name in found) or 'none'
        raise ValueError('workflow must use {{style}} or {{lyrics}} '
                         f'(placeholders found: {shown})')
    return graph


def _placeholder_values(request, title=None):
    instrumental = bool(getattr(request, 'instrumental', False))
    lyrics = '' if instrumental else (getattr(request, 'lyrics', '') or '')
    try:
        duration = float(getattr(request, 'duration_seconds', 0) or 0)
    except (TypeError, ValueError):
        duration = 0.0
    return {
        'lyrics': lyrics,
        'style': getattr(request, 'caption', '') or '',
        'duration': int(duration) if duration.is_integer() else duration,
        'seed': int(getattr(request, 'seed', 0) or 0),
        'title': (title if title is not None else getattr(request, 'title', '') or ''),
        'instrumental': instrumental,
    }


_PLACEHOLDER_PATTERN = re.compile(
    r"\{\{(lyrics|style|duration|seed|title|instrumental)\}\}")


def _replace_text(text: str, values: dict) -> str:
    # One pass, so a value that itself contains ``{{...}}`` is never rewritten afterwards.
    return _PLACEHOLDER_PATTERN.sub(lambda match: str(values[match.group(1)]), text)


def _replace_value(value, values: dict):
    if isinstance(value, str):
        if value in PLACEHOLDER_TOKENS:
            return values[value[2:-2]]
        return _replace_text(value, values)
    if _is_link(value):
        return value
    if isinstance(value, list):
        return [_replace_value(item, values) for item in value]
    if isinstance(value, dict):
        return {key: _replace_value(item, values) for key, item in value.items()}
    return value


def substitute_placeholders(graph: dict, request, title=None) -> dict:
    """Return a copy of ``graph`` with the take's values in every placeholder.

    An input string that is *exactly* a placeholder becomes the take's typed
    value (float seconds, int seed, bool instrumental); a placeholder inside a
    longer string becomes text. The single audio save node's filename_prefix is
    always forced to ``music-studio/workflow``.
    """
    values = _placeholder_values(request, title=title)
    result = copy.deepcopy(graph)
    for node in result.values():
        if isinstance(node, dict) and isinstance(node.get('inputs'), dict):
            node['inputs'] = _replace_value(node['inputs'], values)
    saves = _save_nodes(result)
    if saves:
        saves[0][1].setdefault('inputs', {})['filename_prefix'] = 'music-studio/workflow'
    return result


class WorkflowStore:
    """Persist named ComfyUI workflows in ``root/comfy-workflows.json``."""

    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / WORKFLOWS_FILE

    def _lock(self):
        return (self.root / '.comfy-workflows.lock').open('a')

    def _read(self):
        try:
            data = json.loads(self.path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return []
        if not isinstance(data, list):
            return []
        return [item for item in data
                if isinstance(item, dict) and isinstance(item.get('id'), str)
                and isinstance(item.get('name'), str) and item.get('name')
                and isinstance(item.get('target_id'), str)
                and isinstance(item.get('workflow'), dict)]

    def _write(self, workflows):
        fd, name = tempfile.mkstemp(dir=self.root, prefix='.comfy-workflows-')
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as out:
                json.dump(workflows, out, ensure_ascii=False, indent=2)
                out.flush()
                os.fsync(out.fileno())
            replace_with_retry(name, self.path)
            if os.name != 'nt':
                fd = os.open(self.root, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def _summary(self, record):
        return {'id': record['id'], 'name': record['name'],
                'target_id': record['target_id'],
                'placeholders': list(find_placeholders(record.get('workflow') or {})),
                'created_at': record.get('created_at')}

    def create(self, name, target_id, workflow):
        name = validate_name(name)
        target_id = _valid_id(target_id)
        if isinstance(workflow, str):
            try:
                workflow = json.loads(workflow)
            except ValueError:
                raise ValueError('workflow is not valid JSON') from None
        validate_workflow(workflow)
        encoded = json.dumps(workflow, ensure_ascii=False,
                             separators=(',', ':')).encode('utf-8')
        if len(encoded) > MAX_WORKFLOW_BYTES:
            raise ValueError(f'workflow is larger than {MAX_WORKFLOW_BYTES // 1024} KiB')
        from .network import TargetStore
        try:
            TargetStore(self.root).get(target_id)
        except (KeyError, ValueError):
            raise ValueError(f'unknown target: {target_id}') from None
        record = {'id': str(uuid.uuid4()), 'name': name, 'target_id': target_id,
                  'workflow': copy.deepcopy(workflow), 'created_at': time.time()}
        with self._lock() as lock:
            _lock_ex(lock)
            try:
                workflows = self._read()
                workflows.append(record)
                self._write(workflows)
            finally:
                _lock_un(lock)
        return copy.deepcopy(record)

    def all(self):
        """Every full record, including the graph (internal use)."""
        with self._lock() as lock:
            _lock_ex(lock)
            try:
                return copy.deepcopy(self._read())
            finally:
                _lock_un(lock)

    def list(self):
        """Summaries for the API/UI; the graph stays out of /status-sized payloads."""
        return [self._summary(record) for record in self.all()]

    def get(self, workflow_id):
        workflow_id = _valid_id(workflow_id)
        for record in self.all():
            if record['id'] == workflow_id:
                return record
        raise KeyError(workflow_id)

    def delete(self, workflow_id):
        workflow_id = _valid_id(workflow_id)
        with self._lock() as lock:
            _lock_ex(lock)
            try:
                workflows = self._read()
                remaining = [w for w in workflows if w['id'] != workflow_id]
                if len(remaining) == len(workflows):
                    raise KeyError(workflow_id)
                self._write(remaining)
                return copy.deepcopy(next(w for w in workflows if w['id'] == workflow_id))
            finally:
                _lock_un(lock)
