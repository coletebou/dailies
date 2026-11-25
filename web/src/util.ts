import type { Annotation, Critique, Cut, Meta, Shot, Version } from "./types";

export const FPS = 30;

/** 89.0667 -> "01:29:02" (minutes:seconds:frames at 30 fps) */
export function tc(t: number, fps = FPS): string {
  if (!Number.isFinite(t) || t < 0) t = 0;
  const f = Math.floor(t * fps + 1e-6);
  const s = Math.floor(f / fps);
  const ff = f % fps;
  return `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}:${String(ff).padStart(2, "0")}`;
}

/** "01:29" style, for critic timestamps and compact labels */
export function mmss(t: number): string {
  const s = Math.max(0, Math.floor(t));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

export function frameAt(t: number, fps = FPS): number {
  return Math.floor(t * fps + 1e-6);
}

/** Seek target that lands squarely on frame k (its midpoint), immune to float rounding at the boundary. */
export function timeOfFrame(k: number, fps = FPS): number {
  return (k + 0.5) / fps;
}

/** Does this cut have film frame f? A partial cut (built from s05 to s06) holds only film frames [film_f0, film_f1),
 *  and its own time 0 is film frame film_f0. */
export function cutCovers(cut: Pick<Cut, "film_f0" | "film_f1">, f: number): boolean {
  return f >= cut.film_f0 && (cut.film_f1 === null || f < cut.film_f1);
}

/** /media URL of a project-relative path, each segment percent-encoded (file names may hold #, &, %, spaces). */
export function mediaUrl(rel: string): string {
  return "/media/" + rel.split("/").map(encodeURIComponent).join("/");
}

export function shotAtFrame(meta: Meta, f: number): Shot | undefined {
  const shots = meta.shots;
  for (const s of shots) if (f >= s.f0 && f < s.f1) return s;
  return f >= (shots.at(-1)?.f1 ?? 0) ? shots.at(-1) : shots[0];
}

export function ago(epochSeconds: number, now = Date.now() / 1000): string {
  const d = Math.max(0, now - epochSeconds);
  if (d < 60) return `${Math.floor(d)}s ago`;
  if (d < 3600) return `${Math.floor(d / 60)}m ago`;
  if (d < 86400) return `${Math.floor(d / 3600)}h ago`;
  return `${Math.floor(d / 86400)}d ago`;
}

export function bytes(n: number): string {
  if (n > 1e9) return `${(n / 1e9).toFixed(2)} GB`;
  if (n > 1e6) return `${(n / 1e6).toFixed(1)} MB`;
  return `${Math.round(n / 1e3)} kB`;
}

export function avgOf(c: Critique[], model: "flash" | "pro"): number | null {
  const x = c.filter((k) => k.model_short === model).sort((a, b) => (a.when < b.when ? 1 : -1))[0];
  return x ? x.avg : null;
}

/** Version identity ramp: newest versions read brightest; live renders are ember; studio cuts are paper. */
export function versionTone(id: string | null | undefined, versions: Version[]): string {
  if (!id) return "var(--ember)";
  if (id.startsWith("studio-")) return "var(--paper)";
  const keys = versions.filter((v) => v.kind === "cut").map((v) => v.id).sort().reverse();
  const i = keys.indexOf(id);
  const ramp = ["var(--cream)", "var(--grey-4)", "var(--grey-3)", "var(--ghost)", "var(--grey-2)"];
  return i < 0 ? "var(--ghost)" : ramp[Math.min(i, ramp.length - 1)];
}

export const TAGS: { id: Annotation["tag"]; label: string; glyph: string }[] = [
  { id: "problem", label: "Problem", glyph: "■" },
  { id: "like", label: "Like", glyph: "●" },
  { id: "idea", label: "Idea", glyph: "◆" },
];

export function clamp(x: number, a: number, b: number) {
  return Math.max(a, Math.min(b, x));
}

export function isRemote(): boolean {
  return !["localhost", "127.0.0.1"].includes(location.hostname);
}

export function lyricAt(meta: Meta, t: number) {
  return meta.lyrics.find((l) => t >= l.t0 - 0.15 && t <= l.t1 + 0.35);
}

/** Default critique rubric: criterion "1".."7" in the scores of notes/critiques/*.json. */
export const CRITERIA_SHORT = ["Pro look", "Ref vibe", "Sync", "Pacing", "Consistency", "Jokes", "Originality"];
export const CRITERIA = [
  "Looks professionally made",
  "Matches the reference's vibe",
  "Music–picture sync",
  "Pacing and energy arc",
  "Visual consistency",
  "Jokes land, memorable",
  "Originality",
];
