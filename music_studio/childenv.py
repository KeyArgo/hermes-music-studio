"""The environment a child process gets — an allowlist, never a copy of ours.

Review item 4. `ace_engine._render` and `service._spawn_worker` handed children `os.environ.copy()`, so
every provider key in the Hermes process (OpenAI, Anthropic, the remote ACE key, whatever else the owner
has configured) reached the ACE-Step runtime and the render worker. Neither has any use for them: they
need to find their interpreter and libraries, their models, and their GPU.

The list is an allowlist because a denylist has to guess every secret's name; an allowlist fails closed
when someone adds a new one.
"""

from __future__ import annotations

import os
from typing import Mapping

KEEP = (
    "PATH",
    "HOME",
    "USERPROFILE",
    "LANG",
    "LANGUAGE",
    "LC_ALL",
    "LC_CTYPE",
    "TMPDIR",
    "TMP",
    "TEMP",
    "LD_LIBRARY_PATH",
    "DYLD_LIBRARY_PATH",
    "SYSTEMROOT",
    "SYSTEMDRIVE",
    "WINDIR",
    "COMSPEC",
    "PATHEXT",
    "APPDATA",
    "LOCALAPPDATA",
    "XDG_RUNTIME_DIR",
    "XDG_CACHE_HOME",
    "DISPLAY",
    "WAYLAND_DISPLAY",
    "CUDA_VISIBLE_DEVICES",
    "CUDA_HOME",
    "CUDA_PATH",
    "PYTORCH_CUDA_ALLOC_CONF",
    "OMP_NUM_THREADS",
    "MKL_NUM_THREADS",
    "TOKENIZERS_PARALLELISM",
)

# Variable families a model runtime legitimately reads: HuggingFace caches and offline flags, the ACE
# runtime/source pointers, Triton's cache, and HF-style transfer knobs. None of them is a credential.
KEEP_PREFIXES = (
    "HF_",
    "HUGGINGFACE_",
    "TRANSFORMERS_",
    "ACE_",
    "ACESTEP_",
    "TRITON_",
    "NVIDIA_",
    "PYTORCH_",
    "HEWN_STUDIO_",
)


# A prefix keeps a family, but a keep-rule must never smuggle a credential: `ACE_REMOTE_API_KEY` starts
# with `ACE_`, and it is exactly the kind of name this allowlist exists to stop. Anything that looks like
# a credential is dropped even when its family is allowed.
_SECRET_HINTS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "PASSWD", "CREDENTIAL", "AUTH", "COOKIE")


def _looks_secret(name: str) -> bool:
    upper = name.upper()
    return any(hint in upper for hint in _SECRET_HINTS)


def minimal_env(extra: Mapping[str, str] | None = None,
                source: Mapping[str, str] | None = None) -> dict[str, str]:
    """Return the environment a render/worker child should receive.

    ``source`` exists for tests; production callers leave it at the real environment. ``extra`` is
    applied last and is how a specific child gets what only it needs (PYTHONPATH, the checkpoint dirs).
    """
    src = os.environ if source is None else source
    env = {name: src[name] for name in KEEP if name in src}
    for name, value in src.items():
        if name.startswith(KEEP_PREFIXES) and not _looks_secret(name):
            env[name] = value
    if extra:
        env.update({str(key): str(value) for key, value in extra.items()})
    return env


# What the studio's own host process reads, on top of the minimal set (11-14 review pass: the launcher
# handed the host the whole Hermes environment minus five Python-path variables, so every provider key
# reached it — review item 4 through a door the review did not point at). Names, not families, so a new
# variable has to be added here on purpose. Anything that looks like a credential is still dropped.
STUDIO_KEEP = (
    "COMFYUI_URL",
    "HERMES_HOME",
    "HERMES_PROFILE_HOME",
    "HERMES_MUSIC_REVAMPCTL",
    "HEWN_FFMPEG",
    "REVAMP_PORT",
    "XDG_DATA_HOME",
    "PROGRAMFILES",
    "ProgramFiles",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
)
STUDIO_KEEP_PREFIXES = ("MUSIC_STUDIO_",)


def studio_env(extra: Mapping[str, str] | None = None,
               source: Mapping[str, str] | None = None) -> dict[str, str]:
    """The environment for the studio host: the minimal set plus the studio's own named settings."""
    src = os.environ if source is None else source
    env = minimal_env(source=src)
    for name, value in src.items():
        if (name in STUDIO_KEEP or name.startswith(STUDIO_KEEP_PREFIXES)) and not _looks_secret(name):
            env[name] = value
    if extra:
        env.update({str(key): str(value) for key, value in extra.items()})
    return env
