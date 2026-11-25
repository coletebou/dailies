import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api";
import { TimelinePlayer, type PlayerHandle } from "../components/TimelinePlayer";
import { navigate } from "../router";
import { useData } from "../store";
import type { Build, ShotOption, TimelineSummary, Version } from "../types";
import { ago, avgOf, FPS, mediaUrl, mmss, tc, versionTone } from "../util";

const DRAFT_KEY = "dailies.timeline.draft";

function loadDraft(): { name: string; base: string; shots: Record<string, string> } | null {
  try {
    const raw = localStorage.getItem(DRAFT_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}
function saveDraft(d: { name: string; base: string; shots: Record<string, string> }) {
  try {
    localStorage.setItem(DRAFT_KEY, JSON.stringify(d));
  } catch {
    /* private mode: the draft just isn't remembered */
  }
}

function SourceCard({ sid, o, chosen, onChoose, label, tone, notes, version }: {
  sid: string; o: ShotOption; chosen: boolean; onChoose: () => void; label: string; tone: string; notes: number; version?: Version;
}) {
  const [hover, setHover] = useState(false);
  useEffect(() => {
    if (hover && !o.proxy_ready) api.ensureProxy(o.source, sid).catch(() => undefined);
  }, [hover, o, sid]);
  const f = version ? avgOf(version.critiques, "flash") : null;
  const p = version ? avgOf(version.critiques, "pro") : null;
  return (
    <button className={`src-card ${chosen ? "on" : ""}`} onClick={onChoose} onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)} onFocus={() => setHover(true)} onBlur={() => setHover(false)}
      aria-pressed={chosen}>
      <span className="src-media">
        <img src={o.thumb} alt="" loading="lazy" />
        {hover && o.proxy_ready && <video src={o.proxy} autoPlay muted loop playsInline />}
        {hover && !o.proxy_ready && <span className="chip src-wait">making preview…</span>}
      </span>
      <span className="src-label">
        <span className="tone" style={{ background: tone }} />
        <b>{label}</b> <span className="muted">{o.tier}</span>
        {chosen && <span className="chosen">in the edit</span>}
      </span>
      <span className="src-meta">
        <span>{ago(o.mtime)}</span>
        {(f !== null || p !== null) && <span title="critic flash / pro">{f?.toFixed(2) ?? "–"} / {p?.toFixed(2) ?? "–"}</span>}
        {notes > 0 && <span className="n">{notes} note{notes > 1 ? "s" : ""}</span>}
      </span>
    </button>
  );
}

function BuildRow({ b, start, onCancel }: { b: Build; start: number; onCancel: () => void }) {
  const pct = b.frames_total ? Math.round((100 * b.frames_done) / b.frames_total) : 0;
  const [open, setOpen] = useState(false);
  const [log, setLog] = useState<string[]>([]);
  useEffect(() => {
    if (!open) return;
    let alive = true;
    const pull = () => api.build(b.id).then((x) => alive && setLog(x.log ?? [])).catch(() => undefined);
    pull();
    const id = window.setInterval(pull, 3000);
    return () => { alive = false; window.clearInterval(id); };
  }, [open, b.id]);
  return (
    <li className={`build ${b.status}`}>
      <div className="build-head">
        <b>{b.name}</b>
        <span className={`status ${b.status}`}>{b.status}</span>
        {b.from && <span className="muted">{b.from}–{b.to}</span>}
        <span className="muted">{b.created.slice(5, 16).replace("T", " ")}</span>
        <span className="pane-spacer" />
        {(b.status === "running" || b.status === "queued") && <button className="small" onClick={onCancel}>Cancel</button>}
        {b.status === "done" && (
          <>
            <button className="small primary" onClick={() => navigate("compare", { v: `studio-${b.name}`, t: start, j: Date.now() })}>Open in Compare</button>
            <a className="small btn" href={mediaUrl(b.out)} download>Download</a>
          </>
        )}
        <button className="small" onClick={() => setOpen(!open)} aria-expanded={open}>Log</button>
      </div>
      {(b.status === "running" || b.status === "queued" || b.status === "done") && (
        <div className="progress" role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}>
          <span style={{ width: `${pct}%` }} />
          <em>{b.status === "running" ? `${pct}% · frame ${b.frames_done}/${b.frames_total}${b.current_shot ? " · " + b.current_shot : ""}${b.eta_s ? ` · about ${Math.ceil(b.eta_s / 60)} min left` : ""}`
            : b.status === "done" ? `${b.frames_total} frames in ${Math.round((b.elapsed_s ?? 0) / 60)} min → ${b.out}` : "waiting for the build before it"}</em>
        </div>
      )}
      {b.error && <p className="error-text">{b.error}</p>}
      {open && <pre className="log">{log.join("\n") || "No output yet."}</pre>}
    </li>
  );
}

export default function Timeline() {
  const { meta, versions, sources, annotations, builds, refreshBuilds, refreshSources, toast } = useData();
  const [shots, setShots] = useState<Record<string, string>>({});
  const [base, setBase] = useState("");
  const [name, setName] = useState("");
  const [selected, setSelected] = useState<string>("");
  const [saved, setSaved] = useState<TimelineSummary[]>([]);
  const [time, setTime] = useState(0);
  const [zoom, setZoom] = useState(1);
  const [loopSel, setLoopSel] = useState(false);
  const [dialog, setDialog] = useState(false);
  const player = useRef<PlayerHandle>(null);
  const dlg = useRef<HTMLDialogElement>(null);

  const snapSources = useMemo(() => (sources?.sources ?? []).filter((s) => s.count > 0), [sources]);

  // initial selection: the local draft, else every shot from the newest version that has per-shot masters
  useEffect(() => {
    if (!sources || !meta || Object.keys(shots).length) return;
    const d = loadDraft();
    if (d && Object.keys(d.shots).length) {
      setShots(d.shots);
      setBase(d.base);
      setName(d.name);
    } else {
      const b = snapSources.find((s) => s.kind === "snapshot") ?? snapSources[0];
      if (b) {
        setBase(b.id);
        setShots(Object.fromEntries(meta.shots.map((s) => [s.id, (sources.shots[s.id] ?? []).some((o) => o.source === b.id) ? b.id : sources.shots[s.id]?.[0]?.source ?? ""])));
      }
    }
    setSelected(meta.shots[0]?.id ?? "");
  }, [sources, meta]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (Object.keys(shots).length) saveDraft({ name, base, shots });
  }, [shots, base, name]);

  const refreshSaved = useCallback(() => api.timelines().then(setSaved).catch(() => undefined), []);
  useEffect(() => { refreshSaved(); }, [refreshSaved]);

  const optionFor = useCallback((sid: string) => {
    const opts = sources?.shots[sid] ?? [];
    return opts.find((o) => o.source === shots[sid]);
  }, [sources, shots]);

  const applyBase = (srcId: string) => {
    if (!meta || !sources) return;
    setBase(srcId);
    setShots(Object.fromEntries(meta.shots.map((s) => {
      const has = (sources.shots[s.id] ?? []).some((o) => o.source === srcId);
      return [s.id, has ? srcId : shots[s.id]];
    })));
  };

  const swaps = useMemo(() => (meta?.shots ?? []).filter((s) => shots[s.id] && shots[s.id] !== base), [meta, shots, base]);
  const missing = useMemo(() => (meta?.shots ?? []).filter((s) => !optionFor(s.id)), [meta, optionFor]);

  const loadSaved = async (n: string) => {
    if (!n) return;
    const t = saved.find((x) => x.name === n);
    if (!t) return;
    setShots((cur) => ({ ...cur, ...t.shots }));
    setName(n);
    toast(`Loaded timeline ${n}`);
  };

  const save = async () => {
    const n = name.trim();
    if (!n) {
      toast("Name the timeline first", "error");
      return;
    }
    try {
      await api.saveTimeline(n, shots, swaps.length ? `base ${base}; ${swaps.map((s) => `${s.id} from ${shots[s.id]}`).join(", ")}` : `all ${base}`);
      toast(`Saved timeline ${n} to notes/studio/timelines/${n}.json`);
      refreshSaved();
    } catch (e) {
      toast(`Could not save: ${e instanceof Error ? e.message : e}`, "error");
    }
  };

  // --- build dialog ---------------------------------------------------------------------------------------------------
  const stamp = () => {
    const d = new Date();
    const p2 = (n: number) => String(n).padStart(2, "0");
    return `${p2(d.getMonth() + 1)}${p2(d.getDate())}-${p2(d.getHours())}${p2(d.getMinutes())}`;
  };
  const [bName, setBName] = useState("");
  const [bFrom, setBFrom] = useState("");
  const [bTo, setBTo] = useState("");
  const openBuild = () => {
    setBName(`${(name || "cut").replace(/[^A-Za-z0-9._-]/g, "-")}-${stamp()}`);
    setBFrom(meta?.shots[0].id ?? "");
    setBTo(meta?.shots.at(-1)?.id ?? "");
    setDialog(true);
  };
  useEffect(() => {
    const d = dlg.current;
    if (!d) return;
    if (dialog && !d.open) d.showModal();
    if (!dialog && d.open) d.close();
  }, [dialog]);
  const rangeShots = useMemo(() => {
    if (!meta) return [];
    const i0 = meta.shots.findIndex((s) => s.id === bFrom);
    const i1 = meta.shots.findIndex((s) => s.id === bTo);
    return meta.shots.slice(Math.max(0, i0), i1 + 1);
  }, [meta, bFrom, bTo]);
  const startBuild = async () => {
    try {
      const sel = Object.fromEntries(rangeShots.map((s) => [s.id, shots[s.id]]));
      const full = meta && rangeShots.length === meta.shots.length;
      const b = await api.startBuild({ name: bName, shots: sel, from: full ? undefined : bFrom, to: full ? undefined : bTo, timeline: name || undefined });
      toast(`Build ${b.name} queued`);
      setDialog(false);
      refreshBuilds();
    } catch (e) {
      toast(`Could not start the build: ${e instanceof Error ? e.message : e}`, "error");
    }
  };

  if (!meta || !sources) return <div className="loading">Loading shots and sources…</div>;
  const sel = meta.shots.find((s) => s.id === selected) ?? meta.shots[0];
  const selOpts = sources.shots[sel.id] ?? [];
  const versionOf = (vid: string | null) => versions.find((v) => v.id === vid);
  const labelOf = (srcId: string) => sources.sources.find((s) => s.id === srcId)?.label ?? srcId;
  const notesAt = (vid: string | null, sid: string) => annotations.filter((a) => a.version === vid && a.shot === sid).length;
  const recent = builds.slice(0, 8);

  return (
    <div className="timeline-view">
      <div className="tl-toolbar">
        <label className="inline-field">Timeline
          <input value={name} onChange={(e) => setName(e.target.value)} placeholder="name it to save" maxLength={64}
            pattern="[A-Za-z0-9][A-Za-z0-9._-]*" />
        </label>
        <button onClick={save}>Save</button>
        <label className="inline-field">Open
          <select value="" onChange={(e) => loadSaved(e.target.value)}>
            <option value="">{saved.length ? "saved timelines…" : "none saved yet"}</option>
            {saved.map((t) => <option key={t.name} value={t.name}>{t.name}</option>)}
          </select>
        </label>
        <label className="inline-field">Start from
          <select value={base} onChange={(e) => applyBase(e.target.value)}>
            {snapSources.map((s) => <option key={s.id} value={s.id}>{s.label}</option>)}
          </select>
        </label>
        <span className="swapcount">{swaps.length ? `${swaps.length} shot${swaps.length > 1 ? "s" : ""} swapped in` : "no swaps yet"}</span>
        <span className="pane-spacer" />
        <button className="primary" onClick={openBuild} disabled={missing.length > 0}
          title={missing.length ? `No source for ${missing.map((s) => s.id).join(", ")}` : "Assemble a new full cut from these shots"}>Build cut</button>
      </div>

      <div className="tl-top">
        <TimelinePlayer ref={player} meta={meta} pick={optionFor} onTime={setTime}
          loop={loopSel ? [sel.t0, sel.t1] : null} />
        <section className="tl-detail" aria-label={`Sources for ${sel.id}`}>
          <header>
            <h2>{sel.id} <span className="muted mono">{mmss(sel.t0)}–{mmss(sel.t1)} · {(sel.frames / FPS).toFixed(1)} s</span></h2>
            <label className="check"><input type="checkbox" checked={loopSel} onChange={(e) => setLoopSel(e.target.checked)} /> Loop this shot</label>
          </header>
          {sel.lyric && <p className="detail-lyric">{sel.lyric}</p>}
          {sel.shotlist.picture && <p className="detail-picture">{sel.shotlist.picture}</p>}
          <div className="src-grid">
            {selOpts.map((o) => (
              <SourceCard key={o.source} sid={sel.id} o={o} chosen={shots[sel.id] === o.source} label={labelOf(o.source)}
                tone={versionTone(o.version, versions)} notes={notesAt(o.version, sel.id)} version={versionOf(o.version)}
                onChoose={() => {
                  setShots({ ...shots, [sel.id]: o.source });
                  player.current?.seek(sel.t0);
                }} />
            ))}
            {!selOpts.length && <p className="empty-note">No version has a master for {sel.id}.</p>}
          </div>
        </section>
      </div>

      <div className="tl-zoom">
        <span className="muted">{tc(time)}</span>
        <span className="pane-spacer" />
        <label className="inline-field">Zoom
          <input type="range" min={1} max={6} step={0.5} value={zoom} onChange={(e) => setZoom(parseFloat(e.target.value))} />
        </label>
      </div>
      <div className="tl-scroll">
        <div className="tl-strip" style={{ width: `${zoom * 100}%` }} role="listbox" aria-label="Edit, one block per shot">
          {meta.shots.map((s) => {
            const o = optionFor(s.id);
            const swapped = shots[s.id] && shots[s.id] !== base;
            const n = annotations.filter((a) => a.shot === s.id && (a.version === o?.version)).length;
            return (
              <button key={s.id} role="option" aria-selected={sel.id === s.id}
                className={`tl-shot ${sel.id === s.id ? "on" : ""} ${swapped ? "swapped" : ""} ${o ? "" : "missing"}`}
                style={{ flexGrow: s.frames, flexBasis: 0 }}
                onClick={() => { setSelected(s.id); player.current?.seek(s.t0); }}
                title={`${s.id} · ${labelOf(shots[s.id])}\n${s.lyric}`}>
                {o && <img src={o.thumb} alt="" loading="lazy" draggable={false} />}
                <span className="shot-id">{s.id}</span>
                <span className="tl-src" style={{ borderColor: versionTone(o?.version, versions) }}>{o ? labelOf(o.source).split(" ")[0] : "none"}</span>
                {s.lyric && <span className="shot-lyric">{s.lyric}</span>}
                {n > 0 && <span className="shot-counts"><span className="n">{n}</span></span>}
              </button>
            );
          })}
          <div className="tl-playhead" style={{ left: `${(time / meta.duration) * 100}%` }} />
        </div>
      </div>

      <section className="builds" aria-label="Builds">
        <h2>Builds</h2>
        {recent.length === 0 && <p className="empty-note">Nothing built yet. Pick sources, then Build cut assembles a 1080p film with tools/assemble.py.</p>}
        <ul>{recent.map((b) => <BuildRow key={b.id} b={b} start={meta.shots.find((s) => s.id === b.from)?.t0 ?? 0}
          onCancel={() => api.cancelBuild(b.id).then(refreshBuilds)} />)}</ul>
      </section>

      <dialog ref={dlg} className="build-dialog" onClose={() => setDialog(false)}>
        <form method="dialog" onSubmit={(e) => { e.preventDefault(); startBuild(); }}>
          <h2>Build a cut</h2>
          <label className="field">Name
            <input value={bName} onChange={(e) => setBName(e.target.value)} required pattern="[A-Za-z0-9][A-Za-z0-9._-]{0,63}" />
            <small>Writes renders/studio/cuts/{bName || "…"}.mp4. Names are never overwritten.</small>
          </label>
          <div className="row">
            <label className="field">From
              <select value={bFrom} onChange={(e) => setBFrom(e.target.value)}>
                {meta.shots.map((s) => <option key={s.id} value={s.id}>{s.id} · {mmss(s.t0)}</option>)}
              </select>
            </label>
            <label className="field">To
              <select value={bTo} onChange={(e) => setBTo(e.target.value)}>
                {meta.shots.map((s) => <option key={s.id} value={s.id}>{s.id} · {mmss(s.t1)}</option>)}
              </select>
            </label>
          </div>
          <p className="build-summary">
            {rangeShots.length} shots, {rangeShots.reduce((n, s) => n + s.frames, 0)} frames
            ({(rangeShots.reduce((n, s) => n + s.frames, 0) / FPS).toFixed(1)} s) at 1920×1080, graded and encoded by tools/assemble.py --tier final.
            {" "}At a rough 4 frames a second (it depends on your assembler and machine), that takes about {Math.max(1, Math.round(rangeShots.reduce((n, s) => n + s.frames, 0) / 4 / 60))} min.
          </p>
          <ul className="build-sources">
            {rangeShots.filter((s) => shots[s.id] !== base).map((s) => <li key={s.id}><b>{s.id}</b> from {labelOf(shots[s.id])}</li>)}
            <li className="muted">{rangeShots.filter((s) => shots[s.id] === base).length} shots from {labelOf(base)}</li>
          </ul>
          <div className="row end">
            <button type="button" onClick={() => setDialog(false)}>Cancel</button>
            <button className="primary" type="submit" disabled={!rangeShots.length}>Start build</button>
          </div>
        </form>
      </dialog>
      <button className="link-btn" onClick={refreshSources}>Rescan sources</button>
    </div>
  );
}
