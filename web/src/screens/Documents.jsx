import { useState } from "react";
import { Bi, Empty, StatusBadge, useT } from "../components.jsx";
import { useCase } from "../case.jsx";
import { tr } from "../i18n.js";

const TYPES = ["image/jpeg", "image/png", "image/webp", "application/pdf"];
const BADGE = { missing: "no", needed: "warn", have: "ok", uploaded: "done" };
const AADHAAR_DOCS = new Set(["identity_proof"]);

function DocItem({ item, maxBytes }) {
  const { upload, removeDocument } = useCase();
  const t = useT();
  const [last4, setLast4] = useState("");
  const [state, setState] = useState(null); // null | "uploading" | error key
  const inputId = `file-${item.doc}`;
  const doc = item.document;

  const onFile = async (e) => {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    if (!TYPES.includes(file.type)) return setState("file_type_bad");
    if (file.size > maxBytes) return setState("file_too_big");
    setState("uploading");
    try {
      await upload(item.doc, file, AADHAAR_DOCS.has(item.doc) && /^\d{4}$/.test(last4) ? last4 : null);
      setState(null);
    } catch {
      setState("upload_failed");
    }
  };

  return (
    <li className={`card doc doc-${item.status}`}>
      <div className="doc-head">
        <h2 className="h-sm"><Bi text={item.label} en={item.label_en} block /></h2>
        <StatusBadge kind={BADGE[item.status] || "warn"}><Bi k={`doc_${item.status}`} /></StatusBadge>
      </div>
      {item.schemes?.length > 0 && (
        <p className="muted small"><Bi k="docs_for" vars={{ titles: item.schemes.join(", ") }} /></p>
      )}
      {doc && (
        <p className="doc-meta">
          <span>{doc.content_type} · {Math.max(1, Math.round(doc.size_bytes / 1024))} KB</span>
          {doc.aadhaar && <span className="aadhaar">Aadhaar {doc.aadhaar}</span>}
        </p>
      )}
      {AADHAAR_DOCS.has(item.doc) && !doc && (
        <div className="field">
          <label htmlFor={`a4-${item.doc}`}><Bi k="aadhaar_last4" /></label>
          <input id={`a4-${item.doc}`} inputMode="numeric" maxLength={4} pattern="[0-9]{4}" autoComplete="off"
            value={last4} onChange={(e) => setLast4(e.target.value.replace(/\D/g, "").slice(0, 4))} placeholder="1234" />
          <p className="muted small"><Bi k="aadhaar_hint" /></p>
        </div>
      )}
      <div className="doc-actions">
        <label htmlFor={inputId} className={`btn ${doc ? "btn-ghost" : "btn-primary"} file-btn`}>
          <svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true">
            <path d="M4 8h3l2-3h6l2 3h3v11H4zM12 17a4 4 0 1 0 0-8 4 4 0 0 0 0 8z" fill="none" stroke="currentColor" strokeWidth="2" strokeLinejoin="round" />
          </svg>
          <Bi k={doc ? "replace" : "take_photo"} />
        </label>
        <input id={inputId} type="file" className="sr-only" accept="image/*,application/pdf" capture="environment"
          onChange={onFile} aria-label={`${t(doc ? "replace" : "take_photo")} · ${tr("en", doc ? "replace" : "take_photo")}: ${item.label_en}`} />
        {doc && (
          <button type="button" className="btn btn-danger-ghost" onClick={() => removeDocument(doc.id)}>
            <Bi k="delete" />
          </button>
        )}
      </div>
      {state && (
        <p className={state === "uploading" ? "muted" : "error"} role={state === "uploading" ? "status" : "alert"}>
          <Bi k={state} vars={{ mb: Math.round(maxBytes / 1048576) }} />
        </p>
      )}
    </li>
  );
}

export default function Documents() {
  const { summary, setConsent } = useCase();
  const [saving, setSaving] = useState(false);
  const checklist = summary?.checklist;
  const hours = summary?.limits?.doc_retention_hours ?? 24;
  const maxBytes = summary?.limits?.doc_max_bytes ?? 10 * 1024 * 1024;
  const consented = !!summary?.consent?.documents;
  const items = checklist?.items || [];

  const consent = async (documents) => {
    setSaving(true);
    try {
      await setConsent({ documents });
    } finally {
      setSaving(false);
    }
  };

  return (
    <main className="screen">
      <h1><Bi k="documents_title" block /></h1>
      {items.length === 0 ? (
        <Empty k="docs_empty" />
      ) : (
        <>
          <div className="progress" aria-label={`${checklist.ready} / ${checklist.total}`}>
            <div className="progress-bar"><span style={{ width: `${(100 * checklist.ready) / Math.max(1, checklist.total)}%` }} /></div>
            <Bi k="docs_progress" vars={{ ready: checklist.ready, total: checklist.total }} />
          </div>
          <p className="note-lock">
            <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
              <rect x="5" y="11" width="14" height="10" rx="2" fill="none" stroke="currentColor" strokeWidth="2" />
              <path d="M8 11V8a4 4 0 0 1 8 0v3" fill="none" stroke="currentColor" strokeWidth="2" />
            </svg>
            <Bi k="docs_note" vars={{ hours }} />
          </p>

          {!consented ? (
            <section className="card consent-card" aria-labelledby="dc-h">
              <h2 id="dc-h"><Bi k="docs_consent_title" block /></h2>
              <p><Bi k="docs_consent_body" vars={{ hours }} block /></p>
              <button type="button" className="btn btn-primary btn-lg" disabled={saving} onClick={() => consent(true)}>
                <Bi k="docs_consent_yes" />
              </button>
            </section>
          ) : null}

          <ul className={`doc-list ${consented ? "" : "doc-list-locked"}`} aria-disabled={!consented}>
            {items.map((item) => (
              consented ? <DocItem key={item.doc} item={item} maxBytes={maxBytes} /> : (
                <li key={item.doc} className={`card doc doc-${item.status}`}>
                  <div className="doc-head">
                    <h2 className="h-sm"><Bi text={item.label} en={item.label_en} block /></h2>
                    <StatusBadge kind={BADGE[item.status] || "warn"}><Bi k={`doc_${item.status}`} /></StatusBadge>
                  </div>
                </li>
              )
            ))}
          </ul>

          {consented && (
            <button type="button" className="btn btn-danger-ghost" disabled={saving} onClick={() => consent(false)}>
              <Bi k="delete_all_docs" />
            </button>
          )}
        </>
      )}
    </main>
  );
}
