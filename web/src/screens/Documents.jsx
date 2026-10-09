// Your documents (design page 3): "N of M ready" + "N missing", the first missing item
// pinned in an amber card (Take photo / Choose file), uploaded items with a green check and
// masked details, the encryption note with "Delete now", and a sticky primary button.
// Names come from the scheme's documents list (the portal's labels). Consent first.

import { useState } from "react";
import { Bi, Empty, ScreenHeader, useLabel } from "../components.jsx";
import { useCase } from "../case.jsx";
import { tr } from "../i18n.js";
import { Icon } from "../icons.jsx";

const TYPES = ["image/jpeg", "image/png", "image/webp", "application/pdf"];
const AADHAAR_DOCS = new Set(["identity_proof"]);
const MISSING = new Set(["missing", "needed"]);

/** "Take photo" opens the camera (capture); "Choose file" opens the gallery / files (PDF too). */
function FileButtons({ item, onFile, prominent, disabled }) {
  const label = useLabel();
  const camId = `cam-${item.doc}`;
  const fileId = `file-${item.doc}`;
  return (
    <div className={`doc-actions ${prominent ? "doc-actions-big" : ""}`}>
      <label htmlFor={camId} className={`btn ${prominent ? "btn-gold" : "btn-secondary"} file-btn ${disabled ? "is-disabled" : ""}`}>
        <Icon name="camera" /> <Bi k="take_photo" />
      </label>
      <input id={camId} type="file" className="sr-only" accept="image/*" capture="environment" onChange={onFile}
        disabled={disabled} aria-label={`${label("take_photo")}: ${item.label_en}`} />
      <label htmlFor={fileId} className={`btn btn-ghost file-btn ${disabled ? "is-disabled" : ""}`}>
        <Icon name="fileUp" /> <Bi k="choose_file" />
      </label>
      <input id={fileId} type="file" className="sr-only" accept="image/*,application/pdf" onChange={onFile}
        disabled={disabled} aria-label={`${label("choose_file")}: ${item.label_en}`} />
    </div>
  );
}

function useUpload(item, maxBytes) {
  const { upload } = useCase();
  const [state, setState] = useState(null); // null | "uploading" | error key
  const [last4, setLast4] = useState("");
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
  return { state, onFile, last4, setLast4 };
}

function UploadState({ state, maxBytes }) {
  if (!state) return null;
  return (
    <p className={state === "uploading" ? "muted" : "error"} role={state === "uploading" ? "status" : "alert"}>
      <Bi k={state} vars={{ mb: Math.round(maxBytes / 1048576) }} />
    </p>
  );
}

function Aadhaar4({ item, last4, setLast4 }) {
  if (!AADHAAR_DOCS.has(item.doc)) return null;
  return (
    <div className="field">
      <label htmlFor={`a4-${item.doc}`}><Bi k="aadhaar_last4" /></label>
      <input id={`a4-${item.doc}`} inputMode="numeric" maxLength={4} pattern="[0-9]{4}" autoComplete="off"
        value={last4} onChange={(e) => setLast4(e.target.value.replace(/\D/g, "").slice(0, 4))} placeholder="1234" />
      <p className="muted small"><Bi k="aadhaar_hint" /></p>
    </div>
  );
}

function ForLine({ item }) {
  if (!item.schemes?.length) return null;
  return (
    <p className="muted small">
      <Bi k="docs_for" vars={{ titles: item.schemes.join(", ") }} envars={{ titles: (item.schemes_en || item.schemes).join(", ") }} />
    </p>
  );
}

/** The first missing document, pinned in amber. */
function PinnedItem({ item, maxBytes, consented }) {
  const u = useUpload(item, maxBytes);
  return (
    <li className="doc doc-pinned">
      <div className="doc-row">
        <span className="doc-icon doc-icon-warn"><Icon name="alert" /></span>
        <div className="doc-text">
          <h2 className="doc-name">{item.label}</h2>
          <p className="doc-sub"><Bi k={item.status === "missing" ? "doc_missing_now" : "doc_not_yet"} /></p>
          <ForLine item={item} />
        </div>
      </div>
      {consented && <Aadhaar4 item={item} last4={u.last4} setLast4={u.setLast4} />}
      <FileButtons item={item} onFile={u.onFile} prominent disabled={!consented} />
      <UploadState state={u.state} maxBytes={maxBytes} />
    </li>
  );
}

function DocItem({ item, maxBytes, consented }) {
  const { removeDocument } = useCase();
  const label = useLabel();
  const u = useUpload(item, maxBytes);
  const doc = item.document;
  const done = item.status === "uploaded";
  return (
    <li className={`doc doc-${item.status}`}>
      <div className="doc-row">
        <span className={`doc-icon ${done || item.status === "have" ? "doc-icon-ok" : "doc-icon-empty"}`}>
          {(done || item.status === "have") && <Icon name="check" strokeWidth={2.5} />}
        </span>
        <div className="doc-text">
          <h2 className="doc-name">{item.label}</h2>
          <p className="doc-sub">
            {done ? (
              <>
                {doc?.aadhaar ? <span className="aadhaar">{doc.aadhaar}</span> : <Bi k="doc_uploaded" />}
                {doc && <span className="muted"> · {Math.max(1, Math.round(doc.size_bytes / 1024))} KB</span>}
              </>
            ) : <Bi k={item.status === "have" ? "doc_have" : item.status === "missing" ? "doc_missing" : "doc_not_yet"} />}
          </p>
          <ForLine item={item} />
        </div>
        {doc && (
          <button type="button" className="round-btn" aria-label={`${label("delete")}: ${item.label_en}`} onClick={() => removeDocument(doc.id)}>
            <Icon name="trash" size={20} />
          </button>
        )}
      </div>
      {!done && consented && <Aadhaar4 item={item} last4={u.last4} setLast4={u.setLast4} />}
      {!done && <FileButtons item={item} onFile={u.onFile} disabled={!consented} />}
      <UploadState state={u.state} maxBytes={maxBytes} />
    </li>
  );
}

export default function Documents() {
  const { summary, setConsent, navigate, lang } = useCase();
  const [saving, setSaving] = useState(false);
  const checklist = summary?.checklist;
  const hours = summary?.limits?.doc_retention_hours ?? 24;
  const maxBytes = summary?.limits?.doc_max_bytes ?? 10 * 1024 * 1024;
  const consented = !!summary?.consent?.documents;
  const items = checklist?.items || [];
  const others = checklist?.others || [];
  const pinned = items.find((i) => MISSING.has(i.status));
  const rest = items.filter((i) => i !== pinned);
  const reviewing = summary?.pause?.type === "confirm";

  const consent = async (documents) => {
    setSaving(true);
    try {
      await setConsent({ documents });
    } finally {
      setSaving(false);
    }
  };

  const sub = checklist?.title ? tr(lang, "docs_for_scheme", { title: checklist.title }) : null;

  return (
    <main className="screen documents">
      <ScreenHeader k="documents_title" back="talk" sub={sub} />
      {items.length === 0 ? (
        <Empty k="docs_empty" icon="file" action={<button type="button" className="btn btn-primary" onClick={() => navigate("talk")}><Bi k="go_talk" dual /></button>} />
      ) : (
        <>
          <div className="progress" aria-label={`${checklist.ready} / ${checklist.total}`}>
            <div className="progress-row">
              <strong><Bi k="docs_progress" vars={{ ready: checklist.ready, total: checklist.total }} /></strong>
              {checklist.missing > 0 && <span className="missing-count"><Bi k="docs_missing_n" vars={{ n: checklist.missing }} /></span>}
            </div>
            <div className="progress-bar"><span style={{ width: `${(100 * checklist.ready) / Math.max(1, checklist.total)}%` }} /></div>
          </div>

          {!consented && (
            <section className="card consent-card" aria-labelledby="dc-h">
              <h2 id="dc-h"><Bi k="docs_consent_title" /></h2>
              <p><Bi k="docs_consent_body" vars={{ hours }} /></p>
              <button type="button" className="btn btn-primary btn-block" disabled={saving} onClick={() => consent(true)}>
                <Bi k="docs_consent_yes" dual />
              </button>
            </section>
          )}

          <ul className="doc-list">
            {pinned && <PinnedItem key={pinned.doc} item={pinned} maxBytes={maxBytes} consented={consented} />}
            {rest.map((item) => <DocItem key={item.doc} item={item} maxBytes={maxBytes} consented={consented} />)}
          </ul>

          {others.length > 0 && (
            <details className="card other-docs">
              <summary><Bi k="other_docs" vars={{ n: others.length }} /> <Icon name="chevronRight" className="chev" /></summary>
              <ul className="doc-list">
                {others.map((item) => <DocItem key={item.doc} item={item} maxBytes={maxBytes} consented={consented} />)}
              </ul>
            </details>
          )}

          <p className="note-lock">
            <Icon name="lock" size={20} />
            <span>
              <Bi k="docs_note" vars={{ hours }} />{" "}
              {consented && (
                <button type="button" className="link-btn" disabled={saving} onClick={() => consent(false)}>
                  <Bi k="delete_now" />
                </button>
              )}
            </span>
          </p>

          <div className="sticky-cta">
            <button type="button" className="btn btn-primary btn-block btn-lg" onClick={() => navigate(reviewing ? "review" : "talk")}>
              <Bi k={reviewing ? "go_review" : "continue_saathi"} dual />
            </button>
          </div>
        </>
      )}
    </main>
  );
}
