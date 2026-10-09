// Privacy: consent switches, what the agent knows, "see all my data" (GET /cases/{id}/data:
// metadata only, Aadhaar as last 4) and "delete my data" (DELETE, two steps).

import { useState } from "react";
import { Bi } from "../components.jsx";
import { useCase } from "../case.jsx";

function Toggle({ k, checked, onChange, disabled }) {
  const id = `c-${k}`;
  return (
    <div className="toggle-row">
      <label htmlFor={id}><Bi k={k} block /></label>
      <input id={id} type="checkbox" role="switch" className="switch" checked={checked} disabled={disabled}
        onChange={(e) => onChange(e.target.checked)} />
    </div>
  );
}

export default function Profile() {
  const { summary, session, setConsent, loadMyData, deleteMyData, speakReplies, setSpeakReplies } = useCase();
  const [data, setData] = useState(null);
  const [confirming, setConfirming] = useState(false);
  const [working, setWorking] = useState(false);
  const consent = summary?.consent || {};

  const run = async (fn) => {
    setWorking(true);
    try {
      await fn();
    } finally {
      setWorking(false);
    }
  };

  return (
    <main className="screen">
      <h1><Bi k="profile_title" block /></h1>

      <section className="card" aria-labelledby="cons-h">
        <h2 id="cons-h" className="h-sm"><Bi k="consent_title" /></h2>
        <Toggle k="consent_profile" checked={!!consent.profile} disabled={working}
          onChange={(v) => run(() => setConsent({ profile: v }))} />
        <Toggle k="consent_documents" checked={!!consent.documents} disabled={working}
          onChange={(v) => run(() => setConsent({ documents: v }))} />
        <Toggle k="speak_replies" checked={!!speakReplies} onChange={(v) => setSpeakReplies(v)} />
        <p className="muted small"><Bi k="speak_replies_body" /></p>
      </section>

      <section className="card" aria-labelledby="saved-h">
        <h2 id="saved-h" className="h-sm"><Bi k="saved_details" /></h2>
        {summary?.profile?.length ? (
          <ul className="chips">
            {summary.profile.map((p) => (
              <li key={p.field} className="chip">
                <span className="chip-label"><Bi text={p.label} en={p.label_en} /></span>
                <strong className="chip-value">{p.text}</strong>
              </li>
            ))}
          </ul>
        ) : (
          <p className="muted"><Bi k="known_empty" /></p>
        )}
        {session && <p className="muted small"><Bi k="case_label" />: <code>{session.case_id.slice(0, 12)}…</code></p>}
      </section>

      <section className="card" aria-labelledby="data-h">
        <h2 id="data-h" className="h-sm"><Bi k="view_data" /></h2>
        <button type="button" className="btn btn-secondary" disabled={working}
          onClick={() => (data ? setData(null) : run(async () => setData(await loadMyData().catch(() => ({ empty: true })))))}>
          <Bi k={data ? "hide_data" : "view_data"} />
        </button>
        {data && !data.empty && (
          <div className="data-view">
            <p><Bi k="data_events" vars={{ n: data.events?.length ?? 0 }} /></p>
            <p><Bi k="data_audit" vars={{ n: data.audit?.length ?? 0 }} /></p>
            <pre tabIndex={0} aria-label="My data (JSON)">{JSON.stringify(
              { consent: data.consent, saved_profile: data.saved_profile, case_memory: data.case_memory, documents: data.documents },
              null, 2)}</pre>
          </div>
        )}
        {data?.empty && <p className="muted"><Bi k="known_empty" /></p>}
      </section>

      <section className="card danger-zone" aria-labelledby="del-h">
        <h2 id="del-h" className="h-sm"><Bi k="delete_data" /></h2>
        {!confirming ? (
          <button type="button" className="btn btn-danger" onClick={() => setConfirming(true)}>
            <Bi k="delete_data" />
          </button>
        ) : (
          <div role="alertdialog" aria-labelledby="del-h" aria-describedby="del-body">
            <p id="del-body"><Bi k="delete_confirm" block /></p>
            <div className="row">
              <button type="button" className="btn btn-danger" disabled={working}
                onClick={() => run(async () => { await deleteMyData(); setConfirming(false); })}>
                <Bi k="delete_yes" />
              </button>
              <button type="button" className="btn btn-ghost" onClick={() => setConfirming(false)}>
                <Bi k="cancel" />
              </button>
            </div>
          </div>
        )}
      </section>
    </main>
  );
}
