import { useEffect, useState } from "react";

export type View = "compare" | "timeline" | "live" | "notes";
export interface Route { view: View; params: URLSearchParams }

function parse(): Route {
  const h = location.hash.replace(/^#\/?/, "");
  const [path, query = ""] = h.split("?");
  const view = (["compare", "timeline", "live", "notes"].includes(path) ? path : "compare") as View;
  return { view, params: new URLSearchParams(query) };
}

export function useRoute(): Route {
  const [r, setR] = useState(parse);
  useEffect(() => {
    const on = () => setR(parse());
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return r;
}

export function navigate(view: View, params: Record<string, string | number | undefined> = {}, replace = false) {
  const p = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => v !== undefined && v !== "" && p.set(k, String(v)));
  const q = p.toString();
  const h = `#/${view}${q ? "?" + q : ""}`;
  // replace = silently keep the URL shareable (no hashchange, so views don't re-read their own state)
  if (replace) history.replaceState(null, "", h);
  else location.hash = h;
}
