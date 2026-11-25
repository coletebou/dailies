import { memo, useCallback, useRef } from "react";
import type { Meta, Tag } from "../types";
import { mmss, tc, TAGS } from "../util";

export interface Marker {
  t: number; kind: "note" | "critic"; tag?: Tag; label: string; id: string; version?: string;
}

/** Fine scrubber: film-length track with shot boundaries, reviewer notes (tag glyph colours) and the critic's weakest
 *  moments (magenta, the film's one "missing texture" colour), plus the playhead. Drag anywhere to scrub. */
export function Scrubber({ meta, time, markers, onSeek, onScrubStart, onScrubEnd }: {
  meta: Meta; time: number; markers: Marker[]; onSeek: (t: number) => void;
  onScrubStart?: () => void; onScrubEnd?: () => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const dur = meta.duration;
  const tAt = useCallback((x: number) => {
    const b = ref.current!.getBoundingClientRect();
    return Math.max(0, Math.min(dur, ((x - b.left) / b.width) * dur));
  }, [dur]);
  const raf = useRef(0);
  const pending = useRef<number | null>(null);
  const seekSoon = (t: number) => {
    pending.current = t;
    if (!raf.current) {
      raf.current = requestAnimationFrame(() => {
        raf.current = 0;
        if (pending.current !== null) onSeek(pending.current);
      });
    }
  };
  return (
    <div className="scrubber" ref={ref} role="slider" aria-label="Playhead" aria-valuemin={0} aria-valuemax={dur}
      aria-valuenow={time} aria-valuetext={tc(time)} tabIndex={0}
      onPointerDown={(e) => {
        if ((e.target as HTMLElement).closest(".marker")) return;
        (e.currentTarget as Element).setPointerCapture(e.pointerId);
        onScrubStart?.();
        seekSoon(tAt(e.clientX));
      }}
      onPointerMove={(e) => {
        if (e.buttons & 1 && (e.currentTarget as Element).hasPointerCapture(e.pointerId)) seekSoon(tAt(e.clientX));
      }}
      onPointerUp={() => onScrubEnd?.()}>
      <div className="scrub-track">
        {meta.shots.map((s) => (
          <div key={s.id} className="scrub-shot" style={{ left: `${(s.t0 / dur) * 100}%`, width: `${((s.t1 - s.t0) / dur) * 100}%` }} />
        ))}
        <div className="scrub-fill" style={{ width: `${(time / dur) * 100}%` }} />
      </div>
      {markers.map((m) => (
        <button key={m.id} className={`marker ${m.kind} ${m.tag ? "tag-" + m.tag : ""}`}
          style={{ left: `${(m.t / dur) * 100}%` }} title={`${mmss(m.t)} ${m.label}`}
          aria-label={`${m.kind === "critic" ? "Critic" : "Note"} at ${mmss(m.t)}: ${m.label}`}
          onClick={(e) => {
            e.stopPropagation();
            onSeek(m.t);
          }}>
          {m.kind === "critic" ? "◇" : TAGS.find((t) => t.id === m.tag)?.glyph}
        </button>
      ))}
      <div className="playhead" style={{ left: `${(time / dur) * 100}%` }} />
    </div>
  );
}

/** The film as a strip: one block per EDL shot, width proportional to its length, labelled with its lyric. */
export const ShotStrip = memo(function ShotStrip({ meta, thumbs, current, counts, onPick, loopShot }: {
  meta: Meta; thumbs: Record<string, string | undefined>; current: string | undefined;
  counts: Record<string, { notes: number; critic: number }>; onPick: (sid: string) => void; loopShot?: string | null;
}) {
  return (
    <div className="shotstrip" role="list" aria-label="Shots">
      {meta.shots.map((s) => {
        const c = counts[s.id];
        return (
          <button key={s.id} role="listitem" className={`shot ${current === s.id ? "on" : ""} ${loopShot === s.id ? "looping" : ""}`}
            style={{ flexGrow: s.frames, flexBasis: 0 }} onClick={() => onPick(s.id)}
            title={`${s.id} · ${mmss(s.t0)}–${mmss(s.t1)}\n${s.lyric}\n${s.shotlist.picture ?? ""}`}>
            {thumbs[s.id] && <img src={thumbs[s.id]} alt="" loading="lazy" draggable={false} />}
            <span className="shot-id">{s.id}</span>
            {s.lyric && <span className="shot-lyric">{s.lyric}</span>}
            {c && (c.notes > 0 || c.critic > 0) && (
              <span className="shot-dots" aria-label={`${c.notes} notes, ${c.critic} critic moments`}>
                {Array.from({ length: Math.min(c.notes, 4) }, (_, k) => <i key={`n${k}`} className="n" />)}
                {Array.from({ length: Math.min(c.critic, 4) }, (_, k) => <i key={`c${k}`} className="c" />)}
              </span>
            )}
          </button>
        );
      })}
    </div>
  );
});

/** The sung line at the playhead, word by word, in the film's italic serif voice. */
export function LyricNow({ meta, time }: { meta: Meta; time: number }) {
  const line = meta.lyrics.find((l) => time >= l.t0 - 0.2 && time <= l.t1 + 0.4);
  if (!line) return <div className="lyric-now empty" aria-hidden>&nbsp;</div>;
  return (
    <div className="lyric-now" aria-live="off">
      {line.words.length ? line.words.map((w, i) => (
        <span key={i} className={time >= w.t0 && time < w.t1 + 0.05 ? "w on" : time >= w.t1 ? "w past" : "w"}>{w.w} </span>
      )) : line.text}
    </div>
  );
}
