"""Render through a user-supplied ComfyUI API workflow.

The engine reuses the MiniMax adapter's HTTP, queue, history, download and
FLAC/M4A -> WAV code; the only new parts are the graph (the uploaded workflow
with its placeholders filled) and a health check that asks the server which node
types it actually has.
"""
from __future__ import annotations

import copy
import os
import time

from ..workflows import (WORKFLOW_LIMITS, _save_nodes, engine_id,
                         find_placeholders, substitute_placeholders,
                         validate_workflow)
from .contract import (Capabilities, EngineStatus, HealthCheck, Limits,
                       RenderRequest, RenderResult)
from .minimax_m3_comfy import MiniMaxM3ComfyEngine, _clean

WORKFLOW_CAPABILITIES = Capabilities(instrumental=True, vocal_language=False,
                                     repaint=False, continuation=False, batch=False)


class ComfyWorkflowEngine(MiniMaxM3ComfyEngine):
    """One uploaded ComfyUI graph, pinned to the endpoint it was saved with."""

    kind = 'remote'

    def __init__(self, workflow: dict, base_url: str | None = None,
                 timeout: float = 10.0, ffmpeg: str | None = None,
                 gpu_lease_path: str | os.PathLike | None = None):
        graph = workflow.get('workflow') if isinstance(workflow, dict) and 'workflow' in workflow else workflow
        validate_workflow(graph)
        super().__init__(base_url=base_url, timeout=timeout, ffmpeg=ffmpeg,
                         gpu_lease_path=gpu_lease_path)
        self.record = copy.deepcopy(workflow)
        self.workflow = copy.deepcopy(graph)
        self.workflow_id = workflow.get('id') or ''
        self.workflow_name = workflow.get('name') or 'Custom workflow'
        self.id = engine_id(self.workflow_id) if self.workflow_id else 'comfy_workflow'
        self.label = f'Custom workflow: {self.workflow_name}'
        self.requirements = ('ComfyUI with every node type this workflow uses',
                             f'the saved endpoint for "{self.workflow_name}"')
        self.save_node = _save_nodes(self.workflow)[0][0]

    @property
    def limits(self) -> Limits:
        minimum, maximum, recommended = WORKFLOW_LIMITS
        return Limits(min_seconds=minimum, max_seconds=maximum,
                      recommended_seconds=recommended)

    @property
    def capabilities(self) -> Capabilities:
        return WORKFLOW_CAPABILITIES

    def health(self) -> EngineStatus:
        started = time.monotonic()
        checks: list[HealthCheck] = []
        extra: dict = {'workflow_id': self.workflow_id,
                       'workflow_name': self.workflow_name,
                       'save_node': self.save_node,
                       'placeholders': list(find_placeholders(self.workflow))}

        def check(name, ok, detail=''):
            checks.append(HealthCheck(name, bool(ok), detail))
            return bool(ok)

        if not self.base_url:
            return EngineStatus(
                self.id, 'unavailable',
                'the workflow has no saved ComfyUI endpoint: add one in Settings -> Compute',
                [HealthCheck('base_url', False, 'not configured')], latency_ms=0,
                extra=extra)

        lease = self._gpu_lease_conflict()
        if lease:
            return EngineStatus(
                self.id, 'busy',
                f'local GPU is busy with an ACE render ({lease}); this ComfyUI '
                'host runs on the same machine', checks, latency_ms=0, extra=extra)
        check('gpu_lease', True, 'ComfyUI host is remote or the local GPU is free')

        # /object_info is the real check: if it answers, the endpoint is up and
        # can tell us which node types this graph needs. A server that does not
        # implement /system_stats must not hide a perfectly usable graph.
        try:
            info = self._get('/object_info', timeout=min(self.timeout, 8.0))
        except Exception as exc:  # noqa: BLE001 - health must report, never raise
            check('http', False, _clean(str(exc)))
            return EngineStatus(self.id, 'unavailable', _clean(str(exc)), checks,
                                latency_ms=int((time.monotonic() - started) * 1000),
                                extra=extra)
        check('http', True, 'reachable')
        available = info if isinstance(info, dict) else {}
        missing = sorted({node['class_type'] for node in self.workflow.values()
                          if isinstance(node, dict)
                          and isinstance(node.get('class_type'), str)
                          and node['class_type'] not in available})
        check('nodes', not missing,
              'all present' if not missing else 'missing: ' + ', '.join(missing))
        extra['missing_nodes'] = missing

        try:
            stats = self._get('/system_stats', timeout=min(self.timeout, 4.0))
        except Exception:  # noqa: BLE001 - optional metadata only
            stats = None
        if isinstance(stats, dict):
            devices = stats.get('devices') or []
            if devices and isinstance(devices[0], dict):
                extra['comfy_device'] = devices[0].get('name')
            system = stats.get('system') or {}
            if isinstance(system, dict) and system.get('comfyui_version'):
                extra['comfyui_version'] = system.get('comfyui_version')

        found = find_placeholders(self.workflow)
        check('placeholders', any(name in found for name in ('style', 'lyrics')),
              'using ' + ', '.join('{{' + name + '}}' for name in found)
              if found else 'none')

        hard = [c for c in checks if not c.ok]
        if hard:
            state, detail = 'unavailable', '; '.join(f'{c.name}: {c.detail}' for c in hard)
        else:
            state, detail = 'ready', f'ComfyUI workflow "{self.workflow_name}" ready'
        return EngineStatus(self.id, state, detail, checks,
                            latency_ms=int((time.monotonic() - started) * 1000),
                            extra=extra)

    def build_workflow(self, request: RenderRequest) -> dict:
        """The uploaded graph with this take's values in its placeholders."""
        return substitute_placeholders(self.workflow, request, title=request.title)

    def _provenance(self, prompt_id: str, info: dict, source_format: str,
                    transcode: str | None, request: RenderRequest) -> dict:
        return {
            'engine_id': self.id,
            'engine_label': self.label,
            'workflow_id': self.workflow_id,
            'workflow_name': self.workflow_name,
            'model': self.workflow_name,
            'model_family': 'ComfyUI workflow',
            'comfy_prompt_id': prompt_id,
            'comfy_url': self.base_url,
            'source_format': source_format,
            'transcode': transcode,
            'sample_rate': info['sample_rate'],
            'channels': info['channels'],
            'frames': info['frames'],
            'audio_seconds': info['seconds'],
        }
