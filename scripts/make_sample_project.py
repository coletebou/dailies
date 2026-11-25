#!/usr/bin/env python3
"""Create a small synthetic Dailies project (ffmpeg test patterns, placeholder lyrics) to try the app without real media.

    python scripts/make_sample_project.py ./sample-project
    DAILIES_PROJECT_DIR=./sample-project ./run.sh

Needs ffmpeg on PATH. Everything written here is generated; nothing is downloaded.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

FPS = 30
SHOTS = [("s01", 60), ("s02", 45), ("s03", 75), ("s04", 60)]    # id, frames
PATTERNS = ["testsrc2", "smptebars", "rgbtestsrc", "testsrc"]
LINES = [("Intro", "sample line one", 0.2, 1.8), ("Verse", "sample line two", 2.2, 3.4),
         ("Verse", "sample line three", 3.6, 5.6), ("Outro", "sample line four", 6.0, 8.0)]


def ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True)


def clip(out: Path, pattern: str, frames: int, size: str, tint: str | None = None) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    vf = f"{tint}," if tint else ""
    ffmpeg("-f", "lavfi", "-i", f"{pattern}=size={size}:rate={FPS}", "-frames:v", str(frames),
           "-vf", f"{vf}format=yuv420p", "-c:v", "libx264", "-preset", "veryfast", "-f", "mp4", str(out))


def main() -> None:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else "sample-project").resolve()
    size = "640x360"
    f0, shots = 0, []
    for sid, n in SHOTS:
        shots.append({"id": sid, "f0": f0, "f1": f0 + n, "frames": n})
        f0 += n
    total = f0
    (root / "build").mkdir(parents=True, exist_ok=True)
    (root / "build/edl.json").write_text(json.dumps({"fps": FPS, "lead_frames": 0, "total_frames": total, "shots": shots}, indent=1))

    (root / "music").mkdir(parents=True, exist_ok=True)
    (root / "music/lyrics_timed.json").write_text(json.dumps({"lines": [
        {"section": sec, "text": text, "t0": t0, "t1": t1,
         "words": [{"w": w, "t0": round(t0 + i * (t1 - t0) / len(text.split()), 3),
                    "t1": round(t0 + (i + 1) * (t1 - t0) / len(text.split()), 3)} for i, w in enumerate(text.split())]}
        for sec, text, t0, t1 in LINES]}, indent=1))
    ffmpeg("-f", "lavfi", "-i", f"sine=frequency=220:duration={total / FPS}", str(root / "music/mix_master.wav"))

    # working renders ("live"), one per shot, plus one finished snapshot per version with its full cut
    for (sid, n), pat in zip(SHOTS, PATTERNS):
        clip(root / f"renders/shots/{sid}_cycle.mov", pat, n, size)
        clip(root / f"renders/snapshots/v01_1080/shots/{sid}_final.mov", pat, n, size, "hue=h=60")
        clip(root / f"renders/snapshots/v02_1080/shots/{sid}_final.mov", pat, n, size, "hue=h=200")
    for v, hue in (("v01_1080", "hue=h=60"), ("v02_1080", "hue=h=200")):
        parts = root / "renders/snapshots/_concat.txt"
        parts.write_text("".join(f"file '{root}/renders/snapshots/{v}/shots/{sid}_final.mov'\n" for sid, _ in SHOTS))
        ffmpeg("-f", "concat", "-safe", "0", "-i", str(parts), "-i", str(root / "music/mix_master.wav"),
               "-c:v", "copy", "-c:a", "aac", "-shortest", "-movflags", "+faststart",
               str(root / f"renders/snapshots/{v}.mp4"))
        parts.unlink()
    print(f"sample project written to {root}\nrun: DAILIES_PROJECT_DIR={root} ./run.sh")


if __name__ == "__main__":
    main()
