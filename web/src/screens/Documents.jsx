import { useState } from "react";
import { Bi, Empty, StatusBadge, useT } from "../components.jsx";
import { useCase } from "../case.jsx";
import { tr } from "../i18n.js";

const TYPES = ["image/jpeg", "image/png", "image/webp", "application/pdf"];
const BADGE = { missing: "no", needed: "warn", have: "ok", uploaded: "done" };
const AADHAAR_DOCS = new Set(["identity_proof"]);

/** "Take photo" opens the camera (capture); "Choose file" opens the gallery / files (PDF too). */
function FileButtons({ item, onFile, replacing }) {
  const t = useT();
  const camId = `cam-${item.doc}`;
  const fileId = `file-${item.doc}`;
  return (
    <div className="doc-actions">
      <label htmlFor={camId} className={`btn ${replacing ? "btn-ghost" : "btn-primary"} file-btn`}>
        <svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true">
          <path d="M4 8h3l2-3h6l2 3h3v11H4zM12 17a4 4 0 1 0 0-8 4 4 0 0 0 0 8z" fill="none" stroke="currentColor" strokeWidth="2" strokeLinejoin="round" />
        </svg>
        <Bi k="take_photo" />
      </label>
      <input id={camId} type="file" className="sr-only" accept="image/*" capture="environment" onChange={onFile}
        aria-label={`${t("take_photo")} · ${tr("en", "take_photo")}: ${item.label_en}`} />
      <label htmlFor={fileId} className="btn btn-ghost file-btn">
        <svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true">
          <path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8zM14 3v5h5M12 12v6M9 15l3-3 3 3" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
        <Bi k="choose_file" />
      </label>
      <input id={fileId} type="file" className="sr-only" accept="image/*,application/pdf" onChange={onFile}
        aria-label={`${t("choose_file")} · ${tr("en", "choose_file")}: ${item.label_en}`} />
    </div>
  );
}

function DocItem({ item, maxBytes }) {
  const { upload, removeDocument } = useCase();
  const [last4, setLast4] = useState("");
  const [state, setState] = useState(null); // null | "uploading" | error key
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
        <p className="muted small">
          <Bi k="docs_for" vars={{ titles: item.schemes.join(", ") }} envars={{ titles: (item.schemes_en || item.schemes).join(", ") }} />
        </p>
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
      <FileButtons item={item} onFile={onFile} replacing={!!doc} />
      {doc && (
        <button type="button" className="btn btn-danger-ghost" onClick={() => removeDocument(doc.id)}>
          <Bi k="delete" />
        </button>
      )}
      {state && (
        <p className={state === "uploading" ? "muted" : "error"} role={state === "uploading" ? "status" : "alert"}>
          <Bi k={state} vars={{ mb: Math.round(maxBytes / 1048576) }} />
        </p>
      )}
    </li>
  );
}

function LockedItem({ item }) {
  return (
    <li className={`card doc doc-${item.status}`}>
      <div className="doc-head">
        <h2 className="h-sm"><Bi text={item.label} en={item.label_en} block /></h2>
        <StatusBadge kind={BADGE[item.status] || "warn"}><Bi k={`doc_${item.status}`} /></StatusBadge>
      </div>
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
  const others = checklist?.others || [];
  const Item = consented ? DocItem : LockedItem;

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
      {checklist?.title && (
        <p className="lead">
          <Bi k="docs_for_scheme" vars={{ title: checklist.title }} envars={{ title: checklist.title_en }} block />
        </p>
      )}
      {items.length === 0 ? (
        <Empty k="docs_empty" />
      ) : (
        <>
          <div className="progress" aria-label={`${checklist.ready} / ${checklist.total}`}>
            <div className="progress-row">
              <Bi k="docs_progress" vars={{ ready: checklist.ready, total: checklist.total }} />
              {checklist.missing > 0 && <span className="missing-count"><Bi k="docs_missing_n" vars={{ n: checklist.missing }} /></span>}
            </div>
            <div className="progress-bar"><span style={{ width: `${(100 * checklist.ready) / Math.max(1, checklist.total)}%` }} /></div>
          </div>
          <p className="note-lock">
            <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
              <rect x="5" y="11" width="14" height="10" rx="2" fill="none" stroke="currentColor" strokeWidth="2" />
              <path d="M8 11V8a4 4 0 0 1 8 0v3" fill="none" stroke="currentColor" strokeWidth="2" />
            </svg>
            <Bi k="docs_note" vars={{ hours }} />
          </p>

          {!consented && (
            <section className="card consent-card" aria-labelledby="dc-h">
              <h2 id="dc-h"><Bi k="docs_consent_title" block /></h2>
              <p><Bi k="docs_consent_body" vars={{ hours }} block /></p>
              <button type="button" className="btn btn-primary btn-lg" disabled={saving} onClick={() => consent(true)}>
                <Bi k="docs_consent_yes" />
              </button>
            </section>
          )}

          <ul className={`doc-list ${consented ? "" : "doc-list-locked"}`} aria-disabled={!consented}>
            {items.map((item) => <Item key={item.doc} item={item} maxBytes={maxBytes} />)}
          </ul>

          {others.length > 0 && (
            <details className="card other-docs">
              <summary><Bi k="other_docs" vars={{ n: others.length }} /></summary>
              <ul className="doc-list">
                {others.map((item) => <Item key={item.doc} item={item} maxBytes={maxBytes} />)}
              </ul>
            </details>
          )}

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
