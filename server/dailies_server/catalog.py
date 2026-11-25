"""What exists on disk: the EDL, lyrics, versions (full cuts), per-shot sources and critiques.

Versions are grouped by their vNN key. The canonical cut for a key prefers a fixed 1080p snapshot, then a 1080p
snapshot, then the preview. A file X.mp4 is hidden when X_fixed.mp4 exists (the 2026-10-04 stitch-bug erratum), and so
is its X_crit.mp4 re-encode. Cuts built in Dailies (renders/studio/cuts/*.mp4) are versions too, keyed "studio-<name>".

Per-shot sources are directories of masters named <sid>_<tier>.<ext>:
  renders/snapshots/<name>/shots/      one per snapshot render (final-first, as `assemble.py --tier final` picks)
  renders/studio/frozen/<name>/shots/  clones of the live shots frozen from Dailies
  renders/shots/                       "live": the working renders, cycle-first (what working-cycle cuts use)
"""
from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from .config import Settings
from .safepath import rel_to_root
from .util import ffprobe, file_sig, media_url, parse_timestamp, read_json, version_key

FINAL_FIRST = ("final", "preview", "cycle", "draft")
CYCLE_FIRST = ("cycle", "preview", "final", "draft")
EXTS = (".mov", ".mp4", ".mkv")
TAGS = ("problem", "like", "idea")


@dataclass
class ShotFile:
    sid: str
    tier: str
    path: Path


@dataclass
class Source:
    id: str
    label: str
    kind: str                 # snapshot | frozen | live
    version: str | None       # vNN key the shots belong to (None for live)
    dir: Path
    order: tuple[str, ...]
    files: dict[str, ShotFile] = field(default_factory=dict)


def _shot_in_dir(d: Path, sid: str, order: tuple[str, ...]) -> ShotFile | None:
    for tier in order:
        for ext in EXTS:
            p = d / f"{sid}_{tier}{ext}"
            if p.is_file():
                return ShotFile(sid, tier, p)
    return None


class Catalog:
    def __init__(self, settings: Settings):
        self.s = settings
        self._lock = threading.Lock()
        self._cache: dict[str, tuple[float, object]] = {}

    # --- static-ish project data ------------------------------------------------------------------------------------
    def _cached(self, key: str, ttl: float, fn):
        now = time.monotonic()
        with self._lock:
            hit = self._cache.get(key)
            if hit and now - hit[0] < ttl:
                return hit[1]
        val = fn()
        with self._lock:
            self._cache[key] = (now, val)
        return val

    def invalidate(self) -> None:
        with self._lock:
            self._cache.clear()

    def edl(self) -> dict:
        return self._cached("edl", 5, lambda: json.loads((self.s.root / "build/edl.json").read_text()))

    def shot_ids(self) -> list[str]:
        return [s["id"] for s in self.edl()["shots"]]

    def shot_by_id(self) -> dict[str, dict]:
        return {s["id"]: s for s in self.edl()["shots"]}

    def shot_at_frame(self, frame: int) -> str | None:
        for s in self.edl()["shots"]:
            if s["f0"] <= frame < s["f1"]:
                return s["id"]
        shots = self.edl()["shots"]
        return shots[-1]["id"] if shots and frame >= shots[-1]["f1"] else None

    def lyrics(self) -> dict:
        return self._cached("lyrics", 10, lambda: read_json(self.s.root / "music/lyrics_timed.json", {"lines": []}))

    def shotlist(self) -> dict[str, dict]:
        """Lyric + picture columns from notes/SHOTLIST.md (read server-side only; the file itself is not served)."""
        def load():
            out: dict[str, dict] = {}
            try:
                for line in (self.s.root / "notes/SHOTLIST.md").read_text().splitlines():
                    if line.startswith("| ") and line[2:4].isdigit():
                        c = [x.strip() for x in line.split("|")]
                        pic = re.sub(r"\*\*", "", c[4]) if len(c) > 4 else ""
                        out["s" + c[1]] = {"lyric": c[3] if len(c) > 3 else "", "picture": pic}
            except OSError:
                pass
            return out
        return self._cached("shotlist", 30, load)

    def meta(self) -> dict:
        edl = self.edl()
        fps = edl.get("fps", self.s.fps)
        lines = self.lyrics().get("lines", [])
        sl = self.shotlist()
        shots = []
        for s in edl["shots"]:
            t0, t1 = s["f0"] / fps, s["f1"] / fps
            ov = [ln for ln in lines if ln.get("t0", 0) < t1 and ln.get("t1", 0) > t0]
            # the line that is sung mostly inside this shot labels it
            ov.sort(key=lambda ln: -(min(t1, ln["t1"]) - max(t0, ln["t0"])))
            shots.append({**s, "t0": t0, "t1": t1, "lyric": ov[0]["text"] if ov else "",
                          "section": ov[0].get("section", "") if ov else "",
                          "lyrics": [ln["text"] for ln in sorted(ov, key=lambda ln: ln["t0"])],
                          "shotlist": sl.get(s["id"], {})})
        return {"fps": fps, "total_frames": edl["total_frames"], "duration": edl["total_frames"] / fps,
                "lead_frames": edl.get("lead_frames", 0), "shots": shots,
                "lyrics": [{"section": ln.get("section"), "text": ln.get("text"), "t0": ln.get("t0"), "t1": ln.get("t1"),
                            "words": [{"w": w["w"], "t0": w["t0"], "t1": w["t1"]} for w in ln.get("words", [])]}
                           for ln in lines]}

    # --- critiques ---------------------------------------------------------------------------------------------------
    def critiques(self) -> list[dict]:
        def load():
            out = []
            for p in sorted((self.s.root / "notes/critiques").glob("v*.json")):
                d = read_json(p)
                if not isinstance(d, dict) or "scores" not in d:
                    continue
                label = d.get("label") or p.stem
                weak = []
                for w in d.get("weakest_moments", []) or []:
                    t = parse_timestamp(w.get("timestamp", ""))
                    if t is not None:
                        # "01:29" means somewhere in second 89: place it mid-second, so 01:29 lands in s24 (89.07 s
                        # onward) rather than the last frames of s23
                        if "." not in str(w.get("timestamp", "")):
                            t += 0.5
                        weak.append({"t": t, "timestamp": w.get("timestamp"), "problem": w.get("problem", ""),
                                     "fix": w.get("fix", "")})
                model = d.get("model", "")
                out.append({"file": rel_to_root(self.s.root, p), "label": label, "key": version_key(label),
                            "model": model, "model_short": "pro" if "pro" in model else ("flash" if "flash" in model else model),
                            "scores": d.get("scores", {}), "justification": d.get("justification", {}),
                            "avg": d.get("avg"), "min": d.get("min"), "overall": d.get("overall", ""),
                            "when": d.get("when"), "video": d.get("video"), "weakest": weak})
            return out
        return self._cached("critiques", 10, load)

    # --- versions -----------------------------------------------------------------------------------------------------
    def _cut_entry(self, p: Path, kind: str) -> dict | None:
        info = ffprobe(p)
        sig = file_sig(p)
        if sig is None:
            return None
        rel = rel_to_root(self.s.root, p)
        # film_f0/film_f1: the film frames [f0, f1) the file holds; its own time 0 is film frame f0 (f1 None: to its end)
        return {"id": f"{p.parent.name}__{p.stem}", "path": rel, "kind": kind, "name": p.stem,
                "url": f"{media_url(rel)}?v={sig[0] // 1_000_000}", "size": sig[1], "mtime": sig[0] / 1e9,
                "ready": info is not None, "probe": info, "film_f0": 0, "film_f1": None}

    def cut_files(self) -> list[dict]:
        def load():
            snaps = sorted((self.s.root / "renders/snapshots").glob("v*.mp4"))
            stems = {p.stem for p in snaps}
            cuts = []
            for p in snaps:
                base = p.stem[:-5] if p.stem.endswith("_crit") else p.stem
                if f"{base}_fixed" in stems and not p.stem.endswith("_fixed"):
                    continue          # superseded by the fixed re-stitch
                e = self._cut_entry(p, "snapshot")
                if e:
                    cuts.append(e)
            for p in sorted((self.s.root / "renders/previews").glob("v*.mp4")):
                e = self._cut_entry(p, "preview")
                if e:
                    cuts.append(e)
            return cuts
        return self._cached("cuts", 4, load)

    def studio_cuts(self, active_names: set[str] = frozenset()) -> list[dict]:
        out = []
        d = self.s.cuts_dir
        if not d.is_dir():
            return out
        for p in sorted(d.glob("*.mp4")):
            if ".failed" in p.name or p.name.startswith(".") or p.stem in active_names:
                continue
            e = self._cut_entry(p, "studio")
            if e:
                e["meta"] = read_json(p.with_suffix(".json"), {}) or {}
                e["film_f0"], e["film_f1"] = self.film_range(e["meta"])
                out.append(e)
        return out

    def film_range(self, meta: dict) -> tuple[int, int | None]:
        """Film frames [f0, f1) of a studio cut. A partial build (assemble.py --from A --to B) starts at A's first
        frame: that is the cut's own time 0. Builds record the range; for older ones it comes from their from/to
        shots in the EDL. A full build starts at film frame 0."""
        f0, f1 = meta.get("film_f0"), meta.get("film_f1")
        if isinstance(f0, int) and isinstance(f1, int):
            return f0, f1
        shots = self.shot_by_id()
        a, b = shots.get(meta.get("from") or ""), shots.get(meta.get("to") or meta.get("from") or "")
        return (a["f0"], b["f1"]) if a and b else (0, None)

    @staticmethod
    def _rank(c: dict) -> tuple:
        n = c["name"]
        if c["kind"] == "snapshot":
            r = 0 if n.endswith("_fixed") else (1 if n.endswith("_1080") else 2)
        else:
            r = 3
        return (r, -c["mtime"])

    def versions(self, active_names: set[str] = frozenset()) -> list[dict]:
        groups: dict[str, list[dict]] = {}
        for c in self.cut_files():
            k = version_key(c["name"])
            if k:
                groups.setdefault(k, []).append(c)
        crits = self.critiques()
        sources = self.sources()
        out = []
        for k in sorted(groups):
            cuts = sorted(groups[k], key=self._rank)
            ready = [c for c in cuts if c["ready"]]
            if not ready:
                # still being written (no moov yet): list it so the UI can say so, but it can't be played
                out.append({"id": k, "label": k, "kind": "cut", "cut": cuts[0], "alternates": [], "pending": True,
                            "critiques": [], "sources": [], "studio": None})
                continue
            vc = [c for c in crits if c["key"] == k]
            out.append({"id": k, "label": k, "kind": "cut", "cut": ready[0], "alternates": ready[1:], "pending": False,
                        "critiques": vc, "sources": [s.id for s in sources if s.version == k], "studio": None})
        for c in self.studio_cuts(active_names):
            meta = c.get("meta") or {}
            out.append({"id": f"studio-{c['name']}", "label": c["name"], "kind": "studio", "cut": c, "alternates": [],
                        "pending": not c["ready"], "critiques": [], "sources": [], "studio": meta})
        return out

    def version(self, vid: str) -> dict | None:
        return next((v for v in self.versions() if v["id"] == vid), None)

    def cut_by_id(self, cut_id: str) -> dict | None:
        for c in self.cut_files() + self.studio_cuts():
            if c["id"] == cut_id:
                return c
        return None

    # --- per-shot sources ---------------------------------------------------------------------------------------------
    def sources(self) -> list[Source]:
        def load():
            ids = self.shot_ids()
            out: list[Source] = []
            snapdir = self.s.root / "renders/snapshots"
            for d in sorted(snapdir.iterdir()) if snapdir.is_dir() else []:
                if d.is_dir() and (d / "shots").is_dir():
                    k = version_key(d.name)
                    out.append(Source(d.name, d.name.replace("_", " · ", 1) if k else d.name, "snapshot", k,
                                      d / "shots", FINAL_FIRST))
            fz = self.s.frozen_dir
            for d in sorted(fz.iterdir()) if fz.is_dir() else []:
                if d.is_dir() and (d / "shots").is_dir():
                    k = version_key(d.name)
                    out.append(Source(f"frozen-{d.name}", f"{d.name} · frozen", "frozen", k, d / "shots", CYCLE_FIRST))
            out.append(Source("live", "live · renders/shots", "live", None, self.s.root / "renders/shots", CYCLE_FIRST))
            for src in out:
                for sid in ids:
                    f = _shot_in_dir(src.dir, sid, src.order)
                    if f:
                        src.files[sid] = f
            # newest versions first, live last
            out.sort(key=lambda s: (s.kind == "live", -(int(s.version[1:]) if s.version else 0), s.id))
            return out
        return self._cached("sources", 3, load)

    def source(self, sid_source: str) -> Source | None:
        return next((s for s in self.sources() if s.id == sid_source), None)

    def shot_file(self, source_id: str, sid: str) -> ShotFile | None:
        src = self.source(source_id)
        return src.files.get(sid) if src else None
