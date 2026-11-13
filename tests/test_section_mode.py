"""A section take renders as a plain generate of the section's length, on any engine.

Found live on 2026-11-13: "Change this part" on a ComfyUI (MiniMax Music 3) take failed every time with
"mode must be one of generate, repaint, extend" — the worker passed the take kind 'section' as the mode.
"""
from types import SimpleNamespace

from music_studio import worker


def test_a_section_take_is_requested_as_generate_with_its_own_length(tmp_path):
    snap = {'caption': 'big drum fill', 'lyrics': 'la', 'duration_seconds': 10.0}
    take = {'kind': 'section', 'seed': 7, 'parent_take_id': 'parent',
            'range_start_s': 10.0, 'range_end_s': 20.0}
    store = SimpleNamespace(root=tmp_path)
    jobs = SimpleNamespace(read_audio=lambda *a: (_ for _ in ()).throw(AssertionError('no parent audio for a section')))
    request = worker._render_request(store, jobs, 'p', snap, take)
    assert request.mode == 'generate'
    assert request.duration_seconds == 10.0
    request.validate_common()   # the check that refused it live
