/** Keeps 1-4 <video> elements on the same film frame. One pane carries the audio and the clock; the others are
 *  muted and nudged back into line (playbackRate trims small drift, a hard seek fixes large drift). Pausing parks
 *  every video on the same frame; stepping moves all of them one frame. Frame numbers come from
 *  requestVideoFrameCallback's mediaTime, so a pause or step is frame-accurate, not timeupdate-accurate.
 *
 *  The clock is film time. Each pane has a range of film frames [f0, f1) its media covers: a full cut starts at film
 *  frame 0, a partial cut (built from s05 to s06) at s05's first frame, which is its own time 0. A pane whose cut
 *  doesn't cover the playhead is paused (parked on its first frame when the playhead is before it) and says so. The
 *  clock comes from the audio master while its cut covers the playhead, else from any pane that does. */
import { useSyncExternalStore } from "react";
import { FPS, frameAt, timeOfFrame } from "./util";

type VideoWithRVFC = HTMLVideoElement & {
  requestVideoFrameCallback?: (cb: (now: number, md: { mediaTime: number }) => void) => number;
  cancelVideoFrameCallback?: (h: number) => void;
};

interface Range { f0: number; f1: number | null }   // film frames, f1 exclusive (null: to the end of the media)
const FULL: Range = { f0: 0, f1: null };

export interface SyncState { time: number; frame: number; playing: boolean; duration: number; rate: number }

export class SyncGroup {
  private videos: (VideoWithRVFC | null)[] = [];
  private ranges: Range[] = [];
  private handles: number[] = [];
  private listeners = new Set<() => void>();
  private raf = 0;
  private driver = -1;
  master = 0;
  loop: [number, number] | null = null;
  /** The film's length in seconds: seeks may go anywhere in it, even where no open cut has pictures. */
  filmLength = 0;
  state: SyncState = { time: 0, frame: 0, playing: false, duration: 0, rate: 1 };

  subscribe = (fn: () => void) => {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  };
  getState = () => this.state;

  private emit(patch: Partial<SyncState>) {
    const s = { ...this.state, ...patch };
    if (s.time === this.state.time && s.frame === this.state.frame && s.playing === this.state.playing
      && s.duration === this.state.duration && s.rate === this.state.rate) return;
    this.state = s;
    this.listeners.forEach((l) => l());
  }

  register(i: number, el: HTMLVideoElement | null) {
    const prev = this.videos[i];
    if (prev === el) return;
    if (prev && this.handles[i]) prev.cancelVideoFrameCallback?.(this.handles[i]);
    this.handles[i] = 0;
    this.videos[i] = el as VideoWithRVFC | null;
    if (el) {
      const v = el as VideoWithRVFC;
      v.playbackRate = this.state.rate;
      const onMeta = () => {
        if (this.videos[i] !== v) return;
        this.updateDuration();
        // a newly loaded pane joins on the group's current frame
        this.put(i, timeOfFrame(this.state.frame));
        if (this.state.playing && this.covers(i, this.state.frame)) v.play().catch(() => undefined);
        this.applyAudio();
      };
      v.addEventListener("loadedmetadata", onMeta);
      if (v.readyState >= 1) onMeta();
      this.watch(i, v);
    }
    this.applyAudio();
  }

  /** The film frames pane i's media covers: [f0, f1), f1 null meaning to the end of its media. */
  setRange(i: number, f0: number, f1: number | null) {
    const r = this.ranges[i];
    if (r && r.f0 === f0 && r.f1 === f1) return;
    this.ranges[i] = { f0, f1 };
    this.updateDuration();
    if (this.videos[i]?.readyState) this.put(i, timeOfFrame(this.state.frame));
    this.applyAudio();
  }

  setCount(n: number) {
    this.videos.length = Math.min(this.videos.length, n);
    this.ranges.length = Math.min(this.ranges.length, n);
    if (this.master >= n) this.setMaster(0);
  }

  private range(i: number): Range {
    return this.ranges[i] ?? FULL;
  }

  /** The film frame just past pane i's last: its range's end, or for a full cut the end of its media. */
  private endOf(i: number): number {
    const v = this.videos[i];
    const r = this.range(i);
    return r.f1 ?? (v && Number.isFinite(v.duration) ? r.f0 + Math.round(v.duration * FPS) : Infinity);
  }

  /** Does pane i have film frame k? */
  private covers(i: number, k: number): boolean {
    return k >= this.range(i).f0 && k < this.endOf(i);
  }

  /** The pane that drives the clock at film frame k: the audio master if its cut has k, else the first pane that
   *  does (-1: k is outside every open cut). A pane that has played to its end can't drive. */
  private clock(k = this.state.frame): number {
    const ok = (i: number) => !!this.videos[i] && !this.videos[i]!.ended && this.covers(i, k);
    return ok(this.master) ? this.master : this.videos.findIndex((_, i) => ok(i));
  }

  /** Show film time t in pane i: its own time t - f0 when its cut covers t; otherwise pause it, parked on its first
   *  frame when t comes before the cut so it is ready when the playhead gets there. */
  private put(i: number, t: number) {
    const v = this.videos[i];
    if (!v) return;
    const k = frameAt(t);
    const f0 = this.range(i).f0;
    if (this.covers(i, k)) {
      v.currentTime = t - f0 / FPS;
      return;
    }
    v.pause();
    if (k < f0) v.currentTime = timeOfFrame(0);
  }

  private updateDuration() {
    const ends = this.videos.map((v, i) => (v && Number.isFinite(v.duration) ? this.range(i).f0 / FPS + v.duration : 0));
    this.emit({ duration: Math.max(0, ...ends) });
  }

  /** Last film time a seek may reach. */
  private end(): number {
    return Math.max(this.filmLength, this.state.duration) || Infinity;
  }

  setMaster(i: number) {
    this.master = i;
    this.applyAudio();
  }

  muted = false;
  setMuted(m: boolean) {
    this.muted = m;
    this.applyAudio();
  }

  /** Sound comes from the pane that drives the clock: the master, unless the playhead is outside its cut. */
  private applyAudio() {
    const c = this.clock();
    this.driver = c;
    this.videos.forEach((v, i) => v && (v.muted = i !== c || this.muted));
  }

  /** Report the driving pane's presented frames (rVFC), in film time; timeupdate where rVFC is unsupported. */
  private watch(i: number, v: VideoWithRVFC) {
    const report = (local: number, bias: number) => {
      if (this.videos[i] !== v || this.clock() !== i) return;
      const t = local + this.range(i).f0 / FPS;
      this.emit({ time: t, frame: frameAt(t + bias) });
    };
    if (v.requestVideoFrameCallback) {
      const cb = (_now: number, md: { mediaTime: number }) => {
        if (this.videos[i] !== v) return;
        report(md.mediaTime, 0.5 / FPS);
        this.handles[i] = v.requestVideoFrameCallback!(cb);
      };
      this.handles[i] = v.requestVideoFrameCallback(cb);
    } else {
      v.addEventListener("timeupdate", () => report(v.currentTime, 0));
    }
  }

  private tick = () => {
    if (!this.state.playing) return;
    let c = this.clock();
    if (c < 0) {
      // the driving pane has run out (a partial cut ends before the others): go on from its end if another pane has
      // that frame (from its end, not the last frame reported: frames can be dropped)
      const end = this.videos[this.driver] ? this.endOf(this.driver) : 0;
      const next = Math.max(this.state.frame + 1, Number.isFinite(end) ? end : 0);
      if (this.clock(next) < 0) {
        this.pause();
        return;
      }
      this.emit({ time: next / FPS, frame: next });
      c = this.clock();
    }
    if (c !== this.driver) this.applyAudio();
    const m = this.videos[c]!;
    if (m.paused) m.play().catch(() => undefined);   // a pane that just took over may have been parked
    let t = m.currentTime + this.range(c).f0 / FPS;
    if (this.loop && (t >= this.loop[1] || t < this.loop[0] - 0.5)) {
      this.seekTime(this.loop[0]);
      t = this.state.time;
    }
    const k = frameAt(t);
    this.videos.forEach((v, i) => {
      if (!v || i === c) return;
      if (!this.covers(i, k)) {
        if (!v.paused) v.pause();
        return;
      }
      if (v.readyState < 2) return;
      const target = t - this.range(i).f0 / FPS;
      const drift = v.currentTime - target;
      if (Math.abs(drift) > 0.3) {
        v.currentTime = target + 0.05;
        v.playbackRate = this.state.rate;
      } else if (Math.abs(drift) > 1 / FPS / 2) {
        v.playbackRate = this.state.rate * (1 - Math.max(-0.12, Math.min(0.12, drift * 1.5)));
      } else if (v.playbackRate !== this.state.rate) {
        v.playbackRate = this.state.rate;
      }
      if (v.paused && !m.paused) v.play().catch(() => undefined);
    });
    this.raf = requestAnimationFrame(this.tick);
  };

  play() {
    const k = this.state.frame;
    const c = this.clock(k);
    const m = c >= 0 ? this.videos[c] : null;
    const atEnd = !m || m.currentTime >= (m.duration || Infinity) - 0.05;
    if (atEnd && !this.videos.some((v, i) => v && i !== c && this.covers(i, k + 1))) {
      // at the end of everything, or where no open cut has pictures: start at the next cut ahead, else the first
      const starts = this.videos.flatMap((v, i) => (v ? [this.range(i).f0] : []));
      if (!starts.length) return;
      const ahead = starts.filter((f) => f > k);
      this.seekFrame(!m && ahead.length ? Math.min(...ahead) : Math.min(...starts));
    }
    const f = this.state.frame;
    this.applyAudio();
    this.videos.forEach((v, i) => {
      if (!v || !this.covers(i, f)) return;
      v.playbackRate = this.state.rate;
      v.play().catch(() => undefined);
    });
    this.emit({ playing: true });
    cancelAnimationFrame(this.raf);
    this.raf = requestAnimationFrame(this.tick);
  }

  pause() {
    cancelAnimationFrame(this.raf);
    const c = this.clock();
    const m = c >= 0 ? this.videos[c] : null;
    this.videos.forEach((v) => v?.pause());
    this.emit({ playing: false });
    this.seekFrame(m ? frameAt(m.currentTime + this.range(c).f0 / FPS) : this.state.frame);
  }

  toggle() {
    if (this.state.playing) this.pause();
    else this.play();
  }

  seekTime(t: number) {
    t = Math.max(0, Math.min(t, this.end() - 0.5 / FPS));
    this.videos.forEach((_, i) => this.put(i, t));
    this.emit({ time: t, frame: frameAt(t) });
    this.applyAudio();
  }

  /** Park every video on film frame k (its midpoint, so rounding never shows the neighbour). */
  seekFrame(k: number) {
    const last = Math.max(0, Math.floor(this.end() * FPS) - 1);
    k = Math.max(0, Math.min(k, last));
    const t = timeOfFrame(k);
    this.videos.forEach((_, i) => this.put(i, t));
    this.emit({ time: k / FPS, frame: k });
    this.applyAudio();
  }

  step(d: number) {
    if (this.state.playing) this.pause();
    this.seekFrame(this.state.frame + d);
  }

  setRate(r: number) {
    this.videos.forEach((v) => v && (v.playbackRate = r));
    this.emit({ rate: r });
  }

  getVideo(i: number): HTMLVideoElement | null {
    return this.videos[i] ?? null;
  }

  dispose() {
    cancelAnimationFrame(this.raf);
    this.videos.forEach((v, i) => {
      if (!v) return;
      v.pause();
      if (this.handles[i]) v.cancelVideoFrameCallback?.(this.handles[i]);
    });
  }
}

export function useSync(g: SyncGroup): SyncState {
  return useSyncExternalStore(g.subscribe, g.getState);
}
