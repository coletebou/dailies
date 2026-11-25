import { forwardRef, useCallback, useEffect, useImperativeHandle, useRef, useState } from "react";
import type { Meta, ShotOption } from "../types";
import { FPS, tc } from "../util";

export interface PlayerHandle { seek: (t: number) => void; play: () => void; pause: () => void }

/** Plays the edit in the browser before it is built: the mix as the clock, and each shot's chosen 720p proxy swapped
 *  in at its EDL frame through two double-buffered <video> elements (the next shot is preloaded a second early). */
export const TimelinePlayer = forwardRef<PlayerHandle, {
  meta: Meta; pick: (sid: string) => ShotOption | undefined; onTime: (t: number) => void; loop?: [number, number] | null;
}>(function TimelinePlayer({ meta, pick, onTime, loop }, ref) {
  const audio = useRef<HTMLAudioElement>(null);
  const bufs = [useRef<HTMLVideoElement>(null), useRef<HTMLVideoElement>(null)];
  const loaded = useRef<[string | null, string | null]>([null, null]);
  const [active, setActive] = useState(0);
  const activeRef = useRef(0);
  const [playing, setPlaying] = useState(false);
  const [t, setT] = useState(0);
  const [audioOk, setAudioOk] = useState(true);
  const clock = useRef({ base: 0, at: 0 });
  const raf = useRef(0);
  const tRef = useRef(0);

  const shotAt = useCallback((time: number) => {
    const f = Math.floor(time * FPS + 1e-6);
    return meta.shots.find((s) => f >= s.f0 && f < s.f1) ?? meta.shots[meta.shots.length - 1];
  }, [meta]);

  const keyOf = (o?: ShotOption) => (o ? o.proxy : null);

  const load = (i: number, sid: string) => {
    const o = pick(sid);
    const v = bufs[i].current;
    if (!v || !o) return;
    const key = keyOf(o);
    if (loaded.current[i] === `${sid}|${key}`) return;
    loaded.current[i] = `${sid}|${key}`;
    v.poster = o.thumb;
    v.src = o.proxy;
    v.load();
  };

  const now = () => (audio.current && audioOk && !audio.current.error ? audio.current.currentTime
    : clock.current.base + (playing ? (performance.now() - clock.current.at) / 1000 : 0));

  const show = (time: number, run: boolean) => {
    const s = shotAt(time);
    const want = `${s.id}|${keyOf(pick(s.id))}`;
    let a = activeRef.current;
    if (loaded.current[a] !== want) {
      const other = 1 - a;
      if (loaded.current[other] !== want) load(other, s.id);
      a = other;
      activeRef.current = a;
      setActive(a);
    }
    const v = bufs[a].current;
    if (v) {
      const local = time - s.f0 / FPS;
      if (Math.abs(v.currentTime - local) > (run ? 0.15 : 0.01)) {
        try { v.currentTime = Math.max(0, local + (run ? 0 : 0.5 / FPS)); } catch { /* not seekable yet */ }
      }
      if (run && v.paused) v.play().catch(() => undefined);
      if (!run && !v.paused) v.pause();
    }
    const idx = meta.shots.indexOf(s);
    const next = meta.shots[idx + 1];
    if (run && next && time > next.f0 / FPS - 1.2) load(1 - a, next.id);
    bufs[1 - a].current?.pause();
  };

  const tick = () => {
    let time = now();
    if (loop && (time >= loop[1] || time < loop[0] - 0.5)) {
      time = loop[0];
      seekTo(time, true);
    }
    if (time >= meta.duration) {
      pause();
      return;
    }
    show(time, true);
    tRef.current = time;
    setT(time);
    onTime(time);
    raf.current = requestAnimationFrame(tick);
  };

  const seekTo = (time: number, run = playing) => {
    time = Math.max(0, Math.min(meta.duration - 1 / FPS, time));
    if (audio.current) audio.current.currentTime = time;
    clock.current = { base: time, at: performance.now() };
    tRef.current = time;
    setT(time);
    onTime(time);
    show(time, run);
  };

  const play = () => {
    if (audio.current && audioOk) audio.current.play().catch(() => setAudioOk(false));
    clock.current = { base: tRef.current, at: performance.now() };
    setPlaying(true);
  };
  const pause = () => {
    cancelAnimationFrame(raf.current);
    audio.current?.pause();
    clock.current = { base: tRef.current, at: performance.now() };
    setPlaying(false);
    bufs.forEach((b) => b.current?.pause());
  };

  useEffect(() => {
    if (playing) raf.current = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf.current);
  }); // re-arm each render so tick sees fresh props

  useEffect(() => {
    show(tRef.current, false);
  }, [pick]); // eslint-disable-line react-hooks/exhaustive-deps

  useImperativeHandle(ref, () => ({ seek: (x) => seekTo(x), play, pause }));

  const s = shotAt(t);
  const o = pick(s.id);
  return (
    <div className="tl-player">
      <div className="tl-screen" onClick={() => (playing ? pause() : play())}>
        {bufs.map((b, i) => (
          <video key={i} ref={b} muted playsInline preload="auto" className={i === active ? "on" : ""}
            onError={() => { loaded.current[i] = null; }} />
        ))}
        {o && !o.proxy_ready && <span className="chip tl-wait">Making a 720p proxy of {s.id} from {o.source}…</span>}
      </div>
      <audio ref={audio} src="/api/audio/mix.m4a" preload="auto" onError={() => setAudioOk(false)} />
      <div className="tl-controls">
        <button className="play" onClick={() => (playing ? pause() : play())} aria-label={playing ? "Pause" : "Play"}>{playing ? "❚❚" : "▶"}</button>
        <span className="timecode">{tc(t)}</span>
        <span className="shotnow"><b>{s.id}</b> {o ? <span className="muted">from {o.source} · {o.tier}</span> : <span className="warn">no source</span>}</span>
        {!audioOk && <span className="chip" title="The AAC of music/mix_master.wav is still being made">silent preview</span>}
      </div>
    </div>
  );
});
