"""Detached generation worker.

Launched by ``music_studio.service`` in its own session so a render outlives the
short-lived agent/dashboard process that requested it. The worker owns the GPU
lease for its whole lifetime, renders every take in the batch it was started for
(serially, one at a time), commits each take's final state
(succeeded/failed/cancelled) itself, then exits.

Usage (internal):
    python -m music_studio.worker --data-dir DIR --project UUID --take UUID
"""
import argparse
import logging
import os
import signal
import sys
import time
from pathlib import Path

_ROOT = str(Path(__file__).resolve().parents[1])
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# Imported for two reasons: the adapter resolves the ACE runtime through this
# module (one source of truth for paths and the GPU lease), and callers that
# patch ``worker.ace_engine`` to stub a render keep working — it is the same
# module object ``ace_local`` calls into.
import ace_engine  # noqa: E402  (needs _ROOT on sys.path first)

# A status poll may hold the lease for a few microseconds while it reconciles;
# retry briefly before declaring the GPU busy so a fresh render is not lost.
LEASE_WAIT_SECONDS = 2.0

# A take error is user-facing; the engine already summarises, this is a backstop.
MAX_ERROR_CHARS = 500

# Exit codes: 0 done, 1 the last take failed, 3 GPU busy, 4 take not claimable.
EXIT_OK, EXIT_FAILED, EXIT_BUSY, EXIT_NOT_CLAIMABLE = 0, 1, 3, 4


def _terminate(signum, frame):
    """Turn SIGTERM/SIGINT into an exception so cleanup (kill ACE, abort) runs.

    Without this, a terminated worker would exit without its ``finally`` block
    and leave the ACE render it started running on the GPU.
    """
    raise SystemExit(128 + signum)


def _take_snapshot(store, project_id, take_id):
    from music_studio.jobs import Jobs
    project = store.get(project_id)
    return dict(Jobs(store)._take(project, take_id))


def _registry(store, target_url=None):
    """The engine registry for this worker's data directory.

    Built per render because the worker is short-lived and the preference file
    is authoritative on disk; a stale in-memory preference would let a take
    render on an engine the user has since deselected. When the take pinned a
    named ComfyUI target, the MiniMax engine in this registry is built against
    that exact URL instead of the engine's default.
    """
    from music_studio.engines import MiniMaxM3ComfyEngine, Registry, default_engines
    from music_studio.engines.registry import PREFERENCE_FILE
    preference_path = store.root / PREFERENCE_FILE
    if not target_url:
        return Registry(preference_path=preference_path)
    engines = default_engines()
    engines = [MiniMaxM3ComfyEngine(base_url=target_url)
               if engine.id == 'minimax_m3_comfy' else engine
               for engine in engines]
    return Registry(engines, preference_path=preference_path)


def _render_request(store, jobs, project_id, snap, take):
    """Translate one queued take into contract vocabulary.

    The take record is the single source of truth: its kind selects the mode,
    its stored geometry becomes the request, and its pinned ``engine_id``
    travels with the request to the registry.
    """
    from music_studio.engines import RenderRequest
    from music_studio.store import render_timeout_seconds
    kind = take.get('kind') or 'generate'
    mode = {'extension': 'extend'}.get(kind, kind)
    source = None
    if mode == 'generate':
        duration = snap['duration_seconds']
    elif mode == 'repaint':
        duration = take.get('source_duration') or snap['duration_seconds']
        # Read the parent through the store's symlink-safe descriptor; it is
        # never written, so this take cannot mutate it.
        source = jobs.read_audio(project_id, take['parent_take_id'])
    else:
        duration = take.get('planned_duration') or snap['duration_seconds']
        source = jobs.read_audio(project_id, take['parent_take_id'])
    # Keep the kill-switch generous but inside the contract's bound; the engine
    # itself is what enforces real geometry caps.
    timeout = min(3600, render_timeout_seconds(duration))
    return RenderRequest(
        caption=snap['caption'], lyrics=snap['lyrics'],
        duration_seconds=duration, seed=take['seed'],
        instrumental=bool(snap.get('instrumental', False)),
        vocal_language=snap.get('vocal_language') or 'en',
        bpm=snap.get('bpm'), keyscale=snap.get('keyscale'),
        minimax_guidance=snap.get('minimax_guidance'),
        output_dir=str(store.root), timeout_seconds=timeout,
        mode=mode, source_wav=source,
        range_start_s=take.get('range_start_s'), range_end_s=take.get('range_end_s'),
        extend_seconds=take.get('extend_seconds'))


def _settle_qc(data, snap, take):
    """Measure the rendered audio and pre-render its reversible tail edit.

    Runs *before* the take is committed, and the result is handed to
    :meth:`Jobs.succeed` so measurement, edit and ``state='succeeded'`` land in
    one save: a client that stops polling on success already has the edit.
    QC can never fail the take — a bug here degrades to no measurements. The
    raw WAV is only read; the edit is separate bytes under its own id.
    """
    from music_studio import qc
    try:
        report = qc.analyse_wav(data)
        requested = take.get('planned_duration') or snap.get('duration_seconds')
        record = qc.take_qc(report, requested_s=requested)
        plan = qc.plan_edit(report)
        if plan is None:
            return record, None
        return record, {'bytes': qc.render_edit(data, plan),
                        'plan': plan, 'report': report}
    except Exception:
        logging.exception('audio QC gate failed; take kept as rendered')
        return None, None


def _enforce_capability(reg, take, mode):
    """Re-check the pinned engine's declared capability before rendering.

    The service refuses an unsupported op at enqueue time, but a stale client,
    a hand-edited take or an engine change since then must not slip a repaint
    onto an engine that cannot do it. The contract checks again inside
    ``Engine.render``; this runs first so the failure names the engine and op.
    """
    if mode not in ('repaint', 'extend'):
        return
    engine_id = take.get('engine_id')
    if not engine_id:
        return
    from music_studio.engines import CapabilityUnsupported, resolve_id
    try:
        engine = reg.get(resolve_id(engine_id) or engine_id)
    except KeyError:
        # An unknown engine is refused by Registry.render with its own message.
        return
    attribute = 'repaint' if mode == 'repaint' else 'continuation'
    if not bool(getattr(engine.capabilities, attribute, False)):
        raise CapabilityUnsupported(
            f'{engine.label} ({engine.id}) does not support {mode} '
            f'(declared {attribute}=false)',
            engine_id=engine.id, operation=mode)


def _render_take(jobs, store, project_id, take_id, release_lease=None):
    """Claim, render and commit exactly one take.

    Returns ``(exit_code, batch_id)``. The batch id tells the caller whether
    another take of the same serial batch is waiting behind this one.

    ``release_lease`` is called on the abandonment path *before* the take is
    marked failed: a status poll that sees 'failed' must already find the GPU
    lease free, or the next render is refused for no reason.
    """
    terminal = False
    try:
        take = jobs.claim(project_id, take_id, os.getpid())
    except (ValueError, KeyError):
        # Abandoned/reconciled before we started, or claimed elsewhere (or
        # cancelled from the queue): the take is not ours, so it must not be
        # touched. The caller decides whether that ends the batch.
        return EXIT_NOT_CLAIMABLE, None
    batch_id = take.get('batch_id')
    snap = take.get('snapshot') or _take_snapshot(store, project_id, take_id)['snapshot']

    def on_progress(percent):
        try:
            jobs.progress(project_id, take_id, progress=percent)
        except Exception:
            logging.debug('worker could not record render progress', exc_info=True)

    def on_process(pid):
        # Persist the render pid so an orphan can be reaped if this process
        # is killed before its own cleanup can run.
        jobs.attach_render(project_id, take_id, pid)

    try:
        try:
            jobs.progress(project_id, take_id, started_at=time.time(), progress=0)
            request = _render_request(store, jobs, project_id, snap, take)
            # The registry is the only render entry point. ``engine_id`` is the
            # engine pinned when the take was queued: the registry refuses an
            # explicit engine that is unavailable rather than substituting
            # another one, so an approved take can never switch backend.
            target_url = snap.get('comfy_target_url')
            reg = _registry(store, target_url) if target_url else _registry(store)
            _enforce_capability(reg, take, request.mode)
            result = reg.render(
                request, engine_id=take.get('engine_id'),
                on_progress=on_progress, on_process=on_process)
            data = result.wav
            # Settle QC from the exact bytes being committed, so success and
            # its edit are one observable state.
            record, edit = _settle_qc(data, snap, take)
            # Provenance is part of the take, not of the process that rendered
            # it: the licence notice (required for MiniMax output), the engine
            # actually used and whether the registry fell back must survive the
            # worker's exit.
            jobs.succeed(project_id, take_id, data, metadata=result.metadata,
                         qc_record=record, edit=edit)
            terminal = True
            return EXIT_OK, batch_id
        except Exception as exc:
            message = (str(exc).strip() or type(exc).__name__)[:MAX_ERROR_CHARS]
            try:
                jobs.fail(project_id, take_id, message)
                terminal = True
            except Exception:
                logging.exception('worker could not record take failure')
            print(f'music-studio worker failed: {message}', file=sys.stderr)
            # A failed take must not stop the rest of the batch.
            return EXIT_FAILED, batch_id
    finally:
        if not terminal:
            # Exiting without committing (crash in setup, unexpected signal
            # handling, commit failure) must never leave the take wedged or the
            # ACE render it started running next to the next one.
            try:
                jobs.reap_render(project_id, take_id)
            except Exception:
                logging.exception('worker could not reap its ACE render')
            if release_lease is not None:
                release_lease()
            try:
                jobs.abort(project_id, take_id,
                           'worker exited without committing the take')
            except Exception:
                logging.exception('worker could not record abandoned take')


def main(argv=None):
    parser = argparse.ArgumentParser(description='Render the takes of one project.')
    parser.add_argument('--data-dir', required=True)
    parser.add_argument('--project', required=True)
    parser.add_argument('--take', required=True, help='first take to render')
    args = parser.parse_args(argv)

    from music_studio.store import Store
    from music_studio.jobs import Jobs

    store = Store(args.data_dir)
    jobs = Jobs(store)

    # SIGTERM/SIGINT must unwind through the cleanup path below; SIGKILL cannot
    # be caught, and the recorded ACE pid is what lets /status reap that case.
    signal.signal(signal.SIGTERM, _terminate)
    signal.signal(signal.SIGINT, _terminate)

    # The worker owns the GPU lease for its whole life; the requesting process
    # must never hold it (its exit would otherwise hand the GPU to a new render).
    lease = store.worker_lock()
    deadline = time.monotonic() + LEASE_WAIT_SECONDS
    while lease is None and time.monotonic() < deadline:
        time.sleep(0.05)
        lease = store.worker_lock()
    if lease is None:
        try:
            jobs.abort(args.project, args.take,
                       'GPU busy: another render owns the worker lease')
        except Exception:
            logging.exception('worker could not record lease failure')
        print('music-studio worker: GPU busy', file=sys.stderr)
        return EXIT_BUSY

    try:
        released = []

        def release_lease():
            """Idempotent: the lease is closed once, on the first call."""
            if released:
                return
            released.append(True)
            try:
                lease.close()
            except OSError:
                pass

        take_id, first, code = args.take, True, EXIT_OK
        while take_id:
            code, batch_id = _render_take(jobs, store, args.project, take_id, release_lease)
            if code == EXIT_NOT_CLAIMABLE:
                if first:
                    print('music-studio worker: take not claimable', file=sys.stderr)
                    return EXIT_NOT_CLAIMABLE
                # Cancelled or already handled: look for what is still queued.
                batch_id = _batch_of(store, args.project, take_id)
            first = False
            nxt = jobs.next_queued(args.project, batch_id)
            take_id = nxt['take_id'] if nxt else None
        return EXIT_FAILED if code == EXIT_FAILED else EXIT_OK
    finally:
        try:
            lease.close()
        except OSError:
            pass


def _batch_of(store, project_id, take_id):
    try:
        for take in store.get(project_id)['takes']:
            if take['take_id'] == take_id:
                return take.get('batch_id')
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


if __name__ == '__main__':
    sys.exit(main())
