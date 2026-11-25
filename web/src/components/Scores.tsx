import type { Critique, Version } from "../types";
import { avgOf, CRITERIA, CRITERIA_SHORT } from "../util";

/** "6.29 / 5.86": the latest flash and pro critique averages for a version. */
export function ScorePair({ v }: { v: Version }) {
  const f = avgOf(v.critiques, "flash");
  const p = avgOf(v.critiques, "pro");
  if (f === null && p === null) return v.kind === "studio" ? <span className="score muted">built</span> : <span className="score muted">no critique</span>;
  return (
    <span className="score" title="Critic average, 1–10: flash / pro reviewer">
      <b>{f?.toFixed(2) ?? "–"}</b>
      <span className="slash">/</span>
      <b>{p?.toFixed(2) ?? "–"}</b>
    </span>
  );
}

function latest(cs: Critique[], m: string) {
  return cs.filter((c) => c.model_short === m).sort((a, b) => (a.when < b.when ? 1 : -1))[0];
}

/** Criterion-by-criterion table for a handful of versions: one column per version and model. */
export function ScoreTable({ versions }: { versions: Version[] }) {
  const cols = versions.flatMap((v) => (["flash", "pro"] as const)
    .map((m) => ({ v, m, c: latest(v.critiques, m) }))
    .filter((x) => x.c));
  if (!cols.length) return <p className="empty-note">No critiques for these versions yet.</p>;
  return (
    <div className="score-table-wrap">
      <table className="score-table">
        <thead>
          <tr>
            <th scope="col">Criterion</th>
            {cols.map(({ v, m, c }) => (
              <th key={v.id + m} scope="col" title={c!.label}>{v.label}<small>{m}</small></th>
            ))}
          </tr>
        </thead>
        <tbody>
          {CRITERIA.map((name, i) => (
            <tr key={name}>
              <th scope="row" title={name}>{CRITERIA_SHORT[i]}</th>
              {cols.map(({ v, m, c }) => {
                const s = c!.scores[String(i + 1)];
                return (
                  <td key={v.id + m} title={c!.justification[String(i + 1)] ?? ""}>
                    <span className="cell-bar" style={{ width: `${(s ?? 0) * 3}px` }} />
                    <span className="cell-num">{s ?? "–"}</span>
                  </td>
                );
              })}
            </tr>
          ))}
          <tr className="avg">
            <th scope="row">Average</th>
            {cols.map(({ v, m, c }) => <td key={v.id + m}><span className="cell-num">{c!.avg.toFixed(2)}</span></td>)}
          </tr>
        </tbody>
      </table>
    </div>
  );
}
