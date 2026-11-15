"""Cover art: bytes in, an image file out — and a file the studio will only accept from safe places.

Review item 1 asked for cover art kept, but "done the public way": the agent makes the image with its own
`image_generate` tool and hands the *file* to the studio, which is why `set_cover` takes a path rather than
a prompt. A path from a model is untrusted input, so the accepted places are the ones an image plausibly
arrives in — the studio's own data folder, the temp directory, and Hermes' own image cache under
``<hermes home>/cache/images`` — and the bytes must really be the image type they claim to be.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

MAX_IMAGE = 2 * 1024 * 1024

TYPES = {
    'image/png': ('.png', b'\x89PNG\r\n\x1a\n'),
    'image/jpeg': ('.jpg', b'\xff\xd8\xff'),
    'image/webp': ('.webp', b'RIFF'),
}


class CoverError(ValueError):
    """The image was refused; the message says which rule it broke."""


def sniff(image: bytes) -> str:
    """The MIME type the bytes really are, or ``CoverError`` if they are not an image we store."""
    for mime, (_ext, magic) in TYPES.items():
        if image.startswith(magic):
            if mime == 'image/webp' and image[8:12] != b'WEBP':
                continue
            return mime
    raise CoverError('that file is not a PNG, JPEG or WebP image')


def write_cover(root, project_id: str, image: bytes, mime: str) -> None:
    """Store one image for a project, replacing any previous cover atomically."""
    if mime not in TYPES:
        raise CoverError('unsupported image type')
    if len(image) > MAX_IMAGE:
        raise CoverError('image exceeds 2 MiB')
    if sniff(image) != mime:
        raise CoverError('image does not match its type')
    directory = Path(root) / 'studio-covers'
    directory.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix='.cover-', dir=directory)
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write((mime + '\n').encode('ascii') + image)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, directory / (project_id + '.cover'))
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def read_cover(root, project_id: str) -> tuple[str, bytes] | None:
    """The stored cover for a project: ``(mime, bytes)`` or ``None``."""
    path = Path(root) / 'studio-covers' / (project_id + '.cover')
    if not path.is_file():
        return None
    raw = path.read_bytes()
    header, _, body = raw.partition(b'\n')
    mime = header.decode('ascii', errors='ignore')
    if mime not in TYPES:
        return None
    return mime, body


def hermes_home() -> Path | None:
    """The running profile's own folder, from Hermes' public helper.

    `image_generate` writes what the agent asked for under the profile (`cache/images`), which is the
    most likely place a cover path comes from. The public seam is ``hermes_constants.get_hermes_home``;
    when the host package is not importable there is no profile to accept from.
    """
    from .hermes_home import active_home
    return active_home()


def allowed_roots(root, temp: Path | None = None) -> list[Path]:
    """Where an agent-supplied image may come from: the studio, temp, and Hermes' image cache
    (where `image_generate` puts what the agent made)."""
    temp = Path(temp or tempfile.gettempdir())
    roots = [Path(root), temp]
    profile = hermes_home()
    if profile:
        roots.append(profile / 'cache' / 'images')
    unique, seen = [], set()
    for candidate in roots:
        resolved = candidate.expanduser().resolve()
        if resolved not in seen:
            seen.add(resolved)
            unique.append(resolved)
    return unique


def cover_from_file(root, project_id: str, raw_path: str, temp: Path | None = None) -> dict:
    """Copy an image the agent made into the project's cover slot.

    The path is resolved first (``..`` and symlinks are followed once, before the check) and must land
    inside an allowed root; the file must be a real file, not a link, and its bytes must be the image it
    claims to be.
    """
    if not raw_path or not str(raw_path).strip():
        raise CoverError('image_path is required')
    path = Path(str(raw_path)).expanduser()
    if not path.is_absolute():
        raise CoverError('image_path must be an absolute path')
    if path.is_symlink():
        raise CoverError('image_path must be a real file, not a link')
    resolved = path.resolve()
    roots = allowed_roots(root, temp=temp)
    if not any(resolved == candidate or candidate in resolved.parents for candidate in roots):
        raise CoverError('image_path must be inside the studio folder, the temp directory, or '
                         "Hermes' own image cache")
    if not resolved.is_file():
        raise CoverError('image_path does not exist')
    try:
        size = resolved.stat().st_size
    except OSError as exc:  # pragma: no cover - unreadable file
        raise CoverError(f'cannot read image_path: {exc}') from exc
    if size > MAX_IMAGE:
        raise CoverError('image exceeds 2 MiB')
    image = resolved.read_bytes()
    mime = sniff(image)
    write_cover(root, project_id, image, mime)
    return {'success': True, 'mime': mime, 'bytes': len(image), 'source': str(resolved)}
