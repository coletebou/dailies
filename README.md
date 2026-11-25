# Dailies

A self-hosted review studio for video dailies. Point it at a project directory of renders and it gives you a browser
app to compare versions side by side in lockstep, scrub shot by shot on the edit decision list, draw on exact frames,
keep review notes, mix and match shots from different versions into a new edit, and rebuild that edit into a cut.

It is a small FastAPI server plus a React app. No database, no accounts of its own: state is plain JSON and PNG files
inside your project directory, so it diffs, backs up and version-controls like the rest of the project.

## Features

- **Compare.** Two to four versions side by side, or stacked in Flip mode (keys 1-4 swap the visible version on the
  same frame). One shared clock keeps every pane in sync; frame step (`,` `.`), shot jump (`[` `]`), per-shot loop
  (`L`), 0.25x to 2x speed. Partial cuts line up at their place on the full timeline.
- **Draw on a frame.** Pause and press `D`: pen, arrow, rectangle, ellipse, text. Drawings are stored as vector JSON
  (normalised to the picture) plus a flattened PNG of that exact frame, and reappear as an overlay when you scrub
  back to the frame.
- **Notes.** Timestamped, frame-exact annotations with tags (problem, like, idea), open/done status, filters and
  jump-to-frame. Optional automated critique reports show up beside them as "critic" markers and score tables.
- **Timeline.** The EDL as a strip of shots. For each shot pick which version's master fills it, preview the edit in
  the browser before building anything, save named timelines, then **Build cut** to render it with your own assembler
  (progress, ETA, log, cancel).
- **Live.** A feed of what your render agents or farm are writing right now: previews, contact sheets, QA stills,
  snapshot parts. **Freeze** clones the current working shots so a cycle stays pickable after the next one overwrites it.
- **Proxies.** Browser-friendly 720p H.264 proxies and thumbnails are made lazily and warmed in the background at
  low priority, so remote reviewers do not stream your masters.
- **Safe to expose.** Loopback-only bind, host allowlist, strict path allowlist for media, and built-in verification
  of Cloudflare Access JWTs (see [Security model](#security-model)).

## Quick start

Requirements: Python 3.11+ (tested on 3.15), Node 22+ (tested on 26; `npm test` uses Node's built-in TypeScript support), `ffmpeg` and `ffprobe` on `PATH`.

```sh
git clone https://github.com/coletebou/dailies.git && cd dailies

python3 -m venv .venv
.venv/bin/pip install -r server/requirements.txt

(cd web && npm install && npm run build)       # writes web/dist, which the server serves

# try it on a synthetic project (ffmpeg test patterns, placeholder lyrics)
.venv/bin/python scripts/make_sample_project.py ./sample-project
DAILIES_PROJECT_DIR=./sample-project ./run.sh   # http://127.0.0.1:8765
```

For your own material, point `DAILIES_PROJECT_DIR` at your project directory:

```sh
DAILIES_PROJECT_DIR=/path/to/your/project ./run.sh
```

`run.sh` uses `./.venv` when present, otherwise `python3`, and starts `python -m dailies_server` from `server/`.
For development, `cd web && npm run dev` serves the UI on :5178 and proxies the API to the running server.

## Project directory layout

Dailies reads and writes a fixed layout under the project directory (the convention it was built around). Only
`build/edl.json` is strictly required; everything else lights up features as it appears.

```
<project>/
  build/
    edl.json                    REQUIRED  the edit: fps, total_frames, lead_frames, shots[{id, f0, f1, frames}]
    logs/dailies.log            written    server log (rotated at 10 MB x 3)
  music/
    lyrics_timed.json           optional   {"lines": [{section, text, t0, t1, words: [{w, t0, t1}]}]}  (labels shots, word-by-word line)
    mix_master.wav              optional   audio laid under built cuts
  notes/
    SHOTLIST.md                 optional   markdown table, rows "| 01 | ... | lyric | picture |" (read server-side only)
    critiques/v*.json           optional   automated critique reports (see below); served read-only
    studio/                     written    annotations.json, timelines/, cuts/ (recipes), frames/ (annotated PNGs)
  renders/
    snapshots/vNN_1080.mp4      full cuts, one per version (vNN_1080_fixed.mp4 wins; X_fixed hides X and X_crit)
    snapshots/vNN_1080/shots/   per-shot masters of that version:  <shot>_<tier>.mov|mp4|mkv  (tier order final, preview, cycle, draft)
    previews/vNN.mp4            lower-priority full cuts
    shots/                      "live" working renders, <shot>_<tier>.*  (tier order cycle first); *_preview.mp4, *_sheet.jpg
    tests/qa/*.jpg              QA stills shown in Live
    studio/                     written    proxies/, cuts/ (built videos), frozen/, jobs/, tmp/   (generated media; gitignore it)
  tools/
    assemble.py                 optional   your assembler, used by Build cut (see below)
```

Shot ids are `s` plus digits (`s01`, `s24`). Versions are keyed `vNN`. The `studio` directories under `notes/` and
`renders/` are Dailies' own state; the name is kept so existing data keeps working.

Run `python scripts/make_sample_project.py` to see a minimal, valid project.

### Critique reports (optional)

`notes/critiques/v<NN>*.json`: `{"label": "v03", "model": "...-flash" | "...-pro", "scores": {"1": 7, ... "7": 6},
"avg": 6.5, "min": 5, "overall": "...", "weakest_moments": [{"timestamp": "01:29", "problem": "...", "fix": "..."}]}`.
A model name containing `pro` or `flash` fills that reviewer slot in the score displays; the seven criteria are
labelled in `web/src/util.ts`.

### Build cut and your assembler (optional)

Build cut symlinks the chosen masters into `renders/studio/tmp/<job>/shots/` and runs, under `nice`:

```
<python> server/dailies_server/assemble_progress.py --tier final --res 1920x1080 --shots-dir <tmp> \
    --audio music/mix_master.wav --out renders/studio/cuts/<name>.mp4 --no-latest [--from sA --to sB]
```

`assemble_progress.py` loads `<project>/tools/assemble.py` as a module, calls its `main()` with those arguments, and
(when present) wraps its `grade()` and `shot_file(sid, tier)` functions to print `DAILIES_TOTAL`, `DAILIES_SHOT` and
`DAILIES_PROGRESS` lines. Provide an `assemble.py` that accepts those flags and writes `--out`. `<python>` is
`DAILIES_PYTHON`, else `<project>/.venv/bin/python`, else the server's interpreter. Without an assembler, every
other feature still works; only Build cut fails. One build runs at a time, cuts are never overwritten, and a failed
or cancelled output is renamed `<name>.failed-<job>.mp4`.

## Configuration

All settings are environment variables, read at start-up.

| Variable | Default | Meaning |
|---|---|---|
| `DAILIES_PROJECT_DIR` | current directory | Project directory (data root) described above. |
| `DAILIES_HOST` | `127.0.0.1` | Bind address. Non-loopback addresses are refused. |
| `DAILIES_PORT` | `8765` | Bind port. |
| `DAILIES_PUBLIC_HOSTS` | empty | Comma-separated public hostnames served through your tunnel. Requests for these must carry a valid Cloudflare Access JWT. Empty: loopback only. |
| `DAILIES_ACCESS_TEAM_DOMAIN` | empty | Your Access team domain, e.g. `yourteam.cloudflareaccess.com`. Required for public hosts. |
| `DAILIES_ACCESS_AUD` | empty | The Access application's AUD tag. Required for public hosts; without it public requests get 403. |
| `DAILIES_ACCESS_EMAILS` | empty | Optional comma-separated allowlist of emails, checked on top of the Access policy. Empty: any identity your Access policy admits. |
| `DAILIES_PYTHON` | `<project>/.venv/bin/python`, else the server's | Interpreter used for Build cut. |
| `DAILIES_WARM` | `1` | `0` disables background proxy warm-up (proxies are still made on demand). |
| `DAILIES_WARM_MAX_LOAD` | `0.9` | Warm-up pauses while the 1-minute load average exceeds this times the core count. |

## Deploying behind Cloudflare Access

Recommended way to put Dailies on the internet. The server never listens on a public address; a Cloudflare Tunnel
reaches it on loopback, and Access authenticates the person before a request gets that far.

1. Run Dailies on loopback: `DAILIES_PROJECT_DIR=... ./run.sh` (keep it running with launchd, systemd or similar).
2. Create a tunnel whose ingress points `https://dailies.example.com` at `http://127.0.0.1:8765`, and a DNS record
   for it.
3. In Cloudflare Zero Trust create a **Self-hosted Access application** for `dailies.example.com` with an Allow policy
   for the people who should review (emails, a domain, or an IdP group).
4. Copy the application's **AUD tag** (Access > Applications > your app) and your **team domain**
   (`<team>.cloudflareaccess.com`), and start the server with them:

   ```sh
   export DAILIES_PROJECT_DIR=/path/to/project
   export DAILIES_PUBLIC_HOSTS=dailies.example.com
   export DAILIES_ACCESS_TEAM_DOMAIN=yourteam.cloudflareaccess.com
   export DAILIES_ACCESS_AUD=<aud tag>
   export DAILIES_ACCESS_EMAILS=you@example.com,editor@example.com   # recommended
   ./run.sh
   ```

Dailies verifies the `Cf-Access-Jwt-Assertion` header (or `CF_Authorization` cookie) itself, so it is protected even
if the tunnel or edge policy is misconfigured. Keep secrets in the process environment or a secrets manager, not in
the repository.

For an always-on Linux server, [`deploy/`](deploy/README.md) has hardened systemd units for the server and the
tunnel, an example env file and tunnel config, and the directory layout they expect.

## Security model

- **Bind.** The server binds to loopback only and refuses anything else.
- **Host allowlist.** Requests are answered only for `127.0.0.1`, `localhost` and `DAILIES_PUBLIC_HOSTS`; any other
  `Host` gets 421 (DNS-rebinding guard).
- **Access JWT on public hosts.** For a public host the token must be RS256, signed by a current key from the team's
  `/cdn-cgi/access/certs`, with `aud` equal to your AUD tag, `iss` equal to your team domain, unexpired, and (if set)
  `email` on the allowlist. A missing token, any verification failure, or a missing AUD or team domain is a 403
  (fail closed). Local requests on loopback do not carry an Access token, so loopback is the trust boundary:
  do not publish the port by other means.
- **Writes.** Every POST, PUT, PATCH and DELETE needs an `X-Dailies: 1` header. A cross-site page cannot send it
  without a CORS preflight, and the server answers no preflights, so other sites cannot drive the API with a
  viewer's cookies.
- **Media allowlist.** `/media/<path>` serves only files whose resolved real path is under `renders/`,
  `notes/critiques/` or `notes/studio/`, or is `build/edl.json` or `music/lyrics*.json`. Traversal, absolute paths,
  backslashes, NUL bytes and symlink escapes are refused. HTTP Range is supported. Responses are `Cache-Control:
  private`, with `nosniff`, `same-origin` referrer and frame-deny headers.
- **Build input.** The browser sends source ids only; file paths are always resolved server-side from the catalog.

Report vulnerabilities privately via GitHub security advisories on this repository.

## Development and tests

```sh
# server (needs ffmpeg for the media fixtures)
python3 -m venv .venv && .venv/bin/pip install -r server/requirements.txt
cd server && ../.venv/bin/python -m pytest -q

# web
cd web && npm install
npm test            # unit tests (Node's built-in runner)
npm run typecheck
npm run build
```

Test fixtures are synthetic: a miniature project tree built in a temp directory with generated H.264 test patterns.
CI-friendly, no network, no real media.

Stack: FastAPI and uvicorn, PyJWT, Pillow; React 19, Vite and TypeScript; perfect-freehand (MIT) for pen strokes on
a plain canvas.

## Licence

MIT, see [LICENSE](LICENSE). Bundled fonts (Archivo, Instrument Serif, JetBrains Mono) are under the SIL Open Font
License 1.1, see [web/public/fonts/LICENSES.md](web/public/fonts/LICENSES.md).
