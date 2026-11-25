import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { api } from "./api";
import type { Annotation, Build, Meta, Sources, Version } from "./types";

interface Toast { id: number; text: string; kind: "info" | "error" }

interface Data {
  meta: Meta | null;
  versions: Version[];
  sources: Sources | null;
  annotations: Annotation[];
  builds: Build[];
  error: string | null;
  refreshVersions: () => Promise<void>;
  refreshSources: () => Promise<void>;
  refreshAnnotations: () => Promise<void>;
  refreshBuilds: () => Promise<void>;
  toast: (text: string, kind?: Toast["kind"]) => void;
  toasts: Toast[];
}

const Ctx = createContext<Data | null>(null);

export function useData(): Data {
  const d = useContext(Ctx);
  if (!d) throw new Error("useData outside provider");
  return d;
}

function usePoll(fn: () => void, ms: number) {
  const ref = useRef(fn);
  ref.current = fn;
  useEffect(() => {
    const id = window.setInterval(() => {
      if (document.visibilityState === "visible") ref.current();
    }, ms);
    return () => window.clearInterval(id);
  }, [ms]);
}

export function DataProvider({ children }: { children: ReactNode }) {
  const [meta, setMeta] = useState<Meta | null>(null);
  const [versions, setVersions] = useState<Version[]>([]);
  const [sources, setSources] = useState<Sources | null>(null);
  const [annotations, setAnnotations] = useState<Annotation[]>([]);
  const [builds, setBuilds] = useState<Build[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [toasts, setToasts] = useState<Toast[]>([]);

  const toast = useCallback((text: string, kind: Toast["kind"] = "info") => {
    const id = Date.now() + Math.random();
    setToasts((t) => [...t.slice(-3), { id, text, kind }]);
    window.setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), kind === "error" ? 7000 : 3500);
  }, []);

  const guard = useCallback(async (p: Promise<void>) => {
    try {
      await p;
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  const refreshVersions = useCallback(() => guard(api.versions().then(setVersions)), [guard]);
  const refreshSources = useCallback(() => guard(api.sources().then(setSources)), [guard]);
  const refreshAnnotations = useCallback(() => guard(api.annotations().then(setAnnotations)), [guard]);
  const refreshBuilds = useCallback(() => guard(api.builds().then(setBuilds)), [guard]);

  useEffect(() => {
    guard(api.meta().then(setMeta));
    refreshVersions();
    refreshSources();
    refreshAnnotations();
    refreshBuilds();
  }, [guard, refreshVersions, refreshSources, refreshAnnotations, refreshBuilds]);

  const busy = builds.some((b) => b.status === "running" || b.status === "queued");
  usePoll(refreshBuilds, busy ? 2000 : 15000);
  usePoll(refreshVersions, 30000);
  usePoll(refreshSources, 30000);
  usePoll(refreshAnnotations, 20000);

  // a build that just finished adds a version: pick it up immediately
  const doneCount = builds.filter((b) => b.status === "done").length;
  useEffect(() => {
    if (doneCount) refreshVersions();
  }, [doneCount, refreshVersions]);

  const value = useMemo<Data>(() => ({
    meta, versions, sources, annotations, builds, error, toast, toasts,
    refreshVersions, refreshSources, refreshAnnotations, refreshBuilds,
  }), [meta, versions, sources, annotations, builds, error, toast, toasts, refreshVersions, refreshSources,
    refreshAnnotations, refreshBuilds]);

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}
