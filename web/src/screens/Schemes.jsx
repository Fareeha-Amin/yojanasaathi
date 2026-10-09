import { useState } from "react";
import { Bi, Empty, SpeakButton, StatusBadge, useT } from "../components.jsx";
import { useCase } from "../case.jsx";
import { fieldValue, usableUrl } from "../format.js";
import { tr } from "../i18n.js";

const OP_KEY = { ">=": "rule_min", "<=": "rule_max", ">": "rule_gt", "<": "rule_lt", "==": "rule_eq", in: "rule_eq" };

function WhyBox({ scheme }) {
  return (
    <table className="why">
      <caption className="sr-only">Why</caption>
      <thead>
        <tr>
          <th scope="col"><span className="sr-only">Field</span></th>
          <th scope="col"><Bi k="col_you" /></th>
          <th scope="col"><Bi k="col_rule" /></th>
          <th scope="col"><span className="sr-only">Result</span></th>
        </tr>
      </thead>
      <tbody>
        {scheme.clauses.map((c, i) => {
          const limit = Array.isArray(c.limit) ? c.limit.join(", ") : fieldValue(c.field, c.limit);
          const mark = c.result === true ? "✓" : c.result === false ? "✗" : "?";
          return (
            <tr key={i} className={`why-${c.result === true ? "ok" : c.result === false ? "no" : "unknown"}`}>
              <th scope="row"><Bi text={c.label} en={c.label_en} /></th>
              <td>{c.value === null || c.value === undefined ? <Bi k="not_told" /> : <strong>{fieldValue(c.field, c.value)}</strong>}</td>
              <td><Bi k={OP_KEY[c.op] || "rule_eq"} vars={{ limit }} /></td>
              <td aria-label={c.result === true ? "meets the rule" : c.result === false ? "does not meet the rule" : "unknown"}>
                <span className="mark" aria-hidden="true">{mark}</span>
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

function InlineAnswer({ field }) {
  const { answerField, busy } = useCase();
  const t = useT();
  const [value, setValue] = useState("");
  const id = `ans-${field.field}`;
  return (
    <form className="inline-answer" onSubmit={(e) => {
      e.preventDefault();
      const n = parseInt(value, 10);
      if (Number.isFinite(n)) answerField(field.field, n, `${field.label}: ${n}`);
    }}>
      <label htmlFor={id}><Bi k="question_for" vars={{ field: field.label }} envars={{ field: field.label_en }} /></label>
      <div className="row">
        <input id={id} inputMode="numeric" pattern="[0-9]*" value={value} onChange={(e) => setValue(e.target.value.replace(/\D/g, ""))}
          aria-label={`${field.label} · ${field.label_en}`} placeholder={field.field === "annual_income" ? "120000" : "62"} />
        <button type="submit" className="btn btn-secondary" disabled={busy || !value}>{t("answer")}</button>
      </div>
    </form>
  );
}

function SchemeCard({ s, pause }) {
  const { pickScheme, busy, navigate, lang } = useCase();
  const reviewing = pause?.type === "confirm" ? pause.preview?.scheme_id : null;
  const nMissing = s.missing_fields?.length || 0;
  const url = usableUrl(s.source_url);
  const spoken = [s.title, ...(s.reasons || [])].join(". ");
  return (
    <article className={`card scheme scheme-${s.status}`} aria-labelledby={`t-${s.scheme_id}`}>
      <header className="scheme-head">
        <h2 id={`t-${s.scheme_id}`}><Bi text={s.title} en={s.title_en} block /></h2>
        {s.app_id ? (
          <StatusBadge kind="done"><Bi k="applied" /></StatusBadge>
        ) : s.status === "eligible" ? (
          <StatusBadge kind="ok"><Bi k="st_eligible" /></StatusBadge>
        ) : s.status === "unknown" ? (
          <StatusBadge kind="warn"><Bi k={nMissing === 1 ? "st_unknown_1" : "st_unknown_n"} vars={{ n: nMissing }} /></StatusBadge>
        ) : (
          <StatusBadge kind="no"><Bi k="st_not_eligible" /></StatusBadge>
        )}
      </header>

      {s.app_id && <p className="app-id-line"><Bi k="app_id" />: <code>{s.app_id}</code></p>}

      {s.status === "not_eligible" ? (
        <details className="why-details">
          <summary><Bi k="why_box" /></summary>
          <WhyBox scheme={s} />
        </details>
      ) : (
        <div className="why-wrap">
          <h3 className="h-xs"><Bi k="why_box" /></h3>
          <WhyBox scheme={s} />
        </div>
      )}

      {s.status === "unknown" && !s.app_id && s.missing_fields?.[0] && <InlineAnswer field={s.missing_fields[0]} />}

      <p className="source">
        {url ? <a href={url} target="_blank" rel="noopener noreferrer"><Bi k="source" /></a> : <Bi k="source" />}
        {" · "}
        <Bi text={tr(lang, "effective", { date: s.effective_date_text })}
          en={tr("en", "effective", { date: s.effective_date_text_en })} />
        <span className="demo-tag"><Bi k="demo_scheme" /></span>
      </p>
      {s.status === "eligible" && !s.app_id && (
        <p className="muted small"><Bi k="docs_n" vars={{ n: s.documents.length }} /></p>
      )}

      <div className="scheme-actions">
        <SpeakButton text={spoken} />
        {s.status === "eligible" && !s.app_id && (
          reviewing === s.scheme_id ? (
            <button type="button" className="btn btn-primary" onClick={() => navigate("review")}><Bi k="open_review" /></button>
          ) : (
            <button type="button" className="btn btn-primary" disabled={busy || !!reviewing}
              title={reviewing ? "Finish the open review first" : undefined} onClick={() => pickScheme(s)}>
              <Bi k="apply_this" />
            </button>
          )
        )}
      </div>
      {reviewing && reviewing !== s.scheme_id && s.status === "eligible" && !s.app_id && (
        <p className="muted small"><Bi k="finish_review_first" /></p>
      )}
    </article>
  );
}

export default function Schemes() {
  const { summary } = useCase();
  const schemes = summary?.schemes || [];
  const eligible = schemes.filter((s) => s.status === "eligible").length;
  const notEligible = schemes.filter((s) => s.status === "not_eligible" && !s.app_id);
  const anyKnown = summary?.profile?.length > 0;
  return (
    <main className="screen">
      <h1><Bi k="schemes_title" block /></h1>
      {!anyKnown ? (
        <Empty k="schemes_empty" />
      ) : (
        <p className="lead"><Bi k="schemes_count" vars={{ n: eligible, total: schemes.length }} block /></p>
      )}
      {anyKnown && schemes.filter((s) => s.status !== "not_eligible" || s.app_id)
        .map((s) => <SchemeCard key={s.scheme_id} s={s} pause={summary.pause} />)}
      {anyKnown && notEligible.length > 0 && (
        <details className="card not-eligible-group">
          <summary>
            <Bi k={notEligible.length === 1 ? "not_eligible_group_1" : "not_eligible_group"} vars={{ n: notEligible.length }} />
          </summary>
          {notEligible.map((s) => (
            <div key={s.scheme_id} className="ne-scheme">
              <h3><Bi text={s.title} en={s.title_en} block /></h3>
              <WhyBox scheme={s} />
            </div>
          ))}
        </details>
      )}
    </main>
  );
}
