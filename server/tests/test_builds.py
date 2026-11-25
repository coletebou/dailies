import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from dailies_server import jobs
from dailies_server.catalog import Catalog
from dailies_server.jobs import BuildError, BuildManager, materialize, plan_build
from dailies_server.util import atomic_write_json

SEL = {"s01": "v01_1080", "s02": "v02_1080", "s03": "live", "s04": "v02_1080"}


def test_sources_discovery(settings):
    cat = Catalog(settings)
    ids = [s.id for s in cat.sources()]
    assert ids == ["v02_1080", "v01_1080", "live"]           # newest first, live last
    assert cat.shot_file("v02_1080", "s04").tier == "final"   # final beats cycle in snapshots
    assert cat.shot_file("live", "s01").tier == "cycle"       # live is cycle-first (what the Mac cycle cuts use)
    assert cat.shot_file("live", "s02").path.name == "s02_cycle.mov"   # chunk temps (.partNNNNN) ignored
    assert cat.shot_file("live", "s04") is None


def test_plan_maps_timeline_to_assemble(settings):
    cat = Catalog(settings)
    root = settings.root
    plan = plan_build(settings, cat, "swap-test", SEL, shots_dir=Path("/tmp/x/shots"))
    assert plan.shots == ["s01", "s02", "s03", "s04"]
    assert plan.f_from is None and plan.f_to is None
    assert plan.frames == 120
    assert [(sid, p.relative_to(root).as_posix(), link) for sid, p, link in plan.links] == [
        ("s01", "renders/snapshots/v01_1080/shots/s01_final.mov", "s01_final.mov"),
        ("s02", "renders/snapshots/v02_1080/shots/s02_final.mov", "s02_final.mov"),
        ("s03", "renders/shots/s03_cycle.mov", "s03_cycle.mov"),
        ("s04", "renders/snapshots/v02_1080/shots/s04_final.mov", "s04_final.mov"),
    ]
    cmd = plan.cmd
    assert cmd[:3] == ["nice", "-n", "10"]
    assert cmd[4].endswith("dailies_server/assemble_progress.py")
    assert cmd[5:] == ["--tier", "final", "--res", "1920x1080", "--shots-dir", "/tmp/x/shots",
                       "--audio", "music/mix_master.wav", "--out", "renders/studio/cuts/swap-test.mp4", "--no-latest"]
    assert plan.selection["s03"] == {"source": "live", "tier": "cycle", "path": "renders/shots/s03_cycle.mov"}


def test_plan_partial_range(settings):
    cat = Catalog(settings)
    plan = plan_build(settings, cat, "short", {"s02": "v01_1080", "s03": "v02_1080"}, "s02", "s03")
    assert plan.shots == ["s02", "s03"] and plan.frames == 75
    assert plan.cmd[-4:] == ["--from", "s02", "--to", "s03"]
    # from alone runs to the end, to alone starts at the beginning
    assert plan_build(settings, cat, "tail", {"s03": "live", "s04": "v01_1080"}, "s03").shots == ["s03", "s04"]
    assert plan_build(settings, cat, "head", {"s01": "live"}, None, "s01").cmd[-4:] == ["--from", "s01", "--to", "s01"]


@pytest.mark.parametrize("name,sel,f_from,f_to,msg", [
    ("bad/name", SEL, None, None, "cut name"),
    ("../x", SEL, None, None, "cut name"),
    ("ok", {**SEL, "s04": "live"}, None, None, "live has no s04"),
    ("ok", {k: v for k, v in SEL.items() if k != "s02"}, None, None, "no source for: s02"),
    ("ok", {**SEL, "s99": "live"}, None, None, "unknown shots"),
    ("ok", SEL, "s03", "s01", "range end"),
    ("ok", SEL, "s09", None, "unknown shot"),
    ("ok", {**SEL, "s01": "../../tools"}, None, None, "no source for"),
])
def test_plan_rejects(settings, name, sel, f_from, f_to, msg):
    with pytest.raises(BuildError, match=msg):
        plan_build(settings, Catalog(settings), name, sel, f_from, f_to)


def test_materialize_links_resolve_through_assemble_fallback(settings, tmp_path):
    cat = Catalog(settings)
    plan = plan_build(settings, cat, "m", SEL)
    d = tmp_path / "shots"
    materialize(plan, d)
    for _sid, src, link in plan.links:
        p = d / link
        assert p.is_symlink() and p.resolve() == src.resolve()
    assert sorted(x.name for x in d.iterdir()) == ["s01_final.mov", "s02_final.mov", "s03_cycle.mov", "s04_final.mov"]


def _wait(bm, jid, timeout=30):
    t0 = time.time()
    while time.time() - t0 < timeout:
        j = bm.get(jid)
        if j["status"] not in ("queued", "running"):
            return j
        time.sleep(0.1)
    raise AssertionError("build did not finish")


def test_build_job_end_to_end_with_stub_assembler(settings, monkeypatch):
    # run the wrapper with the test interpreter (no nice needed for the check, but keep it in the command)
    monkeypatch.setattr(type(settings), "python", property(lambda s: Path(__import__("sys").executable)))
    cat = Catalog(settings)
    bm = BuildManager(settings, cat)
    bm.start()
    try:
        job = bm.submit("e2e", SEL, "s02", "s03", timeline="tl", user="tester")
        assert job["status"] == "queued" and job["frames_total"] == 75
        with pytest.raises(BuildError, match="already queued"):
            bm.submit("e2e", SEL, None, None)
        done = _wait(bm, job["id"])
        assert done["status"] == "done", (done, bm.log_tail(job["id"]))
        assert done["frames_done"] == 75
        out = json.loads((settings.root / "renders/studio/cuts/e2e.stub.json").read_text())
        # the stub assembler saw exactly the chosen masters, through the temp shots dir, for the chosen range
        assert [u[0] for u in out["used"]] == ["s02", "s03"]
        assert [u[2] for u in out["used"]] == ["v02_1080:s02", "live:s03"]
        assert out["argv"][out["argv"].index("--from") + 1] == "s02"
        assert not (settings.tmp_dir / job["id"]).exists()                 # temp links cleaned up
        meta = json.loads((settings.root / "notes/studio/cuts/e2e.json").read_text())
        assert meta["shots"]["s03"]["source"] == "live" and meta["timeline"] == "tl"
        # where the cut sits in the film: its time 0 is s02's first frame, and it ends where s03 does
        assert (meta["film_f0"], meta["film_f1"]) == (30, 105)
        assert json.loads((settings.cuts_dir / "e2e.json").read_text())["film_f0"] == 30
        v = next(x for x in Catalog(settings).versions() if x["id"] == "studio-e2e")
        assert (v["cut"]["film_f0"], v["cut"]["film_f1"]) == (30, 105)
        with pytest.raises(BuildError, match="already exists"):           # never overwrite a cut
            bm.submit("e2e", SEL, None, None)
        # the masters themselves are untouched
        assert (settings.root / "renders/shots/s03_cycle.mov").read_text() == "live:s03"
    finally:
        bm.stop()


def test_build_failure_keeps_nothing_half_written(settings, monkeypatch):
    monkeypatch.setattr(type(settings), "python", property(lambda s: Path(__import__("sys").executable)))
    (settings.root / "tools/assemble.py").write_text("raise SystemExit('boom')\n")
    bm = BuildManager(settings, Catalog(settings))
    bm.start()
    try:
        job = bm.submit("bad", SEL, None, None)
        done = _wait(bm, job["id"])
        assert done["status"] == "failed"
        assert not (settings.root / "renders/studio/cuts/bad.mp4").exists()
        assert any("boom" in ln for ln in bm.log_tail(job["id"]))
    finally:
        bm.stop()


def _use_test_python(settings, monkeypatch):
    monkeypatch.setattr(type(settings), "python", property(lambda s: Path(sys.executable)))


def _until(pred, timeout=15):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if pred():
            return
        time.sleep(0.05)
    raise AssertionError("timed out")


def test_cancel_between_dequeue_and_launch(settings, monkeypatch):
    """The worker has taken the job off the queue but not started assemble.py yet: cancel must work (it used to raise
    ValueError from _queue.remove, a 500, while the build went on)."""
    _use_test_python(settings, monkeypatch)
    entered, release = threading.Event(), threading.Event()
    real = jobs.materialize

    def slow_materialize(plan, d):
        entered.set()
        release.wait(10)
        real(plan, d)
    monkeypatch.setattr(jobs, "materialize", slow_materialize)
    bm = BuildManager(settings, Catalog(settings))
    bm.start()
    try:
        job = bm.submit("race", SEL, None, None)
        assert entered.wait(10)
        j = bm.cancel(job["id"])
        assert j["status"] == "running" and j["cancel_requested"]
        release.set()
        done = _wait(bm, job["id"])
        assert done["status"] == "cancelled" and done["pgid"] is None and not done.get("cmd")
        assert not (settings.cuts_dir / "race.mp4").exists()                # assemble.py never ran
        assert not (settings.cuts_dir / "race.stub.json").exists()
        assert not (settings.tmp_dir / job["id"]).exists()
    finally:
        release.set()
        bm.stop()


def test_cancel_handles_every_state(settings, monkeypatch):
    _use_test_python(settings, monkeypatch)
    (settings.root / "tools/assemble.py").write_text("import time\nprint('working', flush=True)\ntime.sleep(60)\n")
    bm = BuildManager(settings, Catalog(settings))
    bm.start()
    try:
        a = bm.submit("slow", SEL, None, None)
        b = bm.submit("next", SEL, None, None)
        _until(lambda: bm.get(a["id"])["pgid"])
        assert bm.get(b["id"])["status"] == "queued"
        assert bm.cancel(b["id"])["status"] == "cancelled"                    # queued: just dropped
        pg = bm.get(a["id"])["pgid"]
        assert bm.cancel(a["id"])["cancel_requested"]                         # running: SIGTERM to its group
        assert _wait(bm, a["id"])["status"] == "cancelled"
        with pytest.raises(ProcessLookupError):
            os.killpg(pg, 0)
        assert bm.cancel(a["id"])["status"] == "cancelled"                    # finished: nothing to do
        assert bm.cancel("b-nope") is None
        time.sleep(0.5)
        assert bm.get(b["id"])["status"] == "cancelled" and bm.get(b["id"])["started"] is None
    finally:
        bm.stop()


def test_startup_stops_only_its_own_orphaned_assembler(settings):
    """After a reboot the pgid in a stale job file can belong to anything: only a process group that is still that
    job's assemble_progress.py is signalled."""
    ours_id, other_id = "b20990101-000000-beef", "b20990101-000000-dead"
    other = subprocess.Popen(["sleep", "30"], start_new_session=True)
    ours = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)", "assemble_progress.py",
                             "--shots-dir", str(settings.tmp_dir / ours_id / "shots")], start_new_session=True)
    try:
        for jid, p in ((ours_id, ours), (other_id, other)):
            atomic_write_json(settings.jobs_dir / f"{jid}.json", {
                "id": jid, "name": jid, "status": "running", "created": "2099-01-01T00:00:00+0000", "pgid": p.pid,
                "out": f"renders/studio/cuts/{jid}.mp4"})
        bm = BuildManager(settings, Catalog(settings))
        assert ours.wait(10) == -signal.SIGTERM                                 # the orphaned assembler is stopped
        time.sleep(0.3)
        assert other.poll() is None                                            # an unrelated process is left alone
        for jid in (ours_id, other_id):
            j = bm.get(jid)
            assert j["status"] == "interrupted" and "restarted" in j["error"]
        # a job file whose process is long gone is just marked, nothing is signalled
        assert not BuildManager._owns_group({"id": other_id, "pgid": 2 ** 22 + 7})
    finally:
        for p in (ours, other):
            p.kill()
            p.wait()


def test_build_name_must_match_whole_string(settings):
    with pytest.raises(BuildError, match="cut name"):
        plan_build(settings, Catalog(settings), "ok\n", SEL)


def test_build_api(client, settings):
    r = client.post("/api/builds", json={"name": "x y", "shots": SEL})
    assert r.status_code == 400
    r = client.post("/api/builds", json={"name": "ok", "shots": "nope"})
    assert r.status_code == 400
    r = client.post("/api/builds", json={"name": "ok", "shots": SEL}, headers={"X-Dailies": ""})
    assert r.status_code == 403
