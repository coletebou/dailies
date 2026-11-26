"""Live panel: what the render agents are producing right now, newest first.

Watched (by mtime, rescanned at most every 2 s):
  renders/shots/*_preview.mp4      browser-playable previews written next to each working render
  renders/shots/*_sheet.jpg        contact sheets
  renders/tests/qa/*.jpg           QA stills
  renders/snapshots/<v>/parts/*    snapshot pieces as the render farm returns them (HEVC; shown via a generated thumbnail)

freeze() clones the current live shot masters into renders/studio/frozen/<name>/shots so a cycle cut (e.g. v07)
stays available as a per-shot source after the next cycle overwrites renders/shots. On APFS `cp -c` makes
copy-on-write clones: no extra disk, and later in-place rewrites of the originals can't reach the clones.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.parse import quote

from .catalog import Catalog
from .config import Settings
from .safepath import rel_to_root
from .util import SID_RE, atomic_write_json, media_url, now_iso, valid_name

RANGE_RE = re.compile(r"_r(\d+)-(\d+)")


class LiveScanner:
    def __init__(self, settings: Settings, catalog: Catalog):
        self.s = settings
        self.cat = catalog
        self._lock = threading.Lock()
        self._cache: tuple[float, list[dict]] | None = None

    def _entry(self, p: Path, st: os.stat_result, typ: str, version: str | None = None) -> dict:
        m = SID_RE.search(p.name)
        rel = rel_to_root(self.s.root, p)
        e = {"path": rel, "name": p.name, "type": typ, "sid": m.group(1) if m else None, "version": version,
             "mtime": st.st_mtime, "size": st.st_size, "url": f"{media_url(rel)}?v={int(st.st_mtime * 1000)}"}
        if typ == "part":
            r = RANGE_RE.search(p.name)
            e["range"] = [int(r.group(1)), int(r.group(2))] if r else None
            e["temp"] = ".part" in p.name
        if typ in ("part", "preview"):
            e["thumb"] = f"/api/live/thumb?path={quote(rel)}&v={int(st.st_mtime * 1000)}"
        return e

    def _scan_dir(self, d: Path, pred, typ: str, version: str | None = None) -> list[dict]:
        out = []
        try:
            with os.scandir(d) as it:
                for de in it:
                    if de.name.startswith(".") or not pred(de.name):
                        continue
                    try:
                        st = de.stat()
                    except OSError:
                        continue
                    if not os.path.isfile(de.path):
                        continue
                    out.append(self._entry(Path(de.path), st, typ, version))
        except (FileNotFoundError, NotADirectoryError):
            pass
        return out

    def scan(self) -> list[dict]:
        now = time.monotonic()
        with self._lock:
            if self._cache and now - self._cache[0] < 2.0:
                return self._cache[1]
        r = self.s.root
        items: list[dict] = []
        items += self._scan_dir(r / "renders/shots", lambda n: n.endswith("_preview.mp4") and ".part" not in n, "preview")
        items += self._scan_dir(r / "renders/shots", lambda n: n.endswith("_sheet.jpg"), "sheet")
        items += self._scan_dir(r / "renders/tests/qa", lambda n: n.lower().endswith((".jpg", ".jpeg")), "qa")
        snaps = r / "renders/snapshots"
        if snaps.is_dir():
            for d in sorted(snaps.iterdir()):
                if (d / "parts").is_dir():
                    items += self._scan_dir(d / "parts", lambda n: n.endswith((".mov", ".mp4", ".mkv")), "part", d.name)
        items.sort(key=lambda e: e["mtime"], reverse=True)
        with self._lock:
            self._cache = (now, items)
        return items

    def query(self, since: float | None, shot: str | None, types: set[str] | None, limit: int) -> dict:
        items = self.scan()
        if since:
            items = [e for e in items if e["mtime"] > since]
        if shot:
            items = [e for e in items if e["sid"] == shot]
        if types:
            items = [e for e in items if e["type"] in types]
        counts: dict[str, int] = {}
        for e in self.scan():
            if e["sid"]:
                counts[e["sid"]] = counts.get(e["sid"], 0) + 1
        return {"now": time.time(), "total": len(items), "items": items[:limit], "per_shot": counts}

    def thumb_source(self, rel: str) -> Path | None:
        """Only snapshot parts (renders/snapshots/<v>/parts/*) and working previews (renders/shots/*_preview.mp4)
        get generated stills; anything else is refused."""
        from .safepath import PathDenied, PathMissing, resolve_media
        try:
            p = resolve_media(self.s.root, rel)
        except (PathDenied, PathMissing):
            return None
        root = self.s.root.resolve()
        if p.parent.name == "parts" and p.parent.parent.parent == root / "renders/snapshots":
            return p
        if p.parent == root / "renders/shots" and p.name.endswith("_preview.mp4"):
            return p
        return None

    def freeze(self, name: str, user: str) -> dict:
        if not valid_name(name):
            raise ValueError("name must be 1-64 chars of letters, digits, '.', '_' or '-'")
        dest = self.s.frozen_dir / name / "shots"
        if dest.parent.exists():
            raise ValueError(f"frozen set {name} already exists")
        live = self.cat.source("live")
        if not live or not live.files:
            raise ValueError("no live shots to freeze")
        dest.mkdir(parents=True)
        made = {}
        for sid, f in live.files.items():
            dst = dest / f.path.name
            # A copy-on-write clone where the filesystem has one: clonefile(2) via BSD `cp -c` on APFS, `cp --reflink=auto`
            # on Linux (btrfs/XFS clone, ext4 falls back to a full copy). GNU cp reads `-c` as --preserve=context, so
            # the flag must match the platform. Any failure falls back to a plain copy.
            cmd = ["cp", "-c"] if sys.platform == "darwin" else ["cp", "--reflink=auto", "--preserve=timestamps"]
            rc = subprocess.run([*cmd, str(f.path), str(dst)], capture_output=True).returncode
            if rc != 0:
                shutil.copy2(f.path, dst)
            made[sid] = {"tier": f.tier, "from": rel_to_root(self.s.root, f.path), "mtime": f.path.stat().st_mtime}
        atomic_write_json(dest.parent / "frozen.json", {"name": name, "frozen": now_iso(), "user": user, "shots": made})
        self.cat.invalidate()
        return {"name": name, "source": f"frozen-{name}", "shots": len(made)}
