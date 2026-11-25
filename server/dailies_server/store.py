"""Annotations and saved timelines, persisted as pretty, git-friendly JSON under notes/studio/.

notes/studio/annotations.json   {"schema": 1, "fps": 30, "annotations": [...]}, sorted by version, frame, id
notes/studio/frames/<id>.png    the annotated frame with its drawing composited (written by the browser)
notes/studio/timelines/<n>.json {"name", "created", "updated", "note", "shots": {sid: {source, tier, path}}}

An annotation: id, version, cut (file it was made on), time (s), frame, shot, tag (problem|like|idea), text,
status (open|done), author, created, updated, optional drawing {aspect, shapes:[...]} with coordinates normalised to
0-1 of the video frame, and frame_png when a composited still exists.
"""
from __future__ import annotations

import io
import math
import re
import secrets
import threading
import time
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .catalog import TAGS, Catalog
from .config import Settings
from .safepath import rel_to_root
from .util import atomic_write_bytes, atomic_write_json, now_iso, read_json, valid_name

# used with fullmatch: "$" would also accept a trailing newline
COLOR_RE = re.compile(r"#[0-9a-fA-F]{6}")
ID_RE = re.compile(r"a-\d{8}-\d{6}-[0-9a-f]{4}")
# json.loads accepts NaN / Infinity, which are not JSON: refuse them in every number of every model below
FINITE = ConfigDict(allow_inf_nan=False)
MAX_POINTS = 40_000
MAX_PNG = 20 * 1024 * 1024


def _r(v: float) -> float:
    return round(float(v), 4)


class Shape(BaseModel):
    model_config = FINITE
    type: Literal["pen", "arrow", "rect", "ellipse", "text"]
    color: str = "#ef541e"
    size: float = Field(0.006, gt=0, le=0.2)       # stroke width (or text height) as a fraction of frame height
    points: list[list[float]] | None = None        # pen: [[x, y, pressure], ...]
    a: list[float] | None = None                   # arrow/rect/ellipse start, text anchor
    b: list[float] | None = None                   # arrow/rect/ellipse end
    text: str | None = Field(None, max_length=300)

    @field_validator("color")
    @classmethod
    def _color(cls, v: str) -> str:
        if not COLOR_RE.fullmatch(v):
            raise ValueError("color must be #rrggbb")
        return v.lower()

    @model_validator(mode="after")
    def _geometry(self):
        def pt(p, n=(2,)):
            # x, y may overshoot the picture a little; pressure is 0..1 (comparisons are False for NaN too)
            if p is None or len(p) not in n or any(not (-0.5 <= x <= 1.5) for x in p[:2]) \
                    or any(not (0 <= x <= 1) for x in p[2:]):
                raise ValueError(f"{self.type}: bad point {p}")
            return [_r(x) for x in p]
        if self.type == "pen":
            if not self.points or len(self.points) > MAX_POINTS:
                raise ValueError("pen needs 1..40000 points")
            self.points = [pt(p, (2, 3)) for p in self.points]
        elif self.type == "text":
            self.a = pt(self.a)
            if not (self.text or "").strip():
                raise ValueError("text shape needs text")
        else:
            self.a, self.b = pt(self.a), pt(self.b)
        self.size = _r(self.size)
        return self


class Drawing(BaseModel):
    model_config = FINITE
    aspect: float = Field(16 / 9, gt=0.2, lt=5)
    shapes: list[Shape] = Field(default_factory=list, max_length=500)

    @model_validator(mode="after")
    def _budget(self):
        if sum(len(s.points or []) for s in self.shapes) > MAX_POINTS:
            raise ValueError("drawing too large")
        self.aspect = _r(self.aspect)
        return self


class AnnotationIn(BaseModel):
    model_config = FINITE
    version: str = Field(..., min_length=1, max_length=96)
    cut: str | None = Field(None, max_length=400)
    time: float = Field(..., ge=0, le=3600)
    frame: int | None = Field(None, ge=0, le=200_000)
    shot: str | None = Field(None, max_length=8)
    tag: Literal["problem", "like", "idea"] = "problem"
    text: str = Field("", max_length=4000)
    status: Literal["open", "done"] = "open"
    drawing: Drawing | None = None


class AnnotationPatch(BaseModel):
    model_config = FINITE
    time: float | None = Field(None, ge=0, le=3600)
    frame: int | None = Field(None, ge=0, le=200_000)
    shot: str | None = Field(None, max_length=8)
    tag: Literal["problem", "like", "idea"] | None = None
    text: str | None = Field(None, max_length=4000)
    status: Literal["open", "done"] | None = None
    drawing: Drawing | None = None
    clear_drawing: bool = False


class StoreError(ValueError):
    pass


class Annotations:
    def __init__(self, settings: Settings, catalog: Catalog):
        self.s = settings
        self.cat = catalog
        self._lock = threading.Lock()

    def _read(self) -> dict:
        d = read_json(self.s.annotations_file, None)
        if not isinstance(d, dict) or not isinstance(d.get("annotations"), list):
            d = {"schema": 1, "fps": self.s.fps, "annotations": []}
        return d

    def _write(self, d: dict) -> None:
        d["annotations"].sort(key=lambda a: (a.get("version", ""), a.get("frame", 0), a.get("id", "")))
        d["updated"] = now_iso()
        atomic_write_json(self.s.annotations_file, d)

    def list(self) -> list[dict]:
        with self._lock:
            return self._read()["annotations"]

    def get(self, aid: str) -> dict | None:
        return next((a for a in self.list() if a["id"] == aid), None)

    @staticmethod
    def _cut_in(version: dict, path: str | None) -> dict | None:
        """The cut entry a note on this version was made on (`path`, else the version's own cut), or None."""
        if not path:
            return version["cut"]
        return next((c for c in (version["cut"], *version["alternates"]) if c["path"] == path), None)

    def _normalise(self, a: dict, cut: dict | None = None) -> None:
        # the frame is canonical and in film frames, the same in every version (a partial cut's own frame 0 is film
        # frame cut["film_f0"]); time is that frame's start (what requestVideoFrameCallback reports as mediaTime)
        fps = self.s.fps
        if a.get("frame") is None:
            a["frame"] = int(math.floor(a["time"] * fps + 1e-6))   # the frame on screen at that time
        a["time"] = round(a["frame"] / fps, 4)
        if cut:
            f0, f1 = cut.get("film_f0") or 0, cut.get("film_f1")
            if a["frame"] < f0 or (f1 is not None and a["frame"] >= f1):
                raise StoreError(f"frame {a['frame']} is outside {cut['name']}, which has film frames "
                                 f"{f0}-{'' if f1 is None else f1 - 1}")
        ids = set(self.cat.shot_ids())
        if a.get("shot") and a["shot"] not in ids:
            raise StoreError(f"unknown shot {a['shot']}")
        if not a.get("shot"):
            a["shot"] = self.cat.shot_at_frame(a["frame"])

    def create(self, body: AnnotationIn, author: str) -> dict:
        v = next((x for x in self.cat.versions() if x["id"] == body.version), None)
        if not v:
            raise StoreError(f"unknown version {body.version}")
        cut = self._cut_in(v, body.cut)
        if not cut:
            raise StoreError(f"{body.cut} is not a cut of {body.version}")
        aid = time.strftime("a-%Y%m%d-%H%M%S-") + secrets.token_hex(2)
        a = {"id": aid, "version": body.version, "cut": body.cut, "time": body.time, "frame": body.frame,
             "shot": body.shot, "tag": body.tag, "text": body.text.strip(), "status": body.status, "author": author,
             "created": now_iso(), "updated": now_iso(),
             "drawing": body.drawing.model_dump(exclude_none=True) if body.drawing else None, "frame_png": None}
        self._normalise(a, cut)
        with self._lock:
            d = self._read()
            d["annotations"].append(a)
            self._write(d)
        return a

    def update(self, aid: str, patch: AnnotationPatch) -> dict | None:
        ch = patch.model_dump(exclude_unset=True)
        moved = "time" in ch or "frame" in ch
        versions = self.cat.versions() if moved else []     # (may ffprobe: not under the lock)
        with self._lock:
            d = self._read()
            a = next((x for x in d["annotations"] if x["id"] == aid), None)
            if not a:
                return None
            if "time" in ch and "frame" not in ch:
                a["frame"] = None
            for k in ("time", "frame", "shot", "tag", "status"):
                if k in ch and ch[k] is not None:
                    a[k] = ch[k]
            if "text" in ch and ch["text"] is not None:
                a["text"] = ch["text"].strip()
            if patch.clear_drawing:
                a["drawing"] = None
            elif patch.drawing is not None:
                a["drawing"] = patch.drawing.model_dump(exclude_none=True)
            if moved or "shot" in ch:                  # a new shot is checked against the EDL, as on create
                if moved and "shot" not in ch:
                    a["shot"] = None                   # the shot follows the moved frame
                v = next((x for x in versions if x["id"] == a.get("version")), None)
                self._normalise(a, self._cut_in(v, a.get("cut")) if v else None)
            a["updated"] = now_iso()
            self._write(d)
            return a

    def delete(self, aid: str) -> bool:
        if not ID_RE.fullmatch(aid):
            return False
        with self._lock:
            d = self._read()
            n = len(d["annotations"])
            d["annotations"] = [a for a in d["annotations"] if a["id"] != aid]
            if len(d["annotations"]) == n:
                return False
            self._write(d)
        png = self.s.frames_dir / f"{aid}.png"
        png.unlink(missing_ok=True)
        return True

    def save_frame(self, aid: str, data: bytes) -> dict:
        """Store the browser-composited frame as notes/studio/frames/<id>.png after checking it really is a PNG."""
        if not ID_RE.fullmatch(aid):
            raise StoreError("bad id")
        if len(data) > MAX_PNG:
            raise StoreError("png too large")
        if not data.startswith(b"\x89PNG\r\n\x1a\n"):
            raise StoreError("not a png")
        from PIL import Image
        try:
            with Image.open(io.BytesIO(data)) as im:
                if im.format != "PNG" or im.width > 4096 or im.height > 4096:
                    raise StoreError("png must be at most 4096x4096")
                im.verify()
        except StoreError:
            raise
        except Exception as e:  # noqa: BLE001
            raise StoreError(f"unreadable png: {e}") from None
        with self._lock:
            d = self._read()
            a = next((x for x in d["annotations"] if x["id"] == aid), None)
            if not a:
                raise KeyError(aid)
            out = self.s.frames_dir / f"{aid}.png"
            atomic_write_bytes(out, data)
            a["frame_png"] = rel_to_root(self.s.root, out)
            a["updated"] = now_iso()
            self._write(d)
            return a


class TimelineIn(BaseModel):
    note: str = Field("", max_length=2000)
    shots: dict[str, str]          # sid -> source id


class Timelines:
    def __init__(self, settings: Settings, catalog: Catalog):
        self.s = settings
        self.cat = catalog
        self._lock = threading.Lock()

    def _path(self, name: str) -> Path:
        if not valid_name(name):
            raise StoreError("timeline name must be 1-64 chars of letters, digits, '.', '_' or '-'")
        return self.s.timelines_dir / f"{name}.json"

    def list(self) -> list[dict]:
        d = self.s.timelines_dir
        out = []
        for p in sorted(d.glob("*.json")) if d.is_dir() else []:
            t = read_json(p)
            if isinstance(t, dict):
                out.append({"name": p.stem, "updated": t.get("updated"), "note": t.get("note", ""),
                            "shots": {k: v.get("source") for k, v in (t.get("shots") or {}).items()}})
        return out

    def get(self, name: str) -> dict | None:
        return read_json(self._path(name))

    def put(self, name: str, body: TimelineIn) -> dict:
        p = self._path(name)
        ids = self.cat.shot_ids()
        bad = [k for k in body.shots if k not in ids]
        if bad:
            raise StoreError(f"unknown shots: {', '.join(bad)}")
        shots = {}
        for sid in ids:                     # EDL order, with the resolved file for whoever reads the JSON later
            src = body.shots.get(sid)
            if not src:
                continue
            f = self.cat.shot_file(src, sid)
            if not f:
                raise StoreError(f"source {src} has no {sid}")
            shots[sid] = {"source": src, "tier": f.tier, "path": rel_to_root(self.s.root, f.path)}
        with self._lock:
            old = read_json(p, {}) or {}
            t = {"name": name, "created": old.get("created") or now_iso(), "updated": now_iso(),
                 "note": body.note, "shots": shots}
            atomic_write_json(p, t)
        return t

    def delete(self, name: str) -> bool:
        p = self._path(name)
        if not p.exists():
            return False
        p.unlink()
        return True


__all__ = ["Annotations", "AnnotationIn", "AnnotationPatch", "Timelines", "TimelineIn", "StoreError", "TAGS"]
