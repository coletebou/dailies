import { useEffect } from "react";
import { navigate, useRoute, type View } from "./router";
import { DataProvider, useData } from "./store";
import Compare from "./views/Compare";
import Live from "./views/Live";
import Notes from "./views/Notes";
import Timeline from "./views/Timeline";

const TABS: { id: View; label: string; key: string }[] = [
  { id: "compare", label: "Compare", key: "g c" },
  { id: "timeline", label: "Timeline", key: "g t" },
  { id: "live", label: "Live", key: "g l" },
  { id: "notes", label: "Notes", key: "g n" },
];

function Shell() {
  const route = useRoute();
  const { builds, annotations, error, toasts } = useData();
  const running = builds.find((b) => b.status === "running");
  const queued = builds.filter((b) => b.status === "queued").length;
  const open = annotations.filter((a) => a.status === "open").length;

  useEffect(() => {
    document.title = `${TABS.find((t) => t.id === route.view)?.label} · Dailies`;
  }, [route.view]);

  return (
    <div className="app">
      <header className="topbar">
        <a className="wordmark" href="#/compare" aria-label="Dailies home">
          <span className="wm-film">Dailies</span>
        </a>
        <nav className="tabs" aria-label="Views">
          {TABS.map((t) => (
            <a key={t.id} href={`#/${t.id}`} className={route.view === t.id ? "on" : ""} aria-current={route.view === t.id ? "page" : undefined}
              onClick={(e) => { e.preventDefault(); navigate(t.id); }}>
              {t.label}
              {t.id === "notes" && open > 0 && <span className="count">{open}</span>}
            </a>
          ))}
        </nav>
        <span className="pane-spacer" />
        {running && (
          <a className="build-pill" href="#/timeline" title={`Building ${running.name}`}>
            <span className="pulse" aria-hidden /> {running.name} {running.frames_total ? Math.round((100 * running.frames_done) / running.frames_total) : 0}%
            {queued > 0 && <span className="muted"> +{queued}</span>}
          </a>
        )}
        {error && <span className="conn-err" title={error}>offline: {error.slice(0, 60)}</span>}
      </header>
      <main className={`view view-${route.view}`}>
        {route.view === "compare" && <Compare route={route} />}
        {route.view === "timeline" && <Timeline />}
        {route.view === "live" && <Live />}
        {route.view === "notes" && <Notes />}
      </main>
      <div className="toasts" role="status" aria-live="polite">
        {toasts.map((t) => <div key={t.id} className={`toast ${t.kind}`}>{t.text}</div>)}
      </div>
    </div>
  );
}

export default function App() {
  return (
    <DataProvider>
      <Shell />
    </DataProvider>
  );
}
