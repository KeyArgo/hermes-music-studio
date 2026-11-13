"""Take audio and peak caches are opened without following a symlink, on every platform.

Windows has no O_NOFOLLOW, so the old os.open followed a symlinked take to its target and served it
(CI: test_symlinked_take_audio_is_refused got 200 on windows-latest). These run the Windows branch
on any OS by hiding O_NOFOLLOW."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from music_studio import safeopen  # noqa: E402


class OpenNoFollowTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.root = Path(self.dir.name)
        self.target = self.root / 'elsewhere.wav'
        self.target.write_bytes(b'RIFF')
        self.link = self.root / 'take.wav'
        try:
            self.link.symlink_to(self.target)
        except (OSError, NotImplementedError):
            self.skipTest('this machine cannot create symlinks')

    def tearDown(self):
        self.dir.cleanup()

    def test_a_symlink_is_refused(self):
        with self.assertRaises(OSError):
            os.close(safeopen.open_nofollow(self.link))

    def test_a_symlink_is_refused_without_o_nofollow_like_on_windows(self):
        with patch.object(safeopen.os, 'O_NOFOLLOW', None, create=True):
            delattr(safeopen.os, 'O_NOFOLLOW')
            with self.assertRaises(OSError):
                os.close(safeopen.open_nofollow(self.link))

    def test_a_regular_file_opens_without_o_nofollow(self):
        with patch.object(safeopen.os, 'O_NOFOLLOW', None, create=True):
            delattr(safeopen.os, 'O_NOFOLLOW')
            fd = safeopen.open_nofollow(self.target)
        with os.fdopen(fd, 'rb') as fh:
            self.assertEqual(fh.read(), b'RIFF')


if __name__ == '__main__':
    unittest.main()
