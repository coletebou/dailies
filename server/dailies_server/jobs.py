"""Build cut: turn a timeline (one chosen source per shot) into a full video with the project's own assembler.

plan_build() is pure: it maps the selection to master files and the exact assemble.py command. BuildManager runs one
build at a time in the background:

  renders/studio/tmp/<job>/shots/<sid>_<tier><ext>  -> symlinks to the chosen masters
  nice -n 10 .venv/bin/python server/dailies_server/assemble_progress.py \
      --tier final --res 1920x1080 --shots-dir <tmp> --audio music/mix_master.wav \
      --out renders/studio/cuts/<name>.mp4 --no-latest [--from sA --to sB]

assemble_progress.py is a thin wrapper that runs tools/assemble.py's main() unchanged and prints frame progress.
Job state lives in renders/studio/jobs/<id>.json (+ .log); a finished cut gets renders/studio/cuts/<name>.json and a
git-friendly copy of its recipe at notes/studio/cuts/<name>.json.
"""
from __future__ import annotations

import logging
import os
import secrets
import shutil
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from .catalog import Catalog
from .config import Settings
from .safepath import rel_to_root
from .util import atomic_write_json, ffprobe, now_iso, read_json, valid_name

log = logging.getLogger("dailies.jobs")


class BuildError(ValueError):
    pass


@dataclass
class BuildPlan:
    name: str
    shots: list[str]                    # shot ids in range, EDL order
    links: list[tuple[str, Path, str]]  # (sid, master path, link name)
    selection: dict[str, dict]          # sid -> {source, tier, path}
    out: Path
    f_from: str | None
    f_to: str | None
    frames: int
    cmd: list[str] = field(default_factory=list)
    film_f0: int = 0                    # film frame that is the cut's own frame 0 (assemble.py starts at --from)
    film_f1: int = 0                    # film frame just past its last


def plan_build(s: Settings, cat: Catalog, name: str, selection: dict[str, str], f_from: str | None = None,
               f_to: str | None = None, shots_dir: Path | None = None) -> BuildPlan:
    """Validate a build request and map it to masters + the assemble.py command. Client sends source ids only;
    file paths are always resolved here from the catalog, never taken from the request."""
    if not valid_name(name):
        raise BuildError("cut name must be 1-64 chars of letters, digits, '.', '_' or '-'")
    edl = cat.edl()["shots"]
    ids = [x["id"] for x in edl]
    if f_from and f_from not in ids:
        raise BuildError(f"unknown shot {f_from}")
    if f_to and f_to not in ids:
        raise BuildError(f"unknown shot {f_to}")
    # from defaults to the first shot and to the last; assemble.py gets both flags whenever the range is partial
    i0 = ids.index(f_from) if f_from else 0
    i1 = ids.index(f_to) if f_to else len(ids) - 1
    if i1 < i0:
        raise BuildError("range end comes before its start")
    rng = edl[i0:i1 + 1]
    unknown = sorted(set(selection) - set(ids))
    if unknown:
        raise BuildError(f"unknown shots in selection: {', '.join(unknown)}")
    links, sel, missing = [], {}, []
    for shot in rng:
        sid = shot["id"]
        src_id = selection.get(sid)
        f = cat.shot_file(src_id, sid) if src_id else None
        if not f:
            missing.append(f"{sid}" + (f" ({src_id} has no {sid})" if src_id else ""))
            continue
        links.append((sid, f.path, f"{sid}_{f.tier}{f.path.suffix}"))
        sel[sid] = {"source": src_id, "tier": f.tier, "path": rel_to_root(s.root, f.path)}
    if missing:
        raise BuildError("no source for: " + ", ".join(missing))
    out = s.cuts_dir / f"{name}.mp4"
    partial = i0 > 0 or i1 < len(ids) - 1
    plan = BuildPlan(name, [x["id"] for x in rng], links, sel, out,
                     rng[0]["id"] if partial else None, rng[-1]["id"] if partial else None,
                     sum(x["frames"] for x in rng))
    plan.film_f0 = rng[0]["f0"]
    plan.film_f1 = plan.film_f0 + plan.frames
    sd = shots_dir or (s.tmp_dir / "<job>" / "shots")
    plan.cmd = ["nice", "-n", str(s.nice_build), str(s.python),
                str(Path(__file__).resolve().parent / "assemble_progress.py"),
                "--tier", "final", "--res", "1920x1080", "--shots-dir", str(sd),
                "--audio", "music/mix_master.wav", "--out", rel_to_root(s.root, out), "--no-latest"]
    if plan.f_from:
        plan.cmd += ["--from", plan.f_from, "--to", plan.f_to]
    return plan


def materialize(plan: BuildPlan, shots_dir: Path) -> None:
    """Create the temp shots dir: one symlink per shot, named so assemble.py's tier fallback finds exactly it."""
    shots_dir.mkdir(parents=True, exist_ok=False)
    for _sid, src, link in plan.links:
        os.symlink(src, shots_dir / link)


class BuildManager:
    def __init__(self, settings: Settings, catalog: Catalog):
        self.s = settings
        self.cat = catalog
        self._lock = threading.Lock()
        self._cv = threading.Condition(self._lock)
        self._queue: list[str] = []
        self._jobs: dict[str, dict] = {}
        self._proc: subprocess.Popen | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._load()

    # --- persistence ---------------------------------------------------------------------------------------------------
    def _job_file(self, jid: str) -> Path:
        return self.s.jobs_dir / f"{jid}.json"

    def _save(self, job: dict) -> None:
        atomic_write_json(self._job_file(job["id"]), job)

    def _load(self) -> None:
        d = self.s.jobs_dir
        if not d.is_dir():
            return
        for p in sorted(d.glob("*.json")):
            job = read_json(p)
            if not isinstance(job, dict) or "id" not in job:
                continue
            if job.get("status") in ("queued", "running"):
                # the server restarted under it: stop its orphaned assembler, if that is still running, and mark it
                if self._owns_group(job):
                    try:
                        os.killpg(job["pgid"], signal.SIGTERM)
                    except (ProcessLookupError, PermissionError):
                        pass
                job["error"] = f"server restarted while this build was {job['status']}"
                job["status"] = "interrupted"
                job["finished"] = now_iso()
                self._retire_partial(job)
                self._cleanup_tmp(job)
                self._save(job)
            self._jobs[job["id"]] = job

    @staticmethod
    def _owns_group(job: dict) -> bool:
        """True only if the job's recorded process group still leads with this job's assemble_progress.py. After a
        reboot (or long enough) the pid belongs to something else, and that must never be signalled."""
        pg = job.get("pgid")
        if not isinstance(pg, int) or pg <= 1:
            return False
        try:
            out = subprocess.run(["ps", "-ww", "-o", "pgid=,command=", "-p", str(pg)], capture_output=True, text=True,
                                 timeout=10).stdout.split(None, 1)
        except (OSError, subprocess.SubprocessError):
            return False
        # the leader is `nice` exec'd into python: its command line names the wrapper and the job's own temp dir
        return len(out) == 2 and out[0] == str(pg) and "assemble_progress.py" in out[1] and job["id"] in out[1]

    # --- public API ----------------------------------------------------------------------------------------------------
    def list(self, limit: int = 50) -> list[dict]:
        with self._lock:
            jobs = sorted(self._jobs.values(), key=lambda j: j["created"], reverse=True)[:limit]
            return [dict(j) for j in jobs]

    def get(self, jid: str) -> dict | None:
        with self._lock:
            j = self._jobs.get(jid)
            return dict(j) if j else None

    def log_tail(self, jid: str, n: int = 40) -> list[str]:
        p = self.s.jobs_dir / f"{jid}.log"
        try:
            with open(p, "rb") as fh:
                fh.seek(0, os.SEEK_END)
                size = fh.tell()
                fh.seek(max(0, size - 16000))
                lines = fh.read().decode(errors="replace").splitlines()
        except FileNotFoundError:
            return []
        return [ln for ln in lines if not ln.startswith("DAILIES_PROGRESS")][-n:]

    def active_names(self) -> set[str]:
        with self._lock:
            return {j["name"] for j in self._jobs.values() if j["status"] in ("queued", "running")}

    def submit(self, name: str, selection: dict[str, str], f_from: str | None, f_to: str | None,
               timeline: str | None = None, user: str = "local") -> dict:
        plan = plan_build(self.s, self.cat, name, selection, f_from, f_to)
        with self._lock:
            if plan.out.exists() or plan.out.with_suffix(".json").exists():
                raise BuildError(f"a cut named {name} already exists; pick another name")
            if name in {j["name"] for j in self._jobs.values() if j["status"] in ("queued", "running")}:
                raise BuildError(f"a build named {name} is already queued")
            jid = time.strftime("b%Y%m%d-%H%M%S-") + secrets.token_hex(2)
            job = {"id": jid, "name": name, "status": "queued", "created": now_iso(), "started": None,
                   "finished": None, "user": user, "timeline": timeline, "from": plan.f_from, "to": plan.f_to,
                   "shots": plan.shots, "selection": plan.selection, "frames_total": plan.frames, "frames_done": 0,
                   "current_shot": None, "out": rel_to_root(self.s.root, plan.out), "error": None, "pgid": None,
                   "cmd": None, "eta_s": None}
            self._jobs[jid] = job
            self._queue.append(jid)
            self._save(job)
            self._cv.notify_all()
        return dict(job)

    def cancel(self, jid: str) -> dict | None:
        """queued: drop it. running: stop assemble.py, or, before it has started, make the worker skip it. Anything
        else has already finished and is returned as is."""
        with self._lock:
            job = self._jobs.get(jid)
            if not job:
                return None
            if job["status"] == "queued":       # still in the queue: the worker dequeues and starts under this lock
                self._queue.remove(jid)
                job.update(status="cancelled", finished=now_iso())
                self._save(job)
            elif job["status"] == "running":
                job["cancel_requested"] = True
                self._save(job)
                if job.get("pgid"):
                    try:
                        os.killpg(job["pgid"], signal.SIGTERM)
                    except ProcessLookupError:
                        pass
            return dict(job)

    # --- worker --------------------------------------------------------------------------------------------------------
    def _cleanup_tmp(self, job: dict) -> None:
        d = self.s.tmp_dir / job["id"]
        if d.is_dir():
            # contains only the symlinks we made; rmtree never follows them into the masters
            shutil.rmtree(d, ignore_errors=True)

    def _retire_partial(self, job: dict) -> None:
        out = self.s.root / job["out"]
        if out.exists():
            out.rename(out.with_name(f"{out.stem}.failed-{job['id']}.mp4"))

    def _run(self, jid: str) -> None:
        with self._lock:
            job = self._jobs[jid]
        shots_dir = self.s.tmp_dir / jid / "shots"
        logf = self.s.jobs_dir / f"{jid}.log"
        try:
            sel = {sid: v["source"] for sid, v in job["selection"].items()}
            plan = plan_build(self.s, self.cat, job["name"], sel, job["from"], job["to"], shots_dir=shots_dir)
            materialize(plan, shots_dir)
            self.s.cuts_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:  # noqa: BLE001
            with self._lock:
                job.update(status="cancelled" if job.get("cancel_requested") else "failed", finished=now_iso(),
                           error=None if job.get("cancel_requested") else str(e))
                self._save(job)
            self._cleanup_tmp(job)
            return
        t0 = time.monotonic()
        with self._lock:
            if job.get("cancel_requested"):      # cancelled while the shots were being linked
                job.update(status="cancelled", finished=now_iso())
                self._save(job)
                cancelled = True
            else:
                job["cmd"] = plan.cmd
                self._save(job)
                cancelled = False
        if cancelled:
            self._cleanup_tmp(job)
            log.info("build %s cancelled before it started (%s)", jid, job["name"])
            return
        with open(logf, "ab") as lf:
            lf.write(("$ " + " ".join(plan.cmd) + "\n").encode())
            lf.flush()
            p = subprocess.Popen(plan.cmd, cwd=self.s.root, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                 start_new_session=True, env={**os.environ, "PYTHONUNBUFFERED": "1", "DAILIES_PROJECT_DIR": str(self.s.root)})
            self._proc = p
            with self._lock:
                job["pgid"] = p.pid
                self._save(job)
                if job.get("cancel_requested"):  # cancelled between the check above and the launch
                    try:
                        os.killpg(p.pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
            last_save = 0.0
            for raw in p.stdout:
                line = raw.decode(errors="replace").rstrip("\n")
                if line.startswith("DAILIES_PROGRESS"):
                    try:
                        done, total = map(int, line.split()[1:3])
                    except ValueError:
                        continue
                    el = time.monotonic() - t0
                    with self._lock:
                        job["frames_done"] = done
                        job["frames_total"] = total
                        job["eta_s"] = round(el / done * (total - done)) if done else None
                        if time.monotonic() - last_save > 5:
                            self._save(job)
                            last_save = time.monotonic()
                    continue
                if line.startswith("DAILIES_SHOT"):
                    with self._lock:
                        job["current_shot"] = line.split()[1] if len(line.split()) > 1 else None
                lf.write(raw)
                lf.flush()
            rc = p.wait()
            self._proc = None
        out = self.s.root / job["out"]
        info = ffprobe(out) if rc == 0 else None
        with self._lock:
            job["finished"] = now_iso()
            job["elapsed_s"] = round(time.monotonic() - t0)
            job["pgid"] = None
            if rc == 0 and info:
                job["status"] = "done"
                job["frames_done"] = job["frames_total"]
                job["probe"] = info
                job["eta_s"] = 0
            else:
                job["status"] = "cancelled" if job.get("cancel_requested") else "failed"
                job["error"] = job.get("error") or (f"assemble exited {rc}" if rc else "output unreadable")
            self._save(job)
        if job["status"] == "done":
            meta = {"name": job["name"], "built": job["finished"], "user": job["user"], "timeline": job["timeline"],
                    "from": job["from"], "to": job["to"], "frames": info.get("frames"), "duration": info.get("duration"),
                    "job": jid, "out": job["out"], "shots": job["selection"],
                    "film_f0": plan.film_f0, "film_f1": plan.film_f1}
            atomic_write_json(out.with_suffix(".json"), meta)
            atomic_write_json(self.s.notes_dir / "cuts" / f"{job['name']}.json", meta)
            self.cat.invalidate()
        else:
            self._retire_partial(job)
        self._cleanup_tmp(job)
        log.info("build %s %s (%s)", jid, job["status"], job["name"])

    def _loop(self) -> None:
        while not self._stop.is_set():
            with self._lock:
                while not self._queue and not self._stop.is_set():
                    self._cv.wait(timeout=1.0)
                if self._stop.is_set():
                    return
                # leaving the queue and becoming "running" are one step, so cancel() always finds the job where its
                # status says it is
                jid = self._queue.pop(0)
                self._jobs[jid].update(status="running", started=now_iso())
                self._save(self._jobs[jid])
            try:
                self._run(jid)
            except Exception as e:  # noqa: BLE001
                log.exception("build %s crashed", jid)
                with self._lock:
                    job = self._jobs[jid]
                    job.update(status="failed", finished=now_iso(), error=f"internal error: {e}")
                    self._save(job)

    def start(self) -> None:
        self.s.jobs_dir.mkdir(parents=True, exist_ok=True)
        self._thread = threading.Thread(target=self._loop, name="builds", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        with self._lock:
            self._cv.notify_all()
            running = [j for j in self._jobs.values() if j["status"] == "running" and j.get("pgid")]
        for j in running:
            try:
                os.killpg(j["pgid"], signal.SIGTERM)
            except ProcessLookupError:
                pass
