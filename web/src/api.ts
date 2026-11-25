import type { Annotation, Build, Drawing, LiveItem, Meta, Sources, Tag, TimelineSummary, Version } from "./types";

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function req<T>(method: string, url: string, body?: unknown, raw?: Blob): Promise<T> {
  const headers: Record<string, string> = {};
  let payload: BodyInit | undefined;
  if (method !== "GET") headers["X-Dailies"] = "1";
  if (raw) {
    headers["Content-Type"] = raw.type || "application/octet-stream";
    payload = raw;
  } else if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    payload = JSON.stringify(body);
  }
  const r = await fetch(url, { method, headers, body: payload, credentials: "same-origin" });
  if (!r.ok) {
    let msg = `${r.status} ${r.statusText}`;
    try {
      const j = await r.json();
      if (j?.detail) msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail);
    } catch {
      /* not json */
    }
    throw new ApiError(r.status, msg);
  }
  return (await r.json()) as T;
}

export const api = {
  meta: () => req<Meta>("GET", "/api/meta"),
  versions: () => req<{ versions: Version[] }>("GET", "/api/versions").then((r) => r.versions),
  sources: () => req<Sources>("GET", "/api/sources"),
  annotations: () => req<{ annotations: Annotation[] }>("GET", "/api/annotations").then((r) => r.annotations),
  createAnnotation: (a: {
    version: string; cut?: string | null; time: number; frame: number; shot?: string | null; tag: Tag;
    text: string; drawing?: Drawing | null;
  }) => req<Annotation>("POST", "/api/annotations", a),
  patchAnnotation: (id: string, patch: Partial<Pick<Annotation, "text" | "tag" | "status" | "time" | "frame">>) =>
    req<Annotation>("PATCH", `/api/annotations/${id}`, patch),
  deleteAnnotation: (id: string) => req<{ deleted: string }>("DELETE", `/api/annotations/${id}`),
  putFrame: (id: string, png: Blob) => req<Annotation>("PUT", `/api/annotations/${id}/frame`, undefined, png),
  timelines: () => req<{ timelines: TimelineSummary[] }>("GET", "/api/timelines").then((r) => r.timelines),
  saveTimeline: (name: string, shots: Record<string, string>, note = "") =>
    req<unknown>("PUT", `/api/timelines/${encodeURIComponent(name)}`, { shots, note }),
  deleteTimeline: (name: string) => req<unknown>("DELETE", `/api/timelines/${encodeURIComponent(name)}`),
  builds: () => req<{ builds: Build[] }>("GET", "/api/builds").then((r) => r.builds),
  build: (id: string) => req<Build>("GET", `/api/builds/${id}`),
  startBuild: (b: { name: string; shots: Record<string, string>; from?: string; to?: string; timeline?: string }) =>
    req<Build>("POST", "/api/builds", b),
  cancelBuild: (id: string) => req<Build>("POST", `/api/builds/${id}/cancel`),
  live: (q: { since?: number; shot?: string; types?: string; limit?: number }) => {
    const p = new URLSearchParams();
    Object.entries(q).forEach(([k, v]) => v !== undefined && v !== "" && p.set(k, String(v)));
    return req<{ now: number; total: number; items: LiveItem[]; per_shot: Record<string, number> }>("GET", `/api/live?${p}`);
  },
  freeze: (name: string) => req<{ name: string; source: string; shots: number }>("POST", "/api/live/freeze", { name }),
  ensureProxy: (source: string, sid: string) => req<{ ready: boolean }>("POST", "/api/proxies/ensure", { source, sid }),
  health: () => req<Record<string, unknown>>("GET", "/api/health"),
};
