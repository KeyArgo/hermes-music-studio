"""Open a file for reading without following a symlink, on every platform."""
import errno
import os
import stat

_FLAGS = (os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0)
          | getattr(os, 'O_BINARY', 0))
_REPARSE = getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400)


def open_nofollow(path):
    """os.open(path) read-only, refusing a symlink (or Windows reparse point) as the last component.

    POSIX has O_NOFOLLOW. Windows does not, so os.open followed a symlinked take straight to its
    target. There the link is refused from lstat, and the opened file must be the one lstat saw, so
    a swap between the two calls is refused too.
    """
    if hasattr(os, 'O_NOFOLLOW'):
        return os.open(path, _FLAGS)
    before = os.lstat(path)
    if stat.S_ISLNK(before.st_mode) or getattr(before, 'st_file_attributes', 0) & _REPARSE:
        raise OSError(errno.ELOOP, 'refusing to follow a link', str(path))
    fd = os.open(path, _FLAGS)
    after = os.fstat(fd)
    if (after.st_dev, after.st_ino) != (before.st_dev, before.st_ino):
        os.close(fd)
        raise OSError(errno.ELOOP, 'file changed while opening', str(path))
    return fd
