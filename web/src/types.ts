export interface Word { w: string; t0: number; t1: number }
export interface LyricLine { section: string; text: string; t0: number; t1: number; words: Word[] }

export interface Shot {
  id: string; f0: number; f1: number; frames: number; t0: number; t1: number;
  lyric: string; section: string; lyrics: string[];
  shotlist: { lyric?: string; picture?: string };
}

export interface Meta { fps: number; total_frames: number; duration: number; shots: Shot[]; lyrics: LyricLine[] }

export interface Probe { duration: number; width: number; height: number; frames: number | null; audio: boolean }

export interface Cut {
  id: string; path: string; kind: "snapshot" | "preview" | "studio"; name: string; url: string;
  size: number; mtime: number; ready: boolean; probe: Probe | null;
  /** film frames [film_f0, film_f1) this file holds; its own time 0 is film frame film_f0 (film_f1 null: to its end) */
  film_f0: number; film_f1: number | null;
  proxy_ready?: boolean; proxy_url?: string; meta?: StudioCutMeta;
}

export interface StudioCutMeta {
  name: string; built: string; timeline: string | null; from: string | null; to: string | null;
  film_f0?: number; film_f1?: number;
  shots: Record<string, { source: string; tier: string; path: string }>;
}

export interface WeakMoment { t: number; timestamp: string; problem: string; fix: string }

export interface Critique {
  file: string; label: string; key: string | null; model: string; model_short: string;
  scores: Record<string, number>; justification: Record<string, string>;
  avg: number; min: number; overall: string; when: string; weakest: WeakMoment[];
}

export interface Version {
  id: string; label: string; kind: "cut" | "studio"; cut: Cut; alternates: Cut[]; pending: boolean;
  critiques: Critique[]; sources: string[]; studio: StudioCutMeta | null;
}

export interface SourceInfo { id: string; label: string; kind: "snapshot" | "frozen" | "live"; version: string | null; count: number }

export interface ShotOption {
  source: string; version: string | null; tier: string; name: string; mtime: number; size: number;
  thumb: string; thumb_ready: boolean; proxy: string; proxy_ready: boolean;
}

export interface Sources { sources: SourceInfo[]; shots: Record<string, ShotOption[]> }

export type Tag = "problem" | "like" | "idea";

export type ShapeType = "pen" | "arrow" | "rect" | "ellipse" | "text";
export interface Shape {
  type: ShapeType; color: string; size: number;
  points?: number[][]; a?: number[]; b?: number[]; text?: string;
}
export interface Drawing { aspect: number; shapes: Shape[] }

export interface Annotation {
  id: string; version: string; cut: string | null; time: number; frame: number; shot: string | null;
  tag: Tag; text: string; status: "open" | "done"; author: string; created: string; updated: string;
  drawing: Drawing | null; frame_png: string | null;
}

export interface Build {
  id: string; name: string; status: "queued" | "running" | "done" | "failed" | "cancelled" | "interrupted";
  created: string; started: string | null; finished: string | null; user: string; timeline: string | null;
  from: string | null; to: string | null; shots: string[];
  selection: Record<string, { source: string; tier: string; path: string }>;
  frames_total: number; frames_done: number; current_shot: string | null; out: string; error: string | null;
  eta_s: number | null; elapsed_s?: number; log?: string[];
}

export interface TimelineSummary { name: string; updated: string; note: string; shots: Record<string, string> }

export interface LiveItem {
  path: string; name: string; type: "preview" | "sheet" | "qa" | "part"; sid: string | null; version: string | null;
  mtime: number; size: number; url: string; range?: number[] | null; temp?: boolean; thumb?: string;
}
