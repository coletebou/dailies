"""The media allowlist. Every file the HTTP surface serves by path goes through resolve_media().

Allowed: anything under renders/, notes/critiques/, notes/studio/; the file build/edl.json; music/lyrics*.json.
A request path is rejected before touching the filesystem if it is absolute, contains '..', '.', empty segments,
backslashes or NUL. It is then resolved (following symlinks) and the *real* path must still sit under an allowlisted
root, so a symlink inside renders/ that points elsewhere is refused too.
"""
from __future__ import annotations

import fnmatch
from pathlib import Path

ALLOW_DIRS = ("renders", "notes/critiques", "notes/studio")
ALLOW_FILES = ("build/edl.json",)
ALLOW_GLOBS = (("music", "lyrics*.json"),)


class PathDenied(Exception):
    """The path is outside the allowlist or malformed (HTTP 403)."""


class PathMissing(Exception):
    """The path is allowed in principle but no such regular file exists (HTTP 404)."""


def _lexically_ok(rel: str) -> bool:
    if not rel or len(rel) > 1024 or "\x00" in rel or "\\" in rel or rel.startswith("/") or "~" == rel[:1]:
        return False
    return all(p not in ("", ".", "..") for p in rel.split("/"))


def is_allowed_real(root: Path, real: Path) -> bool:
    """True if an already-resolved absolute path lies inside the allowlist."""
    root = root.resolve()
    for d in ALLOW_DIRS:
        base = (root / d).resolve()
        if real == base or real.is_relative_to(base):
            return True
    for f in ALLOW_FILES:
        if real == (root / f).resolve():
            return True
    for d, pat in ALLOW_GLOBS:
        if real.parent == (root / d).resolve() and fnmatch.fnmatchcase(real.name, pat):
            return True
    return False


def resolve_media(root: Path, rel: str) -> Path:
    """Map a project-relative path from a URL to a real file, or raise PathDenied / PathMissing."""
    if not _lexically_ok(rel):
        raise PathDenied(rel)
    cand = root / rel
    try:
        real = cand.resolve(strict=True)
    except (FileNotFoundError, NotADirectoryError):
        # Don't leak existence of files outside the allowlist: decide denial first on the lexical path.
        if not is_allowed_real(root, cand.resolve(strict=False)):
            raise PathDenied(rel) from None
        raise PathMissing(rel) from None
    except (OSError, RuntimeError):   # symlink loops etc.
        raise PathDenied(rel) from None
    if not is_allowed_real(root, real):
        raise PathDenied(rel)
    if not real.is_file():
        raise PathMissing(rel)
    return real


def rel_to_root(root: Path, p: Path) -> str:
    """Project-relative POSIX path for a file under root (used to build /media URLs)."""
    try:
        return p.relative_to(root).as_posix()
    except ValueError:
        return p.resolve().relative_to(root.resolve()).as_posix()
