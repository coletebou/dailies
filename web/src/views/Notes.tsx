import { useMemo, useState } from "react";
import { api } from "../api";
import { ScorePair, ScoreTable } from "../components/Scores";
import { navigate } from "../router";
import { useData } from "../store";
import type { Annotation } from "../types";
import { CRITERIA, FPS, frameAt, mediaUrl, mmss, shotAtFrame, tc, TAGS } from "../util";

function jump(version: string, t: number) {
  navigate("compare", { v: version, t: t.toFixed(3), focus: version, j: Date.now() });
}

export default function Notes() {
  const { annotations, versions, meta, sources, refreshAnnotations, toast } = useData();
  const [fv, setFv] = useState("");
  const [ftag, setFtag] = useState("");
  const [fstatus, setFstatus] = useState("open");
  const [fshot, setFshot] = useState("");
  const [q, setQ] = useState("");
  const [critic, setCritic] = useState(true);
  const [editing, setEditing] = useState<{ id: string; text: string } | null>(null);

  const rows = useMemo(() => {
    const notes = annotations.filter((a) => (!fv || a.version === fv) && (!ftag || a.tag === ftag)
      && (!fstatus || a.status === fstatus) && (!fshot || a.shot === fshot)
      && (!q || a.text.toLowerCase().includes(q.toLowerCase())))
      .map((a) => ({ kind: "note" as const, t: a.time, version: a.version, a }));
    const crit = critic && !ftag ? versions.flatMap((v) => v.critiques.flatMap((c) => c.weakest.map((w, k) => ({
      kind: "critic" as const, t: w.t, version: v.id, w, c, key: `${c.file}-${k}`,
    })))).filter((r) => (!fv || r.version === fv) && (!q || r.w.problem.toLowerCase().includes(q.toLowerCase()))
      && (!fshot || (meta && meta.shots.find((s) => s.id === fshot && r.t >= s.t0 && r.t < s.t1)))) : [];
    return [...notes, ...crit].sort((a, b) => (a.version === b.version ? a.t - b.t : a.version < b.version ? 1 : -1));
  }, [annotations, versions, fv, ftag, fstatus, fshot, q, critic, meta]);

  const act = async (fn: () => Promise<unknown>, ok: string) => {
    try {
      await fn();
      await refreshAnnotations();
      toast(ok);
    } catch (e) {
      toast(`Failed: ${e instanceof Error ? e.message : e}`, "error");
    }
  };

  const remove = (a: Annotation) => {
    if (window.confirm(`Delete this note on ${a.version} at ${tc(a.time)}?`)) act(() => api.deleteAnnotation(a.id), "Note deleted");
  };

  const cuts = versions.filter((v) => v.kind === "cut" && !v.pending);
  // a still for rows without a composited frame: that shot's thumbnail from the version's own masters
  const thumbAt = (vid: string, t: number): string | undefined => {
    if (!meta || !sources) return undefined;
    const shot = shotAtFrame(meta, frameAt(t));
    if (!shot) return undefined;
    const pref = versions.find((v) => v.id === vid)?.sources ?? [];
    const opts = sources.shots[shot.id] ?? [];
    return (opts.find((o) => pref.includes(o.source)) ?? opts[0])?.thumb;
  };
  const openCount = annotations.filter((a) => a.status === "open").length;

  return (
    <div className="notes-view">
      <div className="notes-main">
        <div className="notes-bar">
          <input type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search notes" aria-label="Search notes" />
          <select value={fv} onChange={(e) => setFv(e.target.value)} aria-label="Version">
            <option value="">all versions</option>
            {versions.map((v) => <option key={v.id} value={v.id}>{v.label}</option>)}
          </select>
          <select value={ftag} onChange={(e) => setFtag(e.target.value)} aria-label="Tag">
            <option value="">all tags</option>
            {TAGS.map((t) => <option key={t.id} value={t.id}>{t.glyph} {t.label}</option>)}
          </select>
          <select value={fstatus} onChange={(e) => setFstatus(e.target.value)} aria-label="Status">
            <option value="open">open</option><option value="done">done</option><option value="">open and done</option>
          </select>
          <select value={fshot} onChange={(e) => setFshot(e.target.value)} aria-label="Shot">
            <option value="">all shots</option>
            {(meta?.shots ?? []).map((s) => <option key={s.id} value={s.id}>{s.id}</option>)}
          </select>
          <label className="check"><input type="checkbox" checked={critic} onChange={(e) => setCritic(e.target.checked)} /> Critic's weakest moments</label>
          <span className="pane-spacer" />
          <span className="muted">{openCount} open · saved to notes/studio/annotations.json</span>
        </div>
        {rows.length === 0 && <p className="empty-state">No notes match. In Compare, pause on a frame and press N to write one or D to draw on it.</p>}
        <ul className="notes-list">
          {rows.map((r) => r.kind === "note" ? (
            <li key={r.a.id} className={`nrow ${r.a.status}`}>
              <button className="nrow-jump" onClick={() => jump(r.a.version, r.a.time)} title="Open in Compare at this frame">
                {r.a.frame_png ? <img src={`${mediaUrl(r.a.frame_png)}?v=${encodeURIComponent(r.a.updated)}`} alt="Annotated frame" loading="lazy" />
                  : thumbAt(r.a.version, r.a.time) ? <img className="dim" src={thumbAt(r.a.version, r.a.time)} alt="" loading="lazy" />
                  : <span className={`tagglyph big tag-${r.a.tag}`}>{TAGS.find((t) => t.id === r.a.tag)?.glyph}</span>}
              </button>
              <div className="nrow-body">
                <div className="nrow-head">
                  <span className={`tagglyph tag-${r.a.tag}`}>{TAGS.find((t) => t.id === r.a.tag)?.glyph}</span>
                  <b>{r.a.version}</b><span className="mono">{tc(r.a.time)}</span><span className="muted">f{r.a.frame} · {r.a.shot}</span>
                  <span className="muted">{r.a.author} · {r.a.created.slice(5, 16).replace("T", " ")}</span>
                </div>
                {editing?.id === r.a.id ? (
                  <form onSubmit={(e) => { e.preventDefault(); act(() => api.patchAnnotation(r.a.id, { text: editing.text }), "Note updated"); setEditing(null); }}>
                    <textarea autoFocus rows={2} value={editing.text} onChange={(e) => setEditing({ ...editing, text: e.target.value })} />
                    <div className="row"><button className="primary small" type="submit">Save</button><button className="small" type="button" onClick={() => setEditing(null)}>Cancel</button></div>
                  </form>
                ) : <p className="nrow-text">{r.a.text || <i className="muted">drawing only</i>}</p>}
                <div className="row">
                  <button className="small" onClick={() => jump(r.a.version, r.a.time)}>Open</button>
                  <button className="small" onClick={() => act(() => api.patchAnnotation(r.a.id, { status: r.a.status === "open" ? "done" : "open" }), r.a.status === "open" ? "Marked done" : "Reopened")}>
                    {r.a.status === "open" ? "Mark done" : "Reopen"}</button>
                  <button className="small" onClick={() => setEditing({ id: r.a.id, text: r.a.text })}>Edit</button>
                  <button className="small danger" onClick={() => remove(r.a)}>Delete</button>
                </div>
              </div>
            </li>
          ) : (
            <li key={r.key} className="nrow critic">
              <button className="nrow-jump" onClick={() => jump(r.version, r.t)} title="Open in Compare at this moment">
                {thumbAt(r.version, r.t) ? <img className="dim" src={thumbAt(r.version, r.t)} alt="" loading="lazy" />
                  : <span className="tagglyph big critic">◇</span>}
              </button>
              <div className="nrow-body">
                <div className="nrow-head">
                  <span className="tagglyph critic">◇</span><b>{r.version}</b><span className="mono">{mmss(r.t)}</span>
                  <span className="muted">critic · {r.c.label} {r.c.model_short}</span>
                </div>
                <p className="nrow-text">{r.w.problem}</p>
                <p className="nrow-fix">Fix: {r.w.fix}</p>
              </div>
            </li>
          ))}
        </ul>
      </div>
      <aside className="versions-panel" aria-label="Versions and critic scores">
        <h2>Versions</h2>
        <ul className="vlist">
          {versions.map((v) => (
            <li key={v.id}>
              <button onClick={() => jump(v.id, v.cut.film_f0 / FPS)} disabled={v.pending}>
                <b>{v.label}</b>
                <ScorePair v={v} />
                <span className="muted">{v.pending ? "rendering…" : v.cut.path.replace(/^renders\//, "")}</span>
              </button>
            </li>
          ))}
        </ul>
        <h2>Scores by criterion</h2>
        <ScoreTable versions={cuts.slice(-3)} />
        <p className="muted small-print">Latest three versions; 1–10 per criterion: {CRITERIA.join(", ")}. Hover a cell for the critic's reason.</p>
      </aside>
    </div>
  );
}
