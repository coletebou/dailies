"""A miniature project tree: 4-shot EDL, lyrics, two snapshot versions, live renders, critiques, a stub assembler."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dailies_server.app import create_app  # noqa: E402
from dailies_server.config import Settings  # noqa: E402

SHOTS = [("s01", 0, 30), ("s02", 30, 45), ("s03", 75, 30), ("s04", 105, 15)]

STUB_ASSEMBLE = '''
import argparse, json, sys, time
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
SHOTS_DIR = ROOT / "renders/shots"
TIERS = ["final", "preview", "cycle", "draft"]
def shot_file(sid, tier):
    for t in TIERS:
        for ext in (".mov", ".mp4", ".mkv"):
            p = SHOTS_DIR / f"{sid}_{t}{ext}"
            if p.exists():
                return p
    return None
def grade(img, fidx, **k):
    return img
def main():
    ap = argparse.ArgumentParser()
    for a in ("--tier", "--out", "--audio", "--res", "--from", "--to", "--shots-dir"):
        ap.add_argument(a, dest=a[2:].replace("-", "_"))
    ap.add_argument("--no-latest", action="store_true")
    a = ap.parse_args()
    global SHOTS_DIR
    SHOTS_DIR = Path(a.shots_dir)
    shots = json.loads((ROOT / "build/edl.json").read_text())["shots"]
    if a.__dict__.get("from"):
        ids = [s["id"] for s in shots]
        shots = shots[ids.index(a.__dict__["from"]): ids.index(a.to) + 1]
    used = []
    for s in shots:
        p = shot_file(s["id"], a.tier)
        if p is None:
            raise SystemExit("missing shot " + s["id"])
        used.append([s["id"], str(p), Path(p).read_text()])
        for k in range(s["frames"]):
            grade(None, s["f0"] + k)
    out = ROOT / a.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes((ROOT / "renders/snapshots/v01_1080.mp4").read_bytes())   # a real, probe-able mp4
    out.with_suffix(".stub.json").write_text(json.dumps({"argv": sys.argv[1:], "used": used}))
    print("wrote", out)
if __name__ == "__main__":
    main()
'''


def write(p: Path, data: str | bytes) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, bytes):
        p.write_bytes(data)
    else:
        p.write_text(data)
    return p


@pytest.fixture(scope="session")
def tiny_mp4(tmp_path_factory) -> bytes:
    """A real 4 s, 30 fps H.264 + AAC file so ffprobe-based discovery behaves as it does on real cuts."""
    out = tmp_path_factory.mktemp("media") / "tiny.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=64x36:rate=30:duration=4",
                    "-f", "lavfi", "-i", "sine=duration=4", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                    "-shortest", str(out)], check=True)
    return out.read_bytes()


@pytest.fixture()
def project(tmp_path: Path, tiny_mp4: bytes) -> Path:
    root = tmp_path / "proj"
    edl = {"fps": 30, "lead_frames": 3, "total_frames": 120,
           "shots": [{"id": i, "f0": f0, "f1": f0 + n, "frames": n} for i, f0, n in SHOTS]}
    write(root / "build/edl.json", json.dumps(edl))
    write(root / "music/lyrics_timed.json", json.dumps({"lines": [
        {"section": "Intro", "text": "first line", "t0": 0.2, "t1": 0.9, "words": [{"w": "first", "t0": 0.2, "t1": 0.5}]},
        {"section": "Verse", "text": "second line", "t0": 1.1, "t1": 2.4, "words": []}]}))
    write(root / "music/mix_master.wav", b"RIFF")
    write(root / "notes/critiques/v01_critic-flash.json", json.dumps({
        "label": "v01", "model": "critic-flash", "scores": {"1": 6, "2": 7}, "avg": 6.5, "min": 6,
        "weakest_moments": [{"timestamp": "00:02", "problem": "p", "fix": "f"}], "overall": "ok"}))
    for v in ("v01_1080", "v02_1080"):
        for sid, *_ in SHOTS:
            write(root / f"renders/snapshots/{v}/shots/{sid}_final.mov", f"{v}:{sid}")
    write(root / "renders/snapshots/v02_1080/shots/s04_cycle.mov", "v02_1080:s04:cycle")   # final wins over cycle
    for sid, *_ in SHOTS[:3]:
        write(root / f"renders/shots/{sid}_cycle.mov", f"live:{sid}")
    write(root / "renders/shots/s01_final.mov", "live:s01:stale-final")   # live is cycle-first
    write(root / "renders/shots/s02_cycle.part00001.mov", "chunk temp")
    write(root / "renders/snapshots/v01_1080.mp4", tiny_mp4)
    write(root / "renders/snapshots/v01_1080_crit.mp4", tiny_mp4)     # hidden: superseded by _fixed
    write(root / "renders/snapshots/v01_1080_fixed.mp4", tiny_mp4)
    write(root / "renders/previews/v02.mp4", tiny_mp4)
    write(root / "renders/previews/v03.mp4", b"still being written")   # no moov yet -> pending
    write(root / "tools/assemble.py", STUB_ASSEMBLE)
    write(root / "tools/secret.py", "SECRET = 1")
    write(root / "notes/private.md", "not served")
    write(root / "renders/ok.txt", "fine")
    outside = write(tmp_path / "outside.txt", "outside")
    (root / "renders/escape.txt").symlink_to(outside)
    (root / "renders/escape_dir").symlink_to(tmp_path)
    return root


@pytest.fixture()
def settings(project: Path) -> Settings:
    # "testserver" is the Host header of Starlette's TestClient; production answers only 127.0.0.1 / localhost
    return Settings(root=project, warm=False, access_aud="test-aud", access_team_domain="team.example.com",
                    public_hosts=("dailies.example.com",), access_emails=("reviewer@example.com",),
                    local_hosts=("127.0.0.1", "localhost", "testserver"), web_dist=project / "web-dist")


@pytest.fixture()
def client(settings: Settings):
    from fastapi.testclient import TestClient
    app = create_app(settings, start_workers=False)
    with TestClient(app) as c:
        c.headers.update({"X-Dailies": "1"})
        yield c


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for k in list(os.environ):
        if k.startswith("DAILIES_"):
            monkeypatch.delenv(k, raising=False)
