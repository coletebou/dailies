import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api";
import { useData } from "../store";
import type { LiveItem } from "../types";
import { ago, bytes } from "../util";

const TYPES: { id: LiveItem["type"]; label: string }[] = [
  { id: "preview", label: "Previews" },
  { id: "sheet", label: "Contact sheets" },
  { id: "qa", label: "QA stills" },
  { id: "part", label: "Snapshot parts" },
];

function Card({ it, fresh, onOpen }: { it: LiveItem; fresh: boolean; onOpen: () => void }) {
  const [hover, setHover] = useState(false);
  const recent = Date.now() / 1000 - it.mtime < 120;
  return (
    <li className={`live-card ${it.type} ${fresh ? "fresh" : ""}`}>
      <button className="live-media" onClick={onOpen} onMouseEnter={() => setHover(true)} onMouseLeave={() => setHover(false)}
        aria-label={`Open ${it.name}`}>
        {it.type === "preview" ? (
          hover ? <video src={it.url} autoPlay muted loop playsInline poster={it.thumb} /> : <img src={it.thumb} alt="" loading="lazy" />
        ) : it.type === "part" ? (
          <img src={it.thumb} alt="" loading="lazy" />
        ) : (
          <img src={it.url} alt="" loading="lazy" />
        )}
        {it.type === "part" && (it.temp || recent) && <span className="chip hot">{it.temp ? "chunk in progress" : "just landed"}</span>}
      </button>
      <div className="live-meta">
        <b>{it.sid ?? "—"}</b>
        <span className="muted">{TYPES.find((t) => t.id === it.type)?.label.replace(/s$/, "")}</span>
        {it.version && <span className="muted">{it.version}</span>}
        {it.range && <span className="muted mono">f{it.range[0]}–{it.range[1]}</span>}
        <span className="pane-spacer" />
        <time dateTime={new Date(it.mtime * 1000).toISOString()} title={new Date(it.mtime * 1000).toLocaleString()}>{ago(it.mtime)}</time>
      </div>
      <div className="live-name" title={it.path}>{it.name} · {bytes(it.size)}</div>
    </li>
  );
}

export default function Live() {
  const { meta, toast, refreshSources } = useData();
  const [items, setItems] = useState<LiveItem[]>([]);
  const [perShot, setPerShot] = useState<Record<string, number>>({});
  const [types, setTypes] = useState<Set<string>>(new Set(["preview", "sheet", "qa"]));
  const [shot, setShot] = useState("");
  const [auto, setAuto] = useState(true);
  const [open, setOpen] = useState<LiveItem | null>(null);
  const [freshSet, setFreshSet] = useState<Set<string>>(new Set());
  const [lastPoll, setLastPoll] = useState<number | null>(null);
  const seen = useRef<Map<string, number>>(new Map());

  const pull = useCallback(async () => {
    try {
      const r = await api.live({ shot, types: [...types].join(","), limit: 240 });
      const fresh = new Set<string>();
      if (seen.current.size) {
        r.items.forEach((it) => {
          const m = seen.current.get(it.path);
          if (m === undefined || m < it.mtime) fresh.add(it.path);
        });
      }
      r.items.forEach((it) => seen.current.set(it.path, it.mtime));
      setFreshSet(fresh);
      setItems(r.items);
      setPerShot(r.per_shot);
      setLastPoll(r.now);
    } catch (e) {
      toast(`Live scan failed: ${e instanceof Error ? e.message : e}`, "error");
    }
  }, [shot, types, toast]);

  useEffect(() => { seen.current = new Map(); pull(); }, [pull]);
  useEffect(() => {
    if (!auto) return;
    const id = window.setInterval(() => document.visibilityState === "visible" && pull(), 4000);
    return () => window.clearInterval(id);
  }, [auto, pull]);

  const freeze = async () => {
    const name = window.prompt("Freeze the current live shots (renders/shots) as a version you can pick from in the timeline.\nName, e.g. v07:", "v07");
    if (!name) return;
    try {
      const r = await api.freeze(name);
      toast(`Froze ${r.shots} shots as ${r.source}`);
      refreshSources();
    } catch (e) {
      toast(`Could not freeze: ${e instanceof Error ? e.message : e}`, "error");
    }
  };

  const toggle = (t: string) => {
    const n = new Set(types);
    if (n.has(t)) n.delete(t); else n.add(t);
    if (n.size) setTypes(n);
  };

  return (
    <div className="live-view">
      <div className="live-bar">
        <div className="seg" role="group" aria-label="What to show">
          {TYPES.map((t) => (
            <button key={t.id} className={types.has(t.id) ? "on" : ""} aria-pressed={types.has(t.id)} onClick={() => toggle(t.id)}>{t.label}</button>
          ))}
        </div>
        <label className="inline-field">Shot
          <select value={shot} onChange={(e) => setShot(e.target.value)}>
            <option value="">all shots</option>
            {(meta?.shots ?? []).map((s) => <option key={s.id} value={s.id}>{s.id}{perShot[s.id] ? ` (${perShot[s.id]})` : ""}</option>)}
          </select>
        </label>
        <label className="check"><input type="checkbox" checked={auto} onChange={(e) => setAuto(e.target.checked)} /> Follow (every 4 s)</label>
        <span className="muted">{lastPoll ? `checked ${ago(lastPoll)}` : ""}</span>
        <span className="pane-spacer" />
        <button onClick={freeze} title="Clone renders/shots into renders/studio/frozen/<name> so this cycle's shots survive the next one">Freeze live shots…</button>
      </div>
      {items.length === 0 && <p className="empty-state">Nothing matches. Agents write previews, contact sheets and QA stills here as they render.</p>}
      <ul className="live-grid">
        {items.map((it) => <Card key={it.path} it={it} fresh={freshSet.has(it.path)} onOpen={() => setOpen(it)} />)}
      </ul>
      {open && (
        <div className="lightbox" role="dialog" aria-modal="true" aria-label={open.name} onClick={() => setOpen(null)}
          onKeyDown={(e) => e.key === "Escape" && setOpen(null)} tabIndex={-1}>
          <div className="lightbox-inner" onClick={(e) => e.stopPropagation()}>
            {open.type === "preview" ? <video src={open.url} controls autoPlay loop playsInline />
              : <img src={open.type === "part" ? open.thumb : open.url} alt={open.name} />}
            <div className="lightbox-meta">
              <b>{open.name}</b> <span className="muted">{open.path} · {bytes(open.size)} · {ago(open.mtime)}</span>
              <span className="pane-spacer" />
              <button autoFocus onClick={() => setOpen(null)}>Close</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
