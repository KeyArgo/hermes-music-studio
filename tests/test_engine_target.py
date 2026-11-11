"""A pinned ComfyUI target belongs to the ComfyUI engine, not to the song.

The bug this pins: comparing one approved draft across engines failed on the ACE row with
"comfy_target_id requires engine minimax_m3_comfy", because the target was applied to every engine.
"""
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from music_studio import service  # noqa: E402

TARGET = {'target_id': 'tgt-1', 'name': 'Living room box', 'url': 'http://192.0.2.52:8188'}


class EngineAndTargetTests(unittest.TestCase):
    def run_with(self, engine_id, target=TARGET, resolved='ace_local'):
        with mock.patch.object(service, '_selected_target', return_value=target), \
                mock.patch.object(service, 'registry', return_value='reg'), \
                mock.patch.object(service, 'resolve_engine', return_value=resolved), \
                mock.patch.object(service, '_preflight_target', return_value=resolved) as preflight:
            engine, chosen = service._engine_and_target('store', {'engine': 'ace'}, engine_id)
        return engine, chosen, preflight

    def test_a_comfy_target_does_not_block_a_local_engine(self):
        engine, target, preflight = self.run_with('ace_local', resolved='ace_local')
        self.assertEqual(engine, 'ace_local')
        self.assertIsNone(target, 'the pin says nothing about a local engine')
        preflight.assert_not_called()

    def test_a_comfy_target_is_probed_for_the_comfy_engine(self):
        engine, target, preflight = self.run_with('minimax_m3_comfy', resolved='minimax_m3_comfy')
        self.assertEqual(engine, 'minimax_m3_comfy')
        self.assertEqual(target, TARGET)
        preflight.assert_called_once()

    def test_no_target_means_no_probe_for_any_engine(self):
        engine, target, preflight = self.run_with(None, target=None, resolved='minimax_m3_comfy')
        self.assertEqual(engine, 'minimax_m3_comfy')
        self.assertIsNone(target)
        preflight.assert_not_called()


if __name__ == '__main__':
    unittest.main()
