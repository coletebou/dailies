"""Browser-playable proxies and thumbnails, generated lazily and warmed in the background at low priority.

  renders/studio/proxies/<source>/<sid>.mp4    H.264 720p, 15-frame GOP (snappy scrubbing), no audio
  renders/studio/proxies/<source>/<sid>.jpg    mid-shot thumbnail, 384 px wide
  renders/studio/proxies/_cuts/<cut-id>.mp4    720p H.264 + original AAC, for remote viewing of 40 Mbps masters
  renders/studio/proxies/_live/<dir>/<f>.jpg   still of a snapshot part or working preview (Live panel)
  renders/studio/audio/mix_master.m4a          AAC of music/mix_master.wav for in-browser timeline preview

Each output has a <output>.src.json sidecar holding the source path, mtime and size it was made from; a stale or
missing sidecar means regenerate. ffmpeg always runs under `nice -n 15` with 2 threads, and the warm-up loop pauses
while the machine's load average is high (renders may share it).
"""
from __future__ import annotations

import logging
import os
import queue
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from .catalog import Catalog
from .config import Settings
from .util import atomic_write_json, file_sig, read_json

log = logging.getLogger("dailies.proxies")


@dataclass(frozen=True)
class Task:
    kind: str          # thumb | proxy | cutproxy | audio
    key: str           # unique output key
    src: Path
    out: Path
    seek: float = 0.0
    frames: int = 0


class ProxyManager:
    def __init__(self, settings: Settings, catalog: Catalog):
        self.s = settings
        self.cat = catalog
        self._inflight: dict[str, threading.Event] = {}
        self._lock = threading.Lock()
        self._demand: queue.PriorityQueue = queue.PriorityQueue()
        self._seq = 0
        self._stop = threading.Event()
        self._procs: set[subprocess.Popen] = set()
        self._threads: list[threading.Thread] = []
        self.stats = {"made": 0, "failed": 0, "warm_pending": 0, "warm_state": "idle", "last_error": None}
        self._failed: dict[str, tuple] = {}   # key -> src sig that failed (don't retry the same input forever)

    # --- paths -------------------------------------------------------------------------------------------------------
    def shot_task(self, kind: str, source_id: str, sid: str) -> Task | None:
        f = self.cat.shot_file(source_id, sid)
        if not f:
            return None
        shot = self.cat.shot_by_id().get(sid, {})
        frames = int(shot.get("frames", 0))
        d = self.s.proxies_dir / source_id
        if kind == "thumb":
            return Task("thumb", f"thumb:{source_id}:{sid}", f.path, d / f"{sid}.jpg", seek=max(0.0, frames / 2 / self.s.fps))
        return Task("proxy", f"proxy:{source_id}:{sid}", f.path, d / f"{sid}.mp4", frames=frames)

    def cut_task(self, cut: dict) -> Task:
        src = self.s.root / cut["path"]
        return Task("cutproxy", f"cut:{cut['id']}", src, self.s.proxies_dir / "_cuts" / f"{cut['id']}.mp4")

    def live_thumb_task(self, p: Path) -> Task:
        """Still for a Live card: a snapshot part (first frame) or a working preview (1 s in, past any fade-in)."""
        if p.parent.name == "parts":
            return Task("thumb", f"live:{p}", p, self.s.proxies_dir / "_live" / p.parent.parent.name / f"{p.name}.jpg")
        return Task("thumb", f"live:{p}", p, self.s.proxies_dir / "_live" / p.parent.name / f"{p.name}.jpg", seek=1.0)

    def audio_task(self) -> Task:
        return Task("audio", "audio:mix", self.s.root / "music/mix_master.wav", self.s.studio_dir / "audio" / "mix_master.m4a")

    @staticmethod
    def _sidecar(out: Path) -> Path:
        return out.with_name(out.name + ".src.json")

    def fresh(self, t: Task) -> bool:
        if not t.out.is_file():
            return False
        meta = read_json(self._sidecar(t.out), {})
        sig = file_sig(t.src)
        return bool(sig) and meta.get("src") == str(t.src) and meta.get("sig") == list(sig)

    def failed(self, t: Task) -> bool:
        """Generation from the source as it is now already failed (it is retried once the source changes)."""
        sig = file_sig(t.src)
        return bool(sig) and self._failed.get(t.key) == sig

    def url_version(self, t: Task) -> int:
        sig = file_sig(t.src)
        return (sig[0] // 1_000_000) if sig else 0

    # --- generation --------------------------------------------------------------------------------------------------
    def _cmd(self, t: Task, tmp: Path) -> list[str]:
        nice = ["nice", "-n", str(self.s.nice_proxy)]
        th = str(self.s.ffmpeg_threads)
        # -threads alone doesn't cap x264's lookahead or the scaler; pin all three so a proxy uses ~2 cores at most
        base = nice + ["ffmpeg", "-nostdin", "-v", "error", "-y", "-filter_threads", "1", "-threads", th]
        x264 = ["-x264-params", f"threads={th}:lookahead-threads=1"]
        if t.kind == "thumb":
            return base + ["-ss", f"{t.seek:.3f}", "-i", str(t.src), "-frames:v", "1",
                           "-vf", "scale=384:-2:flags=bicubic", "-q:v", "4", "-f", "image2", str(tmp)]
        if t.kind == "proxy":
            lim = ["-frames:v", str(t.frames)] if t.frames else []
            return base + ["-i", str(t.src), "-map", "0:v:0", "-an", *lim,
                           "-vf", "scale=-2:720:flags=bicubic,format=yuv420p", "-c:v", "libx264", "-preset", "veryfast",
                           "-crf", "22", "-g", "15", "-keyint_min", "15", "-sc_threshold", "0", *x264,
                           "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709",
                           "-movflags", "+faststart", "-f", "mp4", str(tmp)]
        if t.kind == "cutproxy":
            return base + ["-i", str(t.src), "-map", "0:v:0", "-map", "0:a:0?",
                           "-vf", "scale=-2:720:flags=bicubic,format=yuv420p", "-c:v", "libx264", "-preset", "veryfast",
                           "-crf", "23", "-g", "15", "-keyint_min", "15", "-sc_threshold", "0", *x264,
                           "-c:a", "copy", "-movflags", "+faststart", "-f", "mp4", str(tmp)]
        if t.kind == "audio":
            return base + ["-i", str(t.src), "-vn", "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
                           "-movflags", "+faststart", "-f", "mp4", str(tmp)]
        raise ValueError(t.kind)

    def _generate(self, t: Task) -> bool:
        sig = file_sig(t.src)
        if not sig:
            return False
        if self._failed.get(t.key) == sig:
            return False
        t.out.parent.mkdir(parents=True, exist_ok=True)
        tmp = t.out.with_name(f".{t.out.name}.{os.getpid()}.tmp")
        cmd = self._cmd(t, tmp)
        t0 = time.monotonic()
        try:
            p = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            with self._lock:
                self._procs.add(p)
            try:
                _, err = p.communicate(timeout=1800)
            finally:
                with self._lock:
                    self._procs.discard(p)
            if p.returncode != 0 or not tmp.is_file() or tmp.stat().st_size == 0:
                # a thumbnail seek past a short clip's end yields nothing; retry from the first frame
                if t.kind == "thumb" and t.seek > 0:
                    return self._generate(Task(t.kind, t.key, t.src, t.out, 0.0, t.frames))
                raise RuntimeError((err or b"").decode(errors="replace")[-400:] or f"rc={p.returncode}")
            # the source may have been rewritten while we read it; only stamp the signature we started from
            os.replace(tmp, t.out)
            atomic_write_json(self._sidecar(t.out), {"src": str(t.src), "sig": list(sig), "made": time.time(),
                                                     "secs": round(time.monotonic() - t0, 2)})
            self.stats["made"] += 1
            self._failed.pop(t.key, None)
            log.info("made %s %s in %.1fs", t.kind, t.out.name, time.monotonic() - t0)
            return True
        except Exception as e:   # noqa: BLE001 - one bad input must never stop the worker
            self.stats["failed"] += 1
            self.stats["last_error"] = f"{t.key}: {e}"
            self._failed[t.key] = sig
            log.warning("proxy failed %s: %s", t.key, e)
            try:
                tmp.unlink()
            except FileNotFoundError:
                pass
            return False

    def run(self, t: Task) -> bool:
        """Generate t unless fresh; concurrent callers for the same key wait for the one in flight."""
        if self.fresh(t):
            return True
        with self._lock:
            ev = self._inflight.get(t.key)
            owner = ev is None
            if owner:
                ev = threading.Event()
                self._inflight[t.key] = ev
        if not owner:
            ev.wait(timeout=1800)
            return self.fresh(t)
        try:
            return self._generate(t)
        finally:
            with self._lock:
                self._inflight.pop(t.key, None)
            ev.set()

    # --- on-demand ---------------------------------------------------------------------------------------------------
    def request(self, t: Task, prio: int = 0) -> threading.Event:
        """Queue t on the demand worker (ahead of warm-up) and return an event set when it is done."""
        done = threading.Event()
        if self.fresh(t):
            done.set()
            return done
        with self._lock:
            self._seq += 1
            seq = self._seq
        self._demand.put((prio, seq, t, done))
        return done

    def _demand_loop(self) -> None:
        while not self._stop.is_set():
            try:
                _, _, t, done = self._demand.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                self.run(t)
            finally:
                done.set()

    # --- warm-up -----------------------------------------------------------------------------------------------------
    def _busy(self) -> bool:
        try:
            return os.getloadavg()[0] / (os.cpu_count() or 1) > self.s.warm_max_load
        except OSError:
            return False

    def warm_tasks(self) -> list[Task]:
        tasks: list[Task] = []
        sources = self.cat.sources()
        ids = self.cat.shot_ids()
        for v in reversed(self.cat.versions()):   # remote-friendly cut proxies first, newest version first
            if not v.get("pending") and v["kind"] == "cut":
                t = self.cut_task(v["cut"])
                if not self.fresh(t):
                    tasks.append(t)
        for src in sources:                       # thumbnails: cheap, and the UI needs them everywhere
            for sid in ids:
                if sid in src.files:
                    t = self.shot_task("thumb", src.id, sid)
                    if t and not self.fresh(t):
                        tasks.append(t)
        at = self.audio_task()
        if at.src.is_file() and not self.fresh(at):
            tasks.append(at)
        for src in sources:                       # then shot proxies, newest version first, live last
            for sid in ids:
                if sid in src.files:
                    t = self.shot_task("proxy", src.id, sid)
                    if t and not self.fresh(t):
                        tasks.append(t)
        return tasks

    def _warm_loop(self) -> None:
        time.sleep(5)
        while not self._stop.is_set():
            try:
                tasks = [t for t in self.warm_tasks() if self._failed.get(t.key) != file_sig(t.src)]
            except Exception as e:  # noqa: BLE001
                log.warning("warm scan failed: %s", e)
                tasks = []
            self.stats["warm_pending"] = len(tasks)
            if not tasks:
                self.stats["warm_state"] = "idle"
                self._stop.wait(60)
                continue
            for t in tasks:
                if self._stop.is_set():
                    return
                while self._busy() and not self._stop.is_set():
                    self.stats["warm_state"] = "paused (machine busy)"
                    self._stop.wait(30)
                # live renders still being written are skipped until they settle for 60 s
                sig = file_sig(t.src)
                if sig and time.time() - sig[0] / 1e9 < 60:
                    continue
                self.stats["warm_state"] = f"making {t.kind} {t.out.parent.name}/{t.out.name}"
                self.run(t)
                self.stats["warm_pending"] = max(0, self.stats["warm_pending"] - 1)
            self.stats["warm_state"] = "idle"
            self._stop.wait(20)

    # --- lifecycle ---------------------------------------------------------------------------------------------------
    def start(self) -> None:
        # temp outputs of encodes that a previous server process was stopped in the middle of
        for tmp in self.s.studio_dir.glob("**/.*.tmp") if self.s.studio_dir.is_dir() else []:
            if tmp.parent.is_relative_to(self.s.proxies_dir) or tmp.parent == self.s.studio_dir / "audio":
                tmp.unlink(missing_ok=True)
        th = threading.Thread(target=self._demand_loop, name="proxy-demand", daemon=True)
        th.start()
        self._threads.append(th)
        if self.s.warm:
            tw = threading.Thread(target=self._warm_loop, name="proxy-warm", daemon=True)
            tw.start()
            self._threads.append(tw)

    def stop(self) -> None:
        self._stop.set()
        with self._lock:
            procs = list(self._procs)
        for p in procs:
            try:
                p.terminate()
            except ProcessLookupError:
                pass
