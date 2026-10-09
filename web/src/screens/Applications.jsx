// My applications: per scheme, the application ID, status and the timeline from the case's
// audit trail (eligibility checked -> review -> you said yes -> submitted). Live status from
// the portal (Phase 6 polling) adds "status_changed" entries and `checked_at`.

import { Bi, Empty, StatusBadge } from "../components.jsx";
import { useCase } from "../case.jsx";
import { dateTime } from "../format.js";

const STATUS_BADGE = { APPROVED: "ok", REJECTED: "no", CORRECTION_REQUIRED: "warn" };

export default function Applications() {
  const { summary, lang, pickScheme, busy } = useCase();
  const apps = summary?.applications || [];
  const next = summary?.next || [];
  const reviewing = summary?.pause?.type === "confirm";
  return (
    <main className="screen">
      <h1><Bi k="apps_title" block /></h1>
      {apps.length === 0 && <Empty k="apps_empty" />}
      {apps.map((a) => (
        <article key={a.scheme_id} className="card app" aria-labelledby={`a-${a.scheme_id}`}>
          <header className="scheme-head">
            <h2 id={`a-${a.scheme_id}`}><Bi text={a.title} en={a.title_en} block /></h2>
            <StatusBadge kind={STATUS_BADGE[a.status] || "done"}>
              <Bi text={a.status_text} en={a.status_text_en} />
            </StatusBadge>
          </header>
          <p className="app-id">
            <span className="muted"><Bi k="app_id" /></span>
            <code>{a.app_id}</code>
          </p>
          <ol className="timeline">
            {a.timeline.map((e, i) => (
              <li key={i}>
                <span className="tl-dot" aria-hidden="true" />
                <Bi k={`tl_${e.kind}`} />
                {e.app_id && <code className="small"> {e.app_id}</code>}
                <time dateTime={e.at} className="muted small">{dateTime(e.at, lang)}</time>
              </li>
            ))}
          </ol>
          <p className="muted small">
            {a.checked_at ? dateTime(a.checked_at, lang) : <Bi k="status_soon" />}
          </p>
          <div className="todo">
            <strong><Bi k="what_to_do" /></strong>
            <Bi k="nothing_to_do" block />
          </div>
        </article>
      ))}

      {next.length > 0 && (
        <section aria-labelledby="next-h">
          <h2 id="next-h"><Bi k="next_title" block /></h2>
          <ul className="next-list">
            {next.map((s) => (
              <li key={s.scheme_id} className="card next">
                <div>
                  <StatusBadge kind="ok"><Bi k="ready_to_apply" /></StatusBadge>
                  <Bi text={s.title} en={s.title_en} block />
                </div>
                <button type="button" className="btn btn-primary" disabled={busy || reviewing} onClick={() => pickScheme(s)}>
                  <Bi k="apply_this" />
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}
    </main>
  );
}
