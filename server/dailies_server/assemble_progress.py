#!/usr/bin/env python3
"""Thin wrapper: run tools/assemble.py unchanged, but print machine-readable progress for Dailies.

It loads assemble.py as a module (its main() is behind `if __name__ == "__main__"`), wraps its per-frame grade() and
its shot_file() lookup to count frames and announce shots, then calls main() with the same argv. If assemble.py is
refactored and those names disappear, it still runs, just without progress lines.

  DAILIES_TOTAL <frames>
  DAILIES_SHOT <sid> <file>
  DAILIES_PROGRESS <done> <total>
"""
import importlib.util
import json
import os
import sys
from pathlib import Path

ROOT = Path(os.environ.get("DAILIES_PROJECT_DIR") or Path.cwd()).resolve()


def main() -> None:
    spec = importlib.util.spec_from_file_location("assemble", ROOT / "tools" / "assemble.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    argv = sys.argv[1:]
    def arg(name):
        return argv[argv.index(name) + 1] if name in argv and argv.index(name) + 1 < len(argv) else None

    shots = json.loads((ROOT / "build" / "edl.json").read_text())["shots"]
    ids = [s["id"] for s in shots]
    f_from, f_to = arg("--from"), arg("--to")
    if f_from:
        shots = shots[ids.index(f_from): ids.index(f_to or f_from) + 1]
    total = sum(s["frames"] for s in shots)
    print(f"DAILIES_TOTAL {total}", flush=True)

    state = {"n": 0}
    if callable(getattr(mod, "grade", None)):
        orig_grade = mod.grade

        def grade(*a, **k):
            out = orig_grade(*a, **k)
            state["n"] += 1
            if state["n"] % 10 == 0 or state["n"] == total:
                print(f"DAILIES_PROGRESS {state['n']} {total}", flush=True)
            return out
        mod.grade = grade
    if callable(getattr(mod, "shot_file", None)):
        orig_shot_file = mod.shot_file

        def shot_file(sid, tier):
            p = orig_shot_file(sid, tier)
            print(f"DAILIES_SHOT {sid} {p}", flush=True)
            return p
        mod.shot_file = shot_file

    sys.argv = [str(ROOT / "tools" / "assemble.py")] + argv
    mod.main()


if __name__ == "__main__":
    main()
