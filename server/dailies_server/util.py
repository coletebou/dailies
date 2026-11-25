"""Small shared helpers: git-friendly JSON, atomic writes, ffprobe, name validation, timestamps."""
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")   # used with fullmatch: "$" would accept "x\n"
SID_RE = re.compile(r"\b(s\d{2})(?=[_.\-]|$)")
VKEY_RE = re.compile(r"^(v\d{2,3})(?=$|[_\-.])")


def valid_name(name: str) -> bool:
    return bool(NAME_RE.fullmatch(name or "")) and ".." not in name


def media_url(rel: str) -> str:
    """/media URL of a project-relative path, each segment percent-encoded (names may hold #, &, %, spaces)."""
    return "/media/" + quote(rel)


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def version_key(name: str) -> str | None:
    m = VKEY_RE.match(name)
    return m.group(1) if m else None


def _is_scalar(x: Any) -> bool:
    return x is None or isinstance(x, (str, int, float, bool))


def _inline(x: Any) -> bool:
    """Lists of scalars, or lists of short scalar lists (point arrays), are written on one line."""
    if not isinstance(x, list):
        return False
    return all(_is_scalar(e) for e in x) or all(isinstance(e, list) and all(_is_scalar(v) for v in e) for e in x)


def dumps_pretty(obj: Any, indent: int = 2, _level: int = 0) -> str:
    """json.dumps(indent=2), except point/number arrays stay on one line so drawings don't explode into 10k lines.
    NaN and Infinity raise ValueError: they are not JSON, and browsers and jq could not read the file back."""
    pad = " " * (indent * (_level + 1))
    end = " " * (indent * _level)
    if isinstance(obj, dict):
        if not obj:
            return "{}"
        items = [f"{pad}{json.dumps(str(k), ensure_ascii=False)}: {dumps_pretty(v, indent, _level + 1)}" for k, v in obj.items()]
        return "{\n" + ",\n".join(items) + "\n" + end + "}"
    if isinstance(obj, list):
        if not obj:
            return "[]"
        if _inline(obj):
            return json.dumps(obj, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        items = [pad + dumps_pretty(v, indent, _level + 1) for v in obj]
        return "[\n" + ",\n".join(items) + "\n" + end + "]"
    return json.dumps(obj, ensure_ascii=False, allow_nan=False)


def atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, 0o644)        # mkstemp makes 0600; keep notes and media readable like any other project file
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


def atomic_write_json(path: Path, obj: Any) -> None:
    atomic_write_bytes(path, (dumps_pretty(obj) + "\n").encode("utf-8"))


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text("utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def file_sig(p: Path) -> tuple[int, int] | None:
    try:
        st = p.stat()
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size)


_probe_cache: dict[str, tuple[tuple[int, int], dict | None]] = {}
_probe_lock = threading.Lock()


def ffprobe(p: Path) -> dict | None:
    """Duration / size / frame count of a media file, cached by (mtime, size). None if unreadable (e.g. still being
    written: an mp4 with +faststart has no moov atom until the encoder finishes)."""
    sig = file_sig(p)
    if sig is None:
        return None
    key = str(p)
    with _probe_lock:
        hit = _probe_cache.get(key)
        if hit and hit[0] == sig:
            return hit[1]
    info: dict | None
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries",
             "stream=codec_type,codec_name,width,height,nb_frames,r_frame_rate,pix_fmt:format=duration",
             "-of", "json", str(p)], capture_output=True, text=True, timeout=20)
        j = json.loads(out.stdout or "{}")
        v = next((s for s in j.get("streams", []) if s.get("codec_type") == "video"), None)
        a = next((s for s in j.get("streams", []) if s.get("codec_type") == "audio"), None)
        dur = float(j.get("format", {}).get("duration") or 0)
        if out.returncode != 0 or v is None or dur <= 0:
            info = None
        else:
            info = {"duration": dur, "width": v.get("width"), "height": v.get("height"), "codec": v.get("codec_name"),
                    "pix_fmt": v.get("pix_fmt"), "frames": int(v["nb_frames"]) if str(v.get("nb_frames", "")).isdigit() else None,
                    "audio": a is not None}
    except (subprocess.SubprocessError, ValueError, OSError):
        info = None
    with _probe_lock:
        _probe_cache[key] = (sig, info)
    return info


def parse_timestamp(ts: str) -> float | None:
    """'01:29' -> 89.0, '1:05.5' -> 65.5, '00:01:05' -> 65.0."""
    try:
        parts = [float(x) for x in str(ts).strip().split(":")]
    except ValueError:
        return None
    t = 0.0
    for x in parts:
        t = t * 60 + x
    return t
