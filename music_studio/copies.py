"""Which copy of Music Studio this process is really running.

Hermes serves every profile (default, jester, private, testing) from ONE Python process and imports
plugin code with no isolation. The packages here are plain top-level names — ``music_studio`` and
``standalone`` — so whichever profile's copy imports first is the one that stays in ``sys.modules``;
every other profile's dashboard then runs against it, silently.

When the copy that won is an older one, a name the newer routes import is simply absent, and the
person sees ``hermes.api error 500: Internal Server Error`` instead of a sentence. This module turns
that situation into one sentence naming the folder, the profile, and the command that fixes it.

Nothing here raises or changes behaviour: it only answers "is the loaded copy ours?".
"""

from __future__ import annotations

import os
import sys

# The top-level packages this plugin owns. Either one landing from another folder is enough.
_PACKAGES = ('music_studio', 'standalone')


def _loaded_from(name: str) -> str | None:
    """The root folder a top-level package was loaded from, or None when it is not loaded.

    ``.../plugins/hermes-music-studio/music_studio/__init__.py`` -> ``.../plugins/hermes-music-studio``:
    the parent of the package directory is the plugin root that ``root`` is compared against.
    """
    module = sys.modules.get(name)
    if module is None:
        return None
    path = getattr(module, '__file__', None)
    if not path:
        # A namespace package, or a stub with no file: nothing to compare, so it cannot be named.
        return None
    return os.path.realpath(os.path.dirname(os.path.dirname(path)))


def _profile(folder: str) -> str:
    """The profile name inside ``.../profiles/<name>/plugins/...``, else "the other profile".

    Hermes' layouts name the profile in the path; when the folder is not one of those (a git
    checkout, an unpacked zip), the name is unknown and saying so is better than guessing.
    """
    parts = folder.split(os.sep)
    if 'profiles' in parts:
        index = parts.index('profiles')
        if index + 1 < len(parts):
            return parts[index + 1]
    return 'the other profile'


def foreign_copy(root: str) -> str | None:
    """A sentence when another folder's Music Studio is the loaded one, None when ours is.

    ``root`` is this copy's plugin folder (``_ROOT`` in ``dashboard/plugin_api.py``). Both sides are
    realpaths, so a symlinked plugin folder is not mistaken for a different copy.
    """
    ours = os.path.realpath(root)
    for name in _PACKAGES:
        folder = _loaded_from(name)
        if folder is None or folder == ours:
            continue
        # One process, one set of modules: this copy's routes are running on that copy's code, and
        # an update of this profile alone cannot fix it. Name the folder and the command.
        profile = _profile(folder)
        return (f'Another Hermes profile loaded an older Music Studio from {folder}. '
                'Hermes runs every profile in one process, so all profiles must have the same '
                f'version. Update it with: hermes -p {profile} plugins update '
                'hermes-music-studio, then restart Hermes.')
    return None
