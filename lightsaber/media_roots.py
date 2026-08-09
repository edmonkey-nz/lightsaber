"""Linked media: referencing big video/image files where they already live,
instead of copying them into the app's own `media/` folder.

Why a named-root indirection instead of just storing an absolute path:

1. **Security.** `run.py` binds `--host 0.0.0.0` by default, so this server
   is reachable from whatever network it's on (a venue's wifi, say). A route
   that served any absolute path on request would be a remote arbitrary-file
   read for anyone who could reach the port. Roots are an explicit opt-in
   allowlist: only files genuinely underneath a configured root are ever
   served, checked after `realpath()` so neither `../` nor a symlink planted
   inside a root can escape it.

2. **Portability.** A shape stores `"videos::gig2/opener.mp4"`, not
   `/home/eddie/Videos/gig2/opener.mp4`. Move the project to a machine where
   the root named `videos` points somewhere else (ARCHITECTURE.md's render
   host, whose media lives on its own disk) and every link still resolves,
   with nothing to relink by hand. The UI still displays the full resolved
   absolute path — the alias is storage, not presentation.

Roots are machine-level, so they live in settings.json (per install), NOT on
ProjectSpec: "where this box keeps its video" is a property of the box, in
the same way ARCHITECTURE.md separates a rig "profile" from a show.
"""

from __future__ import annotations

import os

from . import settings

_SETTINGS_KEY = "media_roots"

# Kept in step with the client-side picker's filter and with the extensions
# the <video>/<img> elements can actually decode.
MEDIA_EXTS = {".mp4", ".m4v", ".mov", ".webm", ".mkv", ".avi", ".ogv",
              ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".avif"}

# Path existence is checked once per broadcast per linked shape, which at 60fps
# would be a stat() storm for no benefit — a linked file appearing or vanishing
# is a human-timescale event (drive unplugged, file moved), so a short cache is
# indistinguishable from live and costs nothing.
_EXISTS_TTL = 2.0
_exists_cache: dict[str, tuple[float, bool]] = {}


def _safe_root_name(name: str) -> str:
    """Root names go in the link string and in URLs, so keep them boring."""
    return "".join(c for c in str(name) if c.isalnum() or c in "-_").strip("-_")[:40]


def roots() -> dict[str, str]:
    """{name: absolute path}. Anything unusable (missing key, non-absolute,
    no longer a directory) is dropped rather than raising — a root on an
    unplugged drive should degrade to "links look missing", not break the app."""
    raw = settings.get(_SETTINGS_KEY, {}) or {}
    out = {}
    if isinstance(raw, dict):
        for name, path in raw.items():
            name = _safe_root_name(name)
            if not name or not isinstance(path, str) or not os.path.isabs(path):
                continue
            out[name] = os.path.realpath(path)
    return out


def set_root(name: str, path: str) -> tuple[bool, str]:
    """Add/replace a root. Returns (ok, message-or-name)."""
    name = _safe_root_name(name)
    if not name:
        return False, "a root needs a name (letters, digits, - or _)"
    if not isinstance(path, str) or not os.path.isabs(path):
        return False, "the path must be absolute"
    real = os.path.realpath(path)
    if not os.path.isdir(real):
        return False, f"not a directory: {real}"
    data = settings.get(_SETTINGS_KEY, {}) or {}
    if not isinstance(data, dict):
        data = {}
    data[name] = real
    settings.set(_SETTINGS_KEY, data)
    return True, name


def remove_root(name: str) -> bool:
    data = settings.get(_SETTINGS_KEY, {}) or {}
    if not isinstance(data, dict) or name not in data:
        return False
    data.pop(name)
    settings.set(_SETTINGS_KEY, data)
    return True


def split_link(link: str):
    """'videos::gig2/opener.mp4' -> ('videos', 'gig2/opener.mp4'), or None."""
    if not isinstance(link, str) or "::" not in link:
        return None
    name, _, rel = link.partition("::")
    name = _safe_root_name(name)
    # Deliberately NOT lstrip("/") — silently turning "::/etc/passwd" into
    # "etc/passwd" relative to the root would land inside the root (so it was
    # never a hole) but reads as if the absolute path had been honoured.
    # Refusing it outright keeps resolve()'s isabs check meaningful.
    if not name or not rel or os.path.isabs(rel):
        return None
    return name, rel


def _contained(root_real: str, candidate_real: str) -> bool:
    """True iff candidate is root itself or genuinely underneath it. Compares
    realpath'd values, which is what makes a symlink inside a root pointing
    at /etc fail this check rather than sail through it."""
    if candidate_real == root_real:
        return True
    return candidate_real.startswith(root_real.rstrip(os.sep) + os.sep)


def resolve(link: str) -> str | None:
    """Link string -> absolute on-disk path, or None if it doesn't name a
    configured root or would escape it. THE security boundary: every path
    that reaches the filesystem or an HTTP response goes through here."""
    parts = split_link(link)
    if parts is None:
        return None
    name, rel = parts
    root = roots().get(name)
    if root is None:
        return None
    # An absolute or drive-ish `rel` would make os.path.join discard the root
    # entirely, so refuse it outright rather than relying on the containment
    # check to catch it afterwards.
    if os.path.isabs(rel):
        return None
    # Defence in depth: a media link may only ever point at media. Roots are
    # folders the user opted into, but if someone registers a broad one (a
    # whole home directory), this keeps a hand-crafted link from turning the
    # feature into a general file-read of everything underneath it.
    if os.path.splitext(rel)[1].lower() not in MEDIA_EXTS:
        return None
    candidate = os.path.realpath(os.path.join(root, rel))
    return candidate if _contained(root, candidate) else None


def to_link(abs_path: str) -> str | None:
    """Absolute path -> link string, or None if it isn't under any root.
    Picks the LONGEST matching root, so a nested root wins over its parent
    and the stored link stays as specific (and as portable) as possible."""
    if not isinstance(abs_path, str) or not os.path.isabs(abs_path):
        return None
    real = os.path.realpath(abs_path)
    best = None
    for name, root in roots().items():
        if _contained(root, real):
            if best is None or len(root) > len(best[1]):
                best = (name, root)
    if best is None:
        return None
    name, root = best
    rel = os.path.relpath(real, root).replace(os.sep, "/")
    return f"{name}::{rel}"


def link_exists(link: str) -> bool:
    """Cached os.path.isfile for a link — see _EXISTS_TTL above."""
    import time
    now = time.monotonic()
    hit = _exists_cache.get(link)
    if hit is not None and now - hit[0] < _EXISTS_TTL:
        return hit[1]
    path = resolve(link)
    ok = bool(path and os.path.isfile(path))
    _exists_cache[link] = (now, ok)
    return ok


def browse(root_name: str, rel: str = "") -> dict:
    """One directory's worth of subfolders + media files, for the picker.
    Same containment rule as resolve(); an invalid request returns an error
    dict rather than leaking whether a path outside the root exists."""
    root = roots().get(_safe_root_name(root_name))
    if root is None:
        return {"error": "unknown root"}
    rel = (rel or "").lstrip("/")
    if os.path.isabs(rel):
        return {"error": "invalid path"}
    here = os.path.realpath(os.path.join(root, rel))
    if not _contained(root, here) or not os.path.isdir(here):
        return {"error": "invalid path"}
    dirs, files = [], []
    try:
        entries = sorted(os.scandir(here), key=lambda e: e.name.lower())
    except OSError as e:
        return {"error": str(e)}
    for e in entries:
        if e.name.startswith("."):
            continue
        try:
            if e.is_dir():
                # A symlinked subdir pointing outside the root can't be
                # entered (resolve/_contained reject it), so listing it would
                # only offer a dead end.
                if not _contained(root, os.path.realpath(e.path)):
                    continue
                dirs.append({"name": e.name})
            elif e.is_file() and os.path.splitext(e.name)[1].lower() in MEDIA_EXTS:
                files.append({"name": e.name, "size": e.stat().st_size})
        except OSError:
            continue
    rel_norm = os.path.relpath(here, root).replace(os.sep, "/")
    if rel_norm == ".":
        rel_norm = ""
    return {"root": _safe_root_name(root_name), "path": rel_norm,
            "abs": here, "dirs": dirs, "files": files}
