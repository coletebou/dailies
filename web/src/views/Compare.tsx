import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api";
import { DrawLayer, DrawingOverlay, type DrawResult } from "../components/DrawLayer";
import { ScoreTable } from "../components/Scores";
import { LyricNow, Scrubber, ShotStrip, type Marker } from "../components/Strip";
import { compositePng } from "../drawing";
import { navigate, type Route } from "../router";
import { useData } from "../store";
import { SyncGroup, useSync } from "../sync";
import type { Annotation, Cut, Tag, Version } from "../types";
import { avgOf, cutCovers, FPS, frameAt, isRemote, mmss, shotAtFrame, tc, TAGS } from "../util";

interface Slot { version: string; cut?: string }
type Quality = "auto" | "original" | "720p";

function pickCut(v: Version, cutId?: string): Cut {
  return [v.cut, ...v.alternates].find((c) => c.id === cutId) ?? v.cut;
}

/** "s05–s06 · 00:15:15–00:23:07": the film a cut holds, for a pane whose cut doesn't have the playhead. */
function coverage(cut: Cut): string {
  const shots = cut.meta?.from ? `${cut.meta.from}–${cut.meta.to ?? cut.meta.from} · ` : "";
  return `${shots}${tc(cut.film_f0 / FPS)}–${cut.film_f1 === null ? "end" : tc((cut.film_f1 - 1) / FPS)}`;
}

function srcFor(cut: Cut, q: Quality): { url: string; proxy: boolean } {
  const wantProxy = q === "720p" || (q === "auto" && isRemote());
  if (wantProxy && cut.proxy_ready && cut.proxy_url) return { url: cut.proxy_url, proxy: true };
  return { url: cut.url, proxy: false };
}

function VersionSelect({ value, versions, onChange, label }: {
  value: string; versions: Version[]; onChange: (id: string) => void; label: string;
}) {
  const cuts = versions.filter((v) => v.kind === "cut");
  const studio = versions.filter((v) => v.kind === "studio");
  const fmt = (v: Version) => {
    const f = avgOf(v.critiques, "flash");
    const p = avgOf(v.critiques, "pro");
    return v.pending ? `${v.label} (rendering…)` : f !== null || p !== null ? `${v.label}   ${f?.toFixed(2) ?? "–"} / ${p?.toFixed(2) ?? "–"}` : v.label;
  };
  return (
    <select className="vselect" value={value} onChange={(e) => onChange(e.target.value)} aria-label={label}>
      <optgroup label="Versions">
        {cuts.map((v) => <option key={v.id} value={v.id} disabled={v.pending}>{fmt(v)}</option>)}
      </optgroup>
      {studio.length > 0 && (
        <optgroup label="Studio cuts">
          {studio.map((v) => <option key={v.id} value={v.id} disabled={v.pending}>{fmt(v)}</option>)}
        </optgroup>
      )}
    </select>
  );
}

function Pane({ i, slot, version, versions, group, quality, frame, isMaster, visible, drawing, overlay, onSlot,
  onRemove, onMaster, onDraw, onSaveDrawing, onCancelDraw, saving, canRemove }: {
  i: number; slot: Slot; version: Version; versions: Version[]; group: SyncGroup; quality: Quality; frame: number;
  isMaster: boolean;
  visible: boolean; drawing: boolean; overlay: Annotation[]; onSlot: (s: Slot) => void; onRemove: () => void;
  onMaster: () => void; onDraw: () => void; onSaveDrawing: (r: DrawResult) => void; onCancelDraw: () => void;
  saving: boolean; canRemove: boolean;
}) {
  const cut = pickCut(version, slot.cut);
  const { url, proxy } = srcFor(cut, quality);
  const wantProxy = quality === "720p" || (quality === "auto" && isRemote());
  useEffect(() => {
    // ask the server to make this cut's 720p proxy now (it answers 503 and queues it ahead of warm-up)
    if (wantProxy && !cut.proxy_ready && cut.proxy_url) fetch(cut.proxy_url, { method: "GET" }).catch(() => undefined);
  }, [wantProxy, cut.proxy_ready, cut.proxy_url]);
  const [el, setEl] = useState<HTMLVideoElement | null>(null);
  const ref = useCallback((v: HTMLVideoElement | null) => {
    group.register(i, v);
    setEl(v);
  }, [group, i]);
  // a partial cut's own time 0 is film frame film_f0: the group maps the shared film clock into this pane's media
  useEffect(() => group.setRange(i, cut.film_f0, cut.film_f1), [group, i, cut.film_f0, cut.film_f1]);
  const outside = !cutCovers(cut, frame);
  const shapes = useMemo(() => overlay.flatMap((a) => a.drawing?.shapes ?? []), [overlay]);
  const labels = useMemo(() => overlay.map((a) => ({ text: a.text, tag: a.tag })), [overlay]);
  const alts = [version.cut, ...version.alternates];
  return (
    <section className={`pane ${isMaster ? "master" : ""} ${visible ? "" : "hidden"} ${drawing ? "drawing" : ""}`}
      aria-label={`Pane ${i + 1}: ${version.label}`}>
      <header className="pane-head">
        <span className="pane-num">{i + 1}</span>
        <VersionSelect value={version.id} versions={versions} onChange={(id) => onSlot({ version: id })} label={`Version in pane ${i + 1}`} />
        {alts.length > 1 && (
          <select className="cutselect" value={cut.id} onChange={(e) => onSlot({ version: version.id, cut: e.target.value })}
            aria-label="Which file of this version">
            {alts.map((c) => <option key={c.id} value={c.id}>{c.path.replace(/^renders\//, "")}</option>)}
          </select>
        )}
        <span className="pane-spacer" />
        {proxy && <span className="chip" title="Playing the 720p proxy of this cut">720p</span>}
        {wantProxy && !proxy && <span className="chip" title="The 720p proxy is being made; playing the original until it is ready">original · 720p soon</span>}
        <button className={`icon ${isMaster ? "on" : ""}`} onClick={onMaster} title="Play this pane's audio"
          aria-pressed={isMaster} aria-label="Audio from this pane">{isMaster ? "◉" : "○"}</button>
        <button className="icon" onClick={onDraw} title="Draw on this frame (D)" aria-label="Draw on this frame">✎</button>
        {canRemove && <button className="icon" onClick={onRemove} title="Remove pane" aria-label="Remove pane">×</button>}
      </header>
      <div className="pane-video">
        <video ref={ref} src={url} preload="auto" playsInline muted={!isMaster} onClick={() => group.toggle()} />
        {!drawing && !outside && shapes.length + labels.length > 0 && <DrawingOverlay video={el} shapes={shapes} labels={labels} />}
        {outside && !drawing && (
          <div className="pane-outside" role="status">
            <b>Outside this cut</b>
            <span className="mono">{coverage(cut)}</span>
            <button className="small" onClick={() => group.seekFrame(frame < cut.film_f0 ? cut.film_f0 : (cut.film_f1 ?? 1) - 1)}>
              {frame < cut.film_f0 ? "Go to its first frame" : "Go to its last frame"}</button>
          </div>
        )}
        {drawing && el && <DrawLayer video={el} onSave={onSaveDrawing} onCancel={onCancelDraw} saving={saving} />}
      </div>
    </section>
  );
}

export default function Compare({ route }: { route: Route }) {
  const { meta, versions, sources, annotations, refreshAnnotations, toast } = useData();
  const group = useMemo(() => new SyncGroup(), []);
  useEffect(() => () => group.dispose(), [group]);
  const st = useSync(group);
  const playable = useMemo(() => versions.filter((v) => !v.pending), [versions]);
  const [slots, setSlots] = useState<Slot[]>([]);
  const [master, setMaster] = useState(0);
  const [layout, setLayout] = useState<"grid" | "flip">("grid");
  const [flipIdx, setFlipIdx] = useState(0);
  const [quality, setQuality] = useState<Quality>("auto");
  const [loopShot, setLoopShot] = useState<string | null>(null);
  const [drawPane, setDrawPane] = useState<number | null>(null);
  const [saving, setSaving] = useState(false);
  const [side, setSide] = useState<"notes" | "critic" | "scores">("notes");
  const [composer, setComposer] = useState<{ tag: Tag; text: string } | null>(null);
  const [muted, setMuted] = useState(false);

  // --- initial state and jumps from other views ---------------------------------------------------------------------
  const applyRoute = useCallback((p: URLSearchParams) => {
    const ids = (p.get("v") ?? "").split(",").filter((id) => playable.some((v) => v.id === id)).slice(0, 4);
    if (ids.length) setSlots(ids.map((version) => ({ version })));
    const t = p.get("t");
    if (t !== null) group.seekFrame(frameAt(parseFloat(t) + 1e-4));
    const focus = p.get("focus");
    if (focus) {
      const k = ids.indexOf(focus);
      if (k >= 0) {
        setMaster(k);
        setFlipIdx(k);
      }
    }
  }, [playable, group]);

  const inited = useRef(false);
  useEffect(() => {
    if (inited.current || !playable.length) return;
    inited.current = true;
    if (route.params.get("v")) applyRoute(route.params);
    else {
      const cuts = playable.filter((v) => v.kind === "cut");
      setSlots(cuts.slice(-2).map((v) => ({ version: v.id })));
    }
  }, [playable, route.params, applyRoute]);
  const jump = route.params.get("j");
  useEffect(() => {
    if (jump && inited.current) applyRoute(route.params);
  }, [jump]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => group.setCount(slots.length), [group, slots.length]);
  useEffect(() => group.setMaster(Math.min(master, Math.max(0, slots.length - 1))), [group, master, slots.length]);
  useEffect(() => group.setMuted(muted), [group, muted]);
  useEffect(() => { group.filmLength = meta?.duration ?? 0; }, [group, meta]);

  // keep the URL shareable: versions + paused frame
  useEffect(() => {
    if (st.playing || !slots.length) return;
    const id = window.setTimeout(() => navigate("compare", {
      v: slots.map((s) => s.version).join(","), t: (st.frame / FPS).toFixed(3),
    }, true), 400);
    return () => window.clearTimeout(id);
  }, [st.frame, st.playing, slots]);

  // --- derived ------------------------------------------------------------------------------------------------------
  const slotVersions = slots.map((s) => playable.find((v) => v.id === s.version)).filter((v): v is Version => !!v);
  const activeVersion = slotVersions[Math.min(master, slotVersions.length - 1)];
  const shot = meta ? shotAtFrame(meta, st.frame) : undefined;
  const visIds = new Set(slotVersions.map((v) => v.id));

  useEffect(() => {
    const s = meta?.shots.find((x) => x.id === loopShot);
    group.loop = s ? [s.t0, s.t1] : null;
  }, [loopShot, meta, group]);

  const thumbs = useMemo(() => {
    const out: Record<string, string | undefined> = {};
    if (!sources || !meta) return out;
    const pref = activeVersion?.sources ?? [];
    for (const s of meta.shots) {
      const opts = sources.shots[s.id] ?? [];
      out[s.id] = (opts.find((o) => pref.includes(o.source)) ?? opts[0])?.thumb;
    }
    return out;
  }, [sources, meta, activeVersion]);

  const visibleNotes = useMemo(() => annotations.filter((a) => visIds.has(a.version)).sort((a, b) => a.frame - b.frame),
    [annotations, slotVersions.map((v) => v.id).join()]); // eslint-disable-line react-hooks/exhaustive-deps
  const criticMoments = useMemo(() => slotVersions.flatMap((v) => v.critiques.flatMap((c) => c.weakest.map((w, k) => ({
    ...w, version: v.id, model: c.model_short, label: c.label, key: `${c.file}-${k}`,
  })))).sort((a, b) => a.t - b.t), [slotVersions.map((v) => v.id).join(), versions]); // eslint-disable-line react-hooks/exhaustive-deps

  const markers: Marker[] = useMemo(() => [
    ...visibleNotes.map((a) => ({ t: a.time, kind: "note" as const, tag: a.tag, label: `${a.version}: ${a.text || "drawing"}`, id: a.id })),
    ...criticMoments.map((m) => ({ t: m.t, kind: "critic" as const, label: `${m.version} ${m.model}: ${m.problem}`, id: m.key })),
  ], [visibleNotes, criticMoments]);

  const counts = useMemo(() => {
    const c: Record<string, { notes: number; critic: number }> = {};
    if (!meta) return c;
    const bump = (t: number, k: "notes" | "critic") => {
      const s = shotAtFrame(meta, frameAt(t));
      if (!s) return;
      c[s.id] = c[s.id] ?? { notes: 0, critic: 0 };
      c[s.id][k]++;
    };
    visibleNotes.forEach((a) => bump(a.time, "notes"));
    criticMoments.forEach((m) => bump(m.t, "critic"));
    return c;
  }, [meta, visibleNotes, criticMoments]);

  // drawings within ±2 frames of the playhead, per pane version
  const overlayFor = (vid: string) => annotations.filter((a) => a.version === vid && Math.abs(a.frame - st.frame) <= 2
    && (a.drawing?.shapes.length || a.frame === st.frame));

  // --- actions ------------------------------------------------------------------------------------------------------
  const gotoShot = useCallback((sid: string) => {
    const s = meta?.shots.find((x) => x.id === sid);
    if (!s) return;
    if (loopShot) setLoopShot(sid);
    group.seekFrame(s.f0);
  }, [meta, group, loopShot]);

  const stepShot = (d: number) => {
    if (!meta || !shot) return;
    const i = meta.shots.findIndex((s) => s.id === shot.id);
    const target = d < 0 && st.frame > shot.f0 + 3 ? shot : meta.shots[Math.max(0, Math.min(meta.shots.length - 1, i + d))];
    gotoShot(target.id);
  };

  /** Notes are made in film frames; a pane whose cut doesn't have this frame shows something else, so refuse. */
  const coversNow = (i: number): boolean => {
    const v = slotVersions[i];
    if (!v) return false;
    const cut = pickCut(v, slots[i]?.cut);
    if (cutCovers(cut, st.frame)) return true;
    toast(`${v.label} has no picture at ${tc(st.frame / FPS)}: it holds ${coverage(cut)}`, "error");
    return false;
  };

  const startDraw = (i: number) => {
    if (!coversNow(i)) return;
    group.pause();
    setDrawPane(i);
    setComposer(null);
  };

  const saveNote = async (vIndex: number, tag: Tag, text: string, shapes?: DrawResult["shapes"]) => {
    const v = slotVersions[vIndex];
    if (!v || !coversNow(vIndex)) return;
    setSaving(true);
    try {
      const video = group.getVideo(vIndex);
      const drawing = shapes?.length && video ? { aspect: video.videoWidth / video.videoHeight, shapes } : null;
      const a = await api.createAnnotation({
        version: v.id, cut: pickCut(v, slots[vIndex]?.cut).path, time: st.frame / FPS, frame: st.frame,
        shot: shot?.id, tag, text, drawing,
      });
      if (drawing && video) {
        try {
          await api.putFrame(a.id, await compositePng(video, drawing));
        } catch (e) {
          toast(`Note saved, but the frame image failed: ${e instanceof Error ? e.message : e}`, "error");
        }
      }
      await refreshAnnotations();
      toast(drawing ? `Saved a drawing on ${v.label} at ${tc(st.frame / FPS)}` : `Saved a note on ${v.label} at ${tc(st.frame / FPS)}`);
      setDrawPane(null);
      setComposer(null);
    } catch (e) {
      toast(`Could not save: ${e instanceof Error ? e.message : e}`, "error");
    } finally {
      setSaving(false);
    }
  };

  const setSlot = (i: number, s: Slot) => setSlots((cur) => cur.map((x, k) => (k === i ? s : x)));
  const addPane = () => {
    if (slots.length >= 4) return;
    const unused = playable.filter((v) => v.kind === "cut" && !slots.some((s) => s.version === v.id));
    const v = unused.at(-1) ?? playable.at(-1);
    if (v) setSlots((cur) => [...cur, { version: v.id }]);
  };
  const removePane = (i: number) => {
    setSlots((cur) => cur.filter((_, k) => k !== i));
    if (master >= i && master > 0) setMaster(master - 1);
  };

  // --- keyboard -----------------------------------------------------------------------------------------------------
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement)?.closest?.("input, textarea, select") || e.metaKey || e.ctrlKey || e.altKey) return;
      if (drawPane !== null) return;
      const k = e.key;
      if (k === " ") { e.preventDefault(); group.toggle(); }
      else if (k === "," || k === "<") { e.preventDefault(); group.step(e.shiftKey ? -10 : -1); }
      else if (k === "." || k === ">") { e.preventDefault(); group.step(e.shiftKey ? 10 : 1); }
      else if (k === "ArrowLeft") { e.preventDefault(); group.seekTime(st.time - (e.shiftKey ? 5 : 1)); }
      else if (k === "ArrowRight") { e.preventDefault(); group.seekTime(st.time + (e.shiftKey ? 5 : 1)); }
      else if (k === "[") stepShot(-1);
      else if (k === "]") stepShot(1);
      else if (k === "l" || k === "L") setLoopShot((cur) => (cur ? null : shot?.id ?? null));
      else if (k === "f" || k === "F") setLayout((l) => (l === "grid" ? "flip" : "grid"));
      else if (k === "m" || k === "M") setMuted((m) => !m);
      else if (k === "n" || k === "N") { group.pause(); setComposer({ tag: "problem", text: "" }); setSide("notes"); }
      else if (k === "d" || k === "D") startDraw(Math.min(master, slots.length - 1));
      else if (/^[1-4]$/.test(k) && +k <= slots.length) { setFlipIdx(+k - 1); setMaster(+k - 1); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  if (!meta) return <div className="loading">Loading the edit…</div>;
  if (!playable.length) return <div className="empty-state">No playable cuts yet. Cuts appear here as soon as renders/snapshots or renders/previews has one.</div>;

  return (
    <div className={`compare ${drawPane !== null ? "is-drawing" : ""}`}>
      <div className="compare-main">
        <div className="stage-bar">
          <div className="seg" role="radiogroup" aria-label="Layout">
            <button className={layout === "grid" ? "on" : ""} onClick={() => setLayout("grid")} title="Side by side (F)">Side by side</button>
            <button className={layout === "flip" ? "on" : ""} onClick={() => setLayout("flip")} title="Stacked; 1–4 flips between versions on the same frame (F)">Flip</button>
          </div>
          {layout === "flip" && (
            <div className="seg flip-tabs" role="tablist">
              {slotVersions.map((v, i) => (
                <button key={i} role="tab" aria-selected={flipIdx === i} className={flipIdx === i ? "on" : ""}
                  onClick={() => { setFlipIdx(i); setMaster(i); }}>{i + 1} · {v.label}</button>
              ))}
            </div>
          )}
          <span className="pane-spacer" />
          <label className="inline-field">Quality
            <select value={quality} onChange={(e) => setQuality(e.target.value as Quality)}>
              <option value="auto">Auto ({isRemote() ? "720p when ready" : "original"})</option>
              <option value="original">Original files</option>
              <option value="720p">720p proxies</option>
            </select>
          </label>
          <button onClick={addPane} disabled={slots.length >= 4}>Add version</button>
        </div>

        <div className={`stage layout-${layout} n-${slots.length}`}>
          {slots.map((s, i) => {
            const v = playable.find((x) => x.id === s.version);
            if (!v) return null;
            const visible = drawPane !== null ? drawPane === i : layout === "grid" || flipIdx === i;
            return (
              <Pane key={i} i={i} slot={s} version={v} versions={versions} group={group} quality={quality}
                frame={st.frame} isMaster={master === i} visible={visible} drawing={drawPane === i} overlay={overlayFor(v.id)}
                onSlot={(ns) => setSlot(i, ns)} onRemove={() => removePane(i)} onMaster={() => setMaster(i)}
                onDraw={() => startDraw(i)} onSaveDrawing={(r) => saveNote(i, r.tag, r.text, r.shapes)}
                onCancelDraw={() => setDrawPane(null)} saving={saving} canRemove={slots.length > 1} />
            );
          })}
        </div>

        <div className="transport" role="toolbar" aria-label="Transport">
          <button className="icon" onClick={() => stepShot(-1)} title="Previous shot ([)" aria-label="Previous shot">⏮</button>
          <button className="icon" onClick={() => group.step(-1)} title="Back one frame (,)" aria-label="Back one frame">◂</button>
          <button className="play" onClick={() => group.toggle()} title="Play / pause (space)" aria-label={st.playing ? "Pause" : "Play"}>
            {st.playing ? "❚❚" : "▶"}
          </button>
          <button className="icon" onClick={() => group.step(1)} title="Forward one frame (.)" aria-label="Forward one frame">▸</button>
          <button className="icon" onClick={() => stepShot(1)} title="Next shot (])" aria-label="Next shot">⏭</button>
          <span className="timecode" title="minutes:seconds:frames at 30 fps">{tc(st.time)}</span>
          <span className="frameno">f{st.frame}</span>
          {shot && <span className="shotnow"><b>{shot.id}</b> <i>{shot.lyric}</i></span>}
          <span className="pane-spacer" />
          <button className={loopShot ? "on" : ""} onClick={() => setLoopShot(loopShot ? null : shot?.id ?? null)}
            title="Loop the current shot in every pane (L)" aria-pressed={!!loopShot}>{loopShot ? `Looping ${loopShot}` : "Loop shot"}</button>
          <select value={st.rate} onChange={(e) => group.setRate(parseFloat(e.target.value))} aria-label="Speed">
            {[0.25, 0.5, 1, 1.5, 2].map((r) => <option key={r} value={r}>{r}×</option>)}
          </select>
          <button className="icon" onClick={() => setMuted(!muted)} aria-pressed={muted} title="Mute (M)">{muted ? "🔇" : "🔊"}</button>
          <button onClick={() => { group.pause(); setComposer({ tag: "problem", text: "" }); setSide("notes"); }} title="Add a note at this frame (N)">Add note</button>
          <button onClick={() => startDraw(Math.min(master, slots.length - 1))} title="Draw on this frame (D)">Draw</button>
        </div>
        <LyricNow meta={meta} time={st.time} />
        <Scrubber meta={meta} time={st.time} markers={markers} onSeek={(t) => group.seekTime(t)} />
        <ShotStrip meta={meta} thumbs={thumbs} current={shot?.id} counts={counts} onPick={gotoShot} loopShot={loopShot} />
      </div>

      <aside className="side" aria-label="Notes, critic and scores">
        <div className="seg side-tabs" role="tablist">
          <button role="tab" aria-selected={side === "notes"} className={side === "notes" ? "on" : ""} onClick={() => setSide("notes")}>
            Notes <span className="count">{visibleNotes.length}</span></button>
          <button role="tab" aria-selected={side === "critic"} className={side === "critic" ? "on" : ""} onClick={() => setSide("critic")}>
            Critic <span className="count">{criticMoments.length}</span></button>
          <button role="tab" aria-selected={side === "scores"} className={side === "scores" ? "on" : ""} onClick={() => setSide("scores")}>Scores</button>
        </div>

        {side === "notes" && (
          <div className="side-body">
            {composer ? (
              <form className="composer" onSubmit={(e) => { e.preventDefault(); saveNote(Math.min(master, slots.length - 1), composer.tag, composer.text); }}>
                <div className="composer-where">
                  <b>{activeVersion?.label}</b> at <span className="mono">{tc(st.frame / FPS)}</span> · {shot?.id}
                </div>
                <div className="seg" role="radiogroup" aria-label="Tag">
                  {TAGS.map((t) => (
                    <button type="button" key={t.id} className={`tagbtn tag-${t.id} ${composer.tag === t.id ? "on" : ""}`}
                      onClick={() => setComposer({ ...composer, tag: t.id })}>{t.glyph} {t.label}</button>
                  ))}
                </div>
                <textarea autoFocus rows={3} value={composer.text} placeholder="What do you see at this frame?"
                  onChange={(e) => setComposer({ ...composer, text: e.target.value })}
                  onKeyDown={(e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) (e.currentTarget.form as HTMLFormElement).requestSubmit(); if (e.key === "Escape") setComposer(null); }} />
                <div className="row">
                  <button className="primary" type="submit" disabled={saving || !composer.text.trim()}>{saving ? "Saving…" : "Save note"}</button>
                  <button type="button" onClick={() => setComposer(null)}>Cancel</button>
                  <span className="hint">⌘↵ saves</span>
                </div>
              </form>
            ) : (
              <button className="wide" onClick={() => { group.pause(); setComposer({ tag: "problem", text: "" }); }}>Add a note at {tc(st.frame / FPS)}</button>
            )}
            {visibleNotes.length === 0 && <p className="empty-note">No notes on these versions yet. Pause on a frame and press N to write one, or D to draw on it.</p>}
            <ul className="note-list">
              {visibleNotes.map((a) => (
                <li key={a.id} className={`note ${Math.abs(a.frame - st.frame) <= 2 ? "here" : ""} ${a.status}`}>
                  <button className="note-jump" onClick={() => group.seekFrame(a.frame)}>
                    <span className={`tagglyph tag-${a.tag}`}>{TAGS.find((t) => t.id === a.tag)?.glyph}</span>
                    <span className="mono">{tc(a.time)}</span>
                    <span className="note-v">{a.version}</span>
                    {a.drawing && <span className="chip">drawing</span>}
                    <span className="note-text">{a.text || <i>drawing only</i>}</span>
                  </button>
                  <button className="icon small" title={a.status === "open" ? "Mark done" : "Reopen"}
                    onClick={async () => { await api.patchAnnotation(a.id, { status: a.status === "open" ? "done" : "open" }); refreshAnnotations(); }}>
                    {a.status === "open" ? "✓" : "↺"}</button>
                </li>
              ))}
            </ul>
          </div>
        )}

        {side === "critic" && (
          <div className="side-body">
            <p className="side-intro">The critic's five weakest moments per critique, read-only. Magenta ◇ on the scrubber.</p>
            <ul className="note-list">
              {criticMoments.map((m) => (
                <li key={m.key} className={`note critic ${Math.abs(frameAt(m.t) - st.frame) < FPS ? "here" : ""}`}>
                  <button className="note-jump" onClick={() => group.seekTime(m.t)}>
                    <span className="tagglyph critic">◇</span>
                    <span className="mono">{mmss(m.t)}</span>
                    <span className="note-v">{m.label} {m.model}</span>
                    <span className="note-text">{m.problem}<small>Fix: {m.fix}</small></span>
                  </button>
                </li>
              ))}
            </ul>
          </div>
        )}

        {side === "scores" && (
          <div className="side-body">
            <ScoreTable versions={slotVersions} />
            {slotVersions.map((v) => v.critiques.slice(0, 2).map((c) => (
              <p key={c.file} className="overall"><b>{c.label} {c.model_short}:</b> {c.overall}</p>
            )))}
          </div>
        )}
        <details className="keys">
          <summary>Keys</summary>
          <dl>
            <dt>Space</dt><dd>play / pause</dd><dt>, .</dt><dd>one frame back / forward (Shift: 10)</dd>
            <dt>[ ]</dt><dd>previous / next shot</dd><dt>← →</dt><dd>one second (Shift: 5)</dd>
            <dt>L</dt><dd>loop the shot</dd><dt>F</dt><dd>side by side / flip</dd><dt>1–4</dt><dd>pick a pane (flip shows it)</dd>
            <dt>N</dt><dd>note at this frame</dd><dt>D</dt><dd>draw on this frame</dd><dt>M</dt><dd>mute</dd>
          </dl>
        </details>
      </aside>
    </div>
  );
}
