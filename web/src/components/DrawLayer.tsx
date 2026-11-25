import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { contentRect, drawAll, drawShape, fitCanvas, PALETTE, SIZES, type Rect } from "../drawing";
import type { Shape, ShapeType, Tag } from "../types";
import { TAGS } from "../util";

/** Read-only overlay of saved drawings (shown when playback or scrubbing is within ±2 frames of their frame). */
export function DrawingOverlay({ video, shapes, labels }: {
  video: HTMLVideoElement | null; shapes: Shape[]; labels: { text: string; tag: Tag }[];
}) {
  const ref = useRef<HTMLCanvasElement>(null);
  const [rect, setRect] = useState<Rect | null>(null);
  useLayoutEffect(() => {
    const c = ref.current;
    if (!c || !video) return;
    const draw = () => {
      const ctx = fitCanvas(c);
      if (!ctx) return;
      const r = contentRect(c.clientWidth, c.clientHeight, video.videoWidth, video.videoHeight);
      setRect(r);
      drawAll(ctx, shapes, r);
    };
    draw();
    const ro = new ResizeObserver(draw);
    ro.observe(c);
    return () => ro.disconnect();
  }, [video, shapes]);
  return (
    <>
      <canvas ref={ref} className="draw-canvas overlay" aria-hidden />
      {labels.length > 0 && rect && (
        <div className="overlay-labels" style={{ left: rect.x + 8, top: rect.y + 8 }}>
          {labels.map((l, i) => (
            <span key={i} className={`overlay-label tag-${l.tag}`}>
              {TAGS.find((t) => t.id === l.tag)?.glyph} {l.text || "drawing"}
            </span>
          ))}
        </div>
      )}
    </>
  );
}

const TOOLS: { id: ShapeType; label: string; key: string; icon: string }[] = [
  { id: "pen", label: "Pen", key: "p", icon: "✎" },
  { id: "arrow", label: "Arrow", key: "a", icon: "↗" },
  { id: "rect", label: "Rectangle", key: "r", icon: "▭" },
  { id: "ellipse", label: "Ellipse", key: "e", icon: "◯" },
  { id: "text", label: "Text", key: "t", icon: "T" },
];

export interface DrawResult { shapes: Shape[]; tag: Tag; text: string }

/** Interactive drawing surface over a paused video, with its own tool palette and save form. */
export function DrawLayer({ video, onSave, onCancel, saving }: {
  video: HTMLVideoElement; onSave: (r: DrawResult) => void; onCancel: () => void; saving: boolean;
}) {
  const ref = useRef<HTMLCanvasElement>(null);
  const [shapes, setShapes] = useState<Shape[]>([]);
  const [draft, setDraft] = useState<Shape | null>(null);
  const [tool, setTool] = useState<ShapeType>("pen");
  const [color, setColor] = useState(PALETTE[0]);
  const [size, setSize] = useState<keyof typeof SIZES>("M");
  const [tag, setTag] = useState<Tag>("problem");
  const [text, setText] = useState("");
  const [textAt, setTextAt] = useState<{ a: number[]; x: number; y: number } | null>(null);
  const [textVal, setTextVal] = useState("");
  const rectRef = useRef<Rect>({ x: 0, y: 0, w: 1, h: 1 });

  useLayoutEffect(() => {
    const c = ref.current;
    if (!c) return;
    const draw = () => {
      const ctx = fitCanvas(c);
      if (!ctx) return;
      const r = contentRect(c.clientWidth, c.clientHeight, video.videoWidth, video.videoHeight);
      rectRef.current = r;
      ctx.save();
      ctx.strokeStyle = "rgba(239,84,30,0.55)";
      ctx.setLineDash([4, 4]);
      ctx.strokeRect(r.x + 0.5, r.y + 0.5, r.w - 1, r.h - 1);
      ctx.restore();
      drawAll(ctx, shapes, r);
      if (draft) drawShape(ctx, draft, r);
    };
    draw();
    const ro = new ResizeObserver(draw);
    ro.observe(c);
    return () => ro.disconnect();
  }, [shapes, draft, video]);

  const norm = (e: React.PointerEvent): number[] => {
    const c = ref.current!;
    const b = c.getBoundingClientRect();
    const r = rectRef.current;
    const x = (e.clientX - b.left - r.x) / r.w;
    const y = (e.clientY - b.top - r.y) / r.h;
    const cl = (v: number) => Math.max(-0.02, Math.min(1.02, v));
    return [cl(x), cl(y)];
  };

  const down = (e: React.PointerEvent) => {
    if (e.button !== 0 || textAt) return;
    const p = norm(e);
    if (tool === "text") {
      e.preventDefault();          // keep the canvas mousedown from pulling focus away from the new label input
      const b = ref.current!.getBoundingClientRect();
      setTextAt({ a: p, x: e.clientX - b.left, y: e.clientY - b.top });
      setTextVal("");
      return;
    }
    (e.target as Element).setPointerCapture(e.pointerId);
    const sz = SIZES[size];
    const pressure = e.pointerType === "pen" ? e.pressure : 0.5;
    setDraft(tool === "pen" ? { type: "pen", color, size: sz * 1.4, points: [[...p, pressure]] }
      : { type: tool, color, size: sz, a: p, b: p });
  };
  const move = (e: React.PointerEvent) => {
    if (!draft) return;
    const p = norm(e);
    if (draft.type === "pen") {
      const events = (e.nativeEvent as PointerEvent).getCoalescedEvents?.() ?? [];
      const extra = events.length > 1 ? events.map((ev) => {
        const b = ref.current!.getBoundingClientRect();
        const r = rectRef.current;
        return [(ev.clientX - b.left - r.x) / r.w, (ev.clientY - b.top - r.y) / r.h, ev.pointerType === "pen" ? ev.pressure : 0.5];
      }) : [[...p, e.pointerType === "pen" ? e.pressure : 0.5]];
      setDraft({ ...draft, points: [...(draft.points ?? []), ...extra] });
    } else {
      setDraft({ ...draft, b: p });
    }
  };
  const up = () => {
    if (!draft) return;
    const tiny = draft.type !== "pen" && draft.a && draft.b && Math.hypot(draft.a[0] - draft.b[0], draft.a[1] - draft.b[1]) < 0.004;
    if (!tiny) setShapes((s) => [...s, draft]);
    setDraft(null);
  };
  const commitText = () => {
    const label = textVal.trim();
    if (textAt && label) {
      // Enter and the blur that follows both land here: the guard keeps one label per anchor
      const at = textAt.a;
      setShapes((s) => (s.some((x) => x.type === "text" && x.a === at) ? s
        : [...s, { type: "text", color, size: SIZES[size] * 6, a: at, text: label }]));
    }
    setTextAt(null);
    setTextVal("");
  };

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const typing = (e.target as HTMLElement)?.closest?.("input, textarea, select");
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "z" && !typing) {
        e.preventDefault();
        e.stopPropagation();
        setShapes((s) => s.slice(0, -1));
        return;
      }
      if (e.key === "Escape") {
        e.stopPropagation();
        if (textAt) setTextAt(null);
        else onCancel();
        return;
      }
      if (typing || e.metaKey || e.ctrlKey || e.altKey) return;
      const t = TOOLS.find((x) => x.key === e.key.toLowerCase());
      if (t) {
        e.stopPropagation();
        setTool(t.id);
      }
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [onCancel, textAt]);

  return (
    <div className="draw-layer">
      <canvas ref={ref} className={`draw-canvas active tool-${tool}`} onPointerDown={down} onPointerMove={move}
        onPointerUp={up} onPointerCancel={up} />
      {textAt && (
        <input className="draw-text-input" autoFocus value={textVal} placeholder="Label, then Enter"
          style={{ left: textAt.x, top: textAt.y, color }} onChange={(e) => setTextVal(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") commitText();
            e.stopPropagation();
          }} onBlur={commitText} maxLength={120} />
      )}
      <div className="draw-tools" onPointerDown={(e) => e.stopPropagation()}>
        <div className="seg" role="radiogroup" aria-label="Drawing tool">
          {TOOLS.map((t) => (
            <button key={t.id} className={tool === t.id ? "on" : ""} title={`${t.label} (${t.key.toUpperCase()})`}
              aria-pressed={tool === t.id} onClick={() => setTool(t.id)}>{t.icon}</button>
          ))}
        </div>
        <div className="swatches" aria-label="Colour">
          {PALETTE.map((c) => (
            <button key={c} className={`swatch ${c === color ? "on" : ""}`} style={{ background: c }}
              title={c} aria-label={`Colour ${c}`} onClick={() => setColor(c)} />
          ))}
          <label className="swatch custom" title="Pick any colour">
            <input type="color" value={color} onChange={(e) => setColor(e.target.value)} aria-label="Custom colour" />
          </label>
        </div>
        <div className="seg" aria-label="Stroke size">
          {(Object.keys(SIZES) as (keyof typeof SIZES)[]).map((k) => (
            <button key={k} className={size === k ? "on" : ""} onClick={() => setSize(k)}>{k}</button>
          ))}
        </div>
        <div className="seg">
          <button onClick={() => setShapes((s) => s.slice(0, -1))} disabled={!shapes.length} title="Undo (⌘Z)">Undo</button>
          <button onClick={() => setShapes([])} disabled={!shapes.length}>Clear</button>
        </div>
        <div className="draw-save">
          <div className="seg" role="radiogroup" aria-label="Tag">
            {TAGS.map((t) => (
              <button key={t.id} className={`tagbtn tag-${t.id} ${tag === t.id ? "on" : ""}`} onClick={() => setTag(t.id)}>
                {t.glyph} {t.label}
              </button>
            ))}
          </div>
          <input className="draw-note" value={text} onChange={(e) => setText(e.target.value)} placeholder="What should change?"
            onKeyDown={(e) => {
              e.stopPropagation();
              if (e.key === "Enter" && (shapes.length || text.trim())) onSave({ shapes, tag, text });
            }} maxLength={4000} />
          <button className="primary" disabled={saving || (!shapes.length && !text.trim())}
            onClick={() => onSave({ shapes, tag, text })}>{saving ? "Saving…" : "Save note"}</button>
          <button onClick={onCancel}>Cancel</button>
        </div>
      </div>
    </div>
  );
}
