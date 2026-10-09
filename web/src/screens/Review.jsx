// Review & confirm: the human gate's screen. Shown while the case is paused at the confirm
// interrupt (summary.review). "ಹೌದು, ಸಲ್ಲಿಸಿ" sends the citizen's yes as words through /turn,
// exactly like saying it: the agent's deterministic gate decides, not this button.
// Edits go to POST /cases/{id}/edit: the rules run again and a new read-back follows.

import { useState } from "react";
import { Bi, Empty, SpeakButton, StatusBadge, useT } from "../components.jsx";
import { useCase } from "../case.jsx";
import { fieldValue, usableUrl } from "../format.js";
import { tr } from "../i18n.js";

function FieldRow({ f }) {
  const { edit, busy } = useCase();
  const t = useT();
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(String(f.value ?? ""));
  const [error, setError] = useState(false);
  const id = `edit-${f.field}`;

  const save = async (e) => {
    e.preventDefault();
    const n = parseInt(value, 10);
    if (!Number.isFinite(n)) return setError(true);
    try {
      setError(false);
      await edit(f.field, n, `${f.label}: ${fieldValue(f.field, n)}`);
      setEditing(false);
    } catch {
      setError(true);
    }
  };

  return (
    <li className={`field-row ${f.unsure ? "field-unsure" : ""}`}>
      <div className="field-main">
        <span className="field-label"><Bi text={f.label} en={f.label_en} /></span>
        {!editing && <strong className="field-value">{f.text}</strong>}
        {f.unsure && !editing && (
          <span className="unsure-note" role="note">
            <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
              <path d="M12 3 2 20h20L12 3zM12 10v4M12 17h.01" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
            <Bi k="unsure_note" />
          </span>
        )}
      </div>
      {f.editable && !editing && (
        <button type="button" className="btn btn-ghost btn-sm" onClick={() => setEditing(true)}
          aria-label={`${t("edit")}: ${f.label} · Edit ${f.label_en}`}>
          <Bi k="edit" />
        </button>
      )}
      {editing && (
        <form className="edit-form" onSubmit={save}>
          <label htmlFor={id} className="sr-only">{f.label_en}</label>
          <input id={id} inputMode="numeric" value={value} autoFocus
            onChange={(e) => setValue(e.target.value.replace(/\D/g, ""))} />
          <button type="submit" className="btn btn-primary btn-sm" disabled={busy || !value}><Bi k="save" /></button>
          <button type="button" className="btn btn-ghost btn-sm" onClick={() => { setEditing(false); setValue(String(f.value ?? "")); }}>
            <Bi k="cancel" />
          </button>
          {error && <p className="error" role="alert"><Bi k="edit_failed" /></p>}
        </form>
      )}
    </li>
  );
}

const DOC_BADGE = { missing: "no", needed: "warn", have: "ok", uploaded: "done" };

export default function Review() {
  const { summary, confirm, busy, lang } = useCase();
  const r = summary?.review;
  if (!r) {
    return (
      <main className="screen">
        <h1><Bi k="review_title" block /></h1>
        <Empty k="review_empty" />
      </main>
    );
  }
  const url = usableUrl(r.source_url);
  return (
    <main className="screen review">
      <h1><Bi k="review_title" block /></h1>
      <p className="review-scheme"><Bi text={r.title} en={r.title_en} block /></p>

      <div className="readback card">
        <p><Bi text={r.readback} en={r.readback_en} block /></p>
        <SpeakButton text={r.readback} />
      </div>

      <section aria-labelledby="det-h">
        <h2 id="det-h" className="h-sm"><Bi k="your_details" /></h2>
        <ul className="fields">
          {r.fields.map((f) => <FieldRow key={`${f.field}-${f.value}`} f={f} />)}
        </ul>
      </section>

      <section aria-labelledby="rdocs-h">
        <h2 id="rdocs-h" className="h-sm"><Bi k="documents_short" /></h2>
        <ul className="mini-docs">
          {r.documents.map((d) => (
            <li key={d.doc}>
              <Bi text={d.label} en={d.label_en} />
              <StatusBadge kind={DOC_BADGE[d.status] || "warn"}><Bi k={`doc_${d.status}`} /></StatusBadge>
            </li>
          ))}
        </ul>
      </section>

      <div className="gate">
        <p className="gate-note"><Bi k="nothing_without_yes" block /></p>
        <button type="button" className="btn btn-yes" disabled={busy} onClick={() => confirm(true)}>
          <span lang="kn">ಹೌದು, ಸಲ್ಲಿಸಿ</span>
          <span className="sep" aria-hidden="true">·</span>
          <span lang="en">Yes, submit</span>
          {lang === "hi" && <span lang="hi" className="btn-yes-hi">हाँ, जमा करें</span>}
        </button>
        <button type="button" className="btn btn-secondary" disabled={busy} onClick={() => confirm(false)}>
          <Bi k="not_now" />
        </button>
      </div>

      <section className="card placeholder-card" aria-labelledby="form-h">
        <h2 id="form-h" className="h-sm"><Bi k="form_title" /></h2>
        <p className="muted small"><Bi k="form_placeholder" block /></p>
        <ul className="form-fields">
          {r.form_fields.map((f) => (
            <li key={f.name} className={f.name === "declaration_consent" ? "declaration" : ""}>
              <Bi text={f.label} en={f.label_en} block />
              <span className="pending" aria-label="not filled yet">—</span>
            </li>
          ))}
        </ul>
        {r.screenshots?.length ? (
          <div className="shots">{r.screenshots.map((s, i) => <img key={i} src={s.url || s} alt={`${tr("en", "screenshot")} ${i + 1}`} className="shot" />)}</div>
        ) : (
          <p className="shot-placeholder" aria-hidden="false"><Bi k="screenshots_placeholder" block /></p>
        )}
      </section>

      <p className="source">
        {url ? <a href={url} target="_blank" rel="noreferrer"><Bi k="source" /></a> : <Bi k="source" />}
        {" · "}
        <Bi text={tr(lang, "effective", { date: r.effective_date_text })} en={tr("en", "effective", { date: r.effective_date })} />
      </p>
    </main>
  );
}
