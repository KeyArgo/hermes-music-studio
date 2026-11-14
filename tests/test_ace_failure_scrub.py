"""A failed ACE render never shows the server's file layout, on any platform."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ace_engine import _summarize_failure  # noqa: E402


def test_a_posix_absolute_path_is_reduced_to_its_basename():
    message = _summarize_failure('/opt/ace/checkpoints/unet.ckpt could not load', 1)
    assert '/opt/ace' not in message
    assert 'unet.ckpt' in message


def test_a_windows_drive_path_is_reduced_to_its_basename():
    message = _summarize_failure(
        'Traceback\nOSError: cannot load C:\\Users\\alice\\models\\unet.ckpt', 1)
    assert 'alice' not in message
    assert 'Users' not in message
    assert 'unet.ckpt' in message


def test_a_windows_unc_path_is_reduced_to_its_basename():
    message = _summarize_failure(
        r'OSError: cannot load \\fileserver\share\checkpoints\model.safetensors', 1)
    assert 'fileserver' not in message
    assert 'share' not in message
    assert 'model.safetensors' in message
