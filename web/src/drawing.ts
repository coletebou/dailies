/** Vector drawings on video frames. Coordinates are normalised 0-1 to the video picture (not the letterboxed element),
 *  sizes are fractions of the picture height, so a drawing renders identically on a phone, a laptop and the 1080p
 *  composite PNG. Pen strokes use perfect-freehand (MIT) for pressure-shaped outlines. */
import { getStroke } from "perfect-freehand";
import type { Drawing, Shape } from "./types";

export interface Rect { x: number; y: number; w: number; h: number }

/** The rectangle the video picture occupies inside its element (object-fit: contain). */
export function contentRect(elW: number, elH: number, vidW: number, vidH: number): Rect {
  if (!vidW || !vidH || !elW || !elH) return { x: 0, y: 0, w: elW, h: elH };
  const a = vidW / vidH;
  if (elW / elH > a) {
    const w = elH * a;
    return { x: (elW - w) / 2, y: 0, w, h: elH };
  }
  const h = elW / a;
  return { x: 0, y: (elH - h) / 2, w: elW, h };
}

const P = (p: number[], r: Rect): [number, number] => [r.x + p[0] * r.w, r.y + p[1] * r.h];

function strokeOutline(points: number[][], size: number, r: Rect): Path2D {
  const pts = points.map((p) => [r.x + p[0] * r.w, r.y + p[1] * r.h, p[2] ?? 0.5]);
  const hasPressure = points.some((p) => p.length > 2 && p[2] !== 0.5);
  const outline = getStroke(pts, {
    size: Math.max(1, size * r.h), thinning: 0.55, smoothing: 0.5, streamline: 0.45,
    simulatePressure: !hasPressure, last: true,
  });
  const path = new Path2D();
  if (!outline.length) return path;
  path.moveTo(outline[0][0], outline[0][1]);
  for (let i = 1; i < outline.length; i++) {
    const [x0, y0] = outline[i];
    const [x1, y1] = outline[(i + 1) % outline.length];
    path.quadraticCurveTo(x0, y0, (x0 + x1) / 2, (y0 + y1) / 2);
  }
  path.closePath();
  return path;
}

export function drawShape(ctx: CanvasRenderingContext2D, s: Shape, r: Rect) {
  ctx.save();
  ctx.shadowColor = "rgba(0,0,0,0.55)";
  ctx.shadowBlur = Math.max(2, r.h * 0.004);
  ctx.strokeStyle = s.color;
  ctx.fillStyle = s.color;
  const lw = Math.max(1.5, s.size * r.h);
  ctx.lineWidth = lw;
  ctx.lineCap = "round";
  ctx.lineJoin = "round";
  if (s.type === "pen" && s.points?.length) {
    ctx.fill(strokeOutline(s.points, s.size, r));
  } else if ((s.type === "arrow" || s.type === "rect" || s.type === "ellipse") && s.a && s.b) {
    const [x0, y0] = P(s.a, r);
    const [x1, y1] = P(s.b, r);
    ctx.beginPath();
    if (s.type === "rect") {
      ctx.rect(Math.min(x0, x1), Math.min(y0, y1), Math.abs(x1 - x0), Math.abs(y1 - y0));
    } else if (s.type === "ellipse") {
      ctx.ellipse((x0 + x1) / 2, (y0 + y1) / 2, Math.abs(x1 - x0) / 2, Math.abs(y1 - y0) / 2, 0, 0, Math.PI * 2);
    } else {
      const ang = Math.atan2(y1 - y0, x1 - x0);
      const head = Math.max(lw * 3.2, Math.min(r.h * 0.05, Math.hypot(x1 - x0, y1 - y0) * 0.4));
      ctx.moveTo(x0, y0);
      ctx.lineTo(x1, y1);
      ctx.moveTo(x1 - head * Math.cos(ang - 0.45), y1 - head * Math.sin(ang - 0.45));
      ctx.lineTo(x1, y1);
      ctx.lineTo(x1 - head * Math.cos(ang + 0.45), y1 - head * Math.sin(ang + 0.45));
    }
    ctx.stroke();
  } else if (s.type === "text" && s.a && s.text) {
    const [x, y] = P(s.a, r);
    const px = Math.max(10, s.size * r.h);
    ctx.font = `700 ${px}px Archivo, system-ui, sans-serif`;
    ctx.textBaseline = "top";
    ctx.shadowBlur = 0;
    ctx.lineWidth = Math.max(2, px * 0.16);
    ctx.strokeStyle = "rgba(10,9,10,0.85)";
    ctx.strokeText(s.text, x, y);
    ctx.fillText(s.text, x, y);
  }
  ctx.restore();
}

export function drawAll(ctx: CanvasRenderingContext2D, shapes: Shape[], r: Rect) {
  for (const s of shapes) drawShape(ctx, s, r);
}

/** Size a canvas to its CSS box at device resolution and return a context in CSS pixels. */
export function fitCanvas(c: HTMLCanvasElement): CanvasRenderingContext2D | null {
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  const w = c.clientWidth, h = c.clientHeight;
  if (c.width !== Math.round(w * dpr) || c.height !== Math.round(h * dpr)) {
    c.width = Math.round(w * dpr);
    c.height = Math.round(h * dpr);
  }
  const ctx = c.getContext("2d");
  if (!ctx) return null;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);
  return ctx;
}

/** The paused video frame with the drawing composited, at the video's own resolution, as a PNG. */
export async function compositePng(video: HTMLVideoElement, drawing: Drawing): Promise<Blob> {
  const w = video.videoWidth, h = video.videoHeight;
  const c = document.createElement("canvas");
  c.width = w;
  c.height = h;
  const ctx = c.getContext("2d");
  if (!ctx) throw new Error("no 2d context");
  ctx.drawImage(video, 0, 0, w, h);
  drawAll(ctx, drawing.shapes, { x: 0, y: 0, w, h });
  return await new Promise<Blob>((res, rej) => c.toBlob((b) => (b ? res(b) : rej(new Error("toBlob failed"))), "image/png"));
}

export const PALETTE = ["#ef541e", "#e9e4db", "#ffffff", "#ff00ff", "#2bd9c8", "#0a090a"];
export const SIZES = { S: 0.004, M: 0.007, L: 0.013 } as const;
