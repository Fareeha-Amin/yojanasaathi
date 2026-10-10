// Schemes for you (design page 2): "Based on" the citizen's real values, the first scheme
// they qualify for emphasised (WHY with icons, official rule link, Apply with YojanaSaathi),
// other qualifying schemes as compact cards, "question left" cards, and one collapsed row
// for the schemes they don't qualify for. Opened from Talk and from My applications.

import { useState } from "react";
import { Bi, Empty, ScreenHeader, SpeakButton, StatusBadge, useLabel, useText } from "../components.jsx";
import { useCase } from "../case.jsx";
import { fieldValue, usableUrl } from "../format.js";
import { tr } from "../i18n.js";
import { Icon } from "../icons.jsx";

const OP_KEY = { ">=": "why_min", "<=": "why_max", ">": "why_gt", "<": "why_lt", "==": "why_eq", in: "why_eq" };

/** One WHY line in the case language, e.g. "Age 62 · rule needs 60 or above". */
export function whyLine(c, lang) {
  const label = lang === "en" && c.label_en ? c.label_en : c.label;
  const limit = Array.isArray(c.limit) ? c.limit.join(", ") : fieldValue(c.field, c.limit);
  if (c.value === null || c.value === undefined) return tr(lang, "why_unknown", { label });
  return tr(lang, OP_KEY[c.op] || "why_eq", { label, value: fieldValue(c.field, c.value), limit });
}

function WhyBox({ scheme, compact = false }) {
  const { lang } = useCase();
  return (
    <div className={`why-box ${compact ? "why-box-plain" : ""}`}>
      {!compact && <p className="why-head"><Bi k="why_box" /></p>}
      <ul className="why-list">
        {scheme.clauses.map((c, i) => {
          const state = c.result === true ? "ok" : c.result === false ? "no" : "unknown";
          return (
            <li key={i} className={`why-${state}`}>
              <Icon name={state === "ok" ? "check" : state === "no" ? "x" : "help"} size={20} strokeWidth={2.5}
                title={state === "ok" ? "meets the rule" : state === "no" ? "does not meet the rule" : "not known yet"} />
              <span>{whyLine(c, lang)}</span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function RuleLink({ s }) {
  const { lang } = useCase();
  const url = usableUrl(s.source_url);
  const date = lang === "en" && s.effective_date_text_en ? s.effective_date_text_en : s.effective_date_text;
  const text = tr(lang, "official_rule", { date });
  return url ? (
    <a className="rule-link" href={url} target="_blank" rel="noopener noreferrer">
      <Icon name="external" size={18} /> {text}
    </a>
  ) : (
    <p className="rule-link rule-link-off"><Icon name="file" size={18} /> {text}</p>
  );
}

function ApplyButton({ s, reviewing }) {
  const { pickScheme, busy, navigate } = useCase();
  if (reviewing === s.scheme_id) {
    return (
      <button type="button" className="btn btn-primary btn-block" onClick={() => navigate("review")}>
        <Bi k="open_review" dual />
      </button>
    );
  }
  return (
    <>
      <button type="button" className="btn btn-primary btn-block" disabled={busy || !!reviewing} onClick={() => pickScheme(s)}>
        <Bi k="apply_with" dual />
      </button>
      {reviewing && <p className="muted small"><Bi k="finish_review_first" /></p>}
    </>
  );
}

function CardTop({ s, badge }) {
  return (
    <div className="card-top">
      {badge}
      <span className="category"><Bi text={s.category} en={s.category_en} /></span>
    </div>
  );
}

function FeaturedCard({ s, reviewing }) {
  return (
    <article className="card scheme scheme-featured" aria-labelledby={`t-${s.scheme_id}`}>
      <CardTop s={s} badge={<StatusBadge kind="ok-solid" icon="check"><Bi k="st_eligible" /></StatusBadge>} />
      <h2 id={`t-${s.scheme_id}`} className="scheme-title"><Bi text={s.title} en={s.title_en} /></h2>
      <WhyBox scheme={s} />
      <RuleLink s={s} />
      <ApplyButton s={s} reviewing={reviewing} />
    </article>
  );
}

function CompactCard({ s, reviewing }) {
  const { lang } = useCase();
  const first = s.clauses.find((c) => c.result === true) || s.clauses[0];
  return (
    <details className="card scheme scheme-compact">
      <summary aria-labelledby={`t-${s.scheme_id}`}>
        <CardTop s={s} badge={s.app_id
          ? <StatusBadge kind="done" icon="check"><Bi k="applied" /></StatusBadge>
          : <StatusBadge kind="ok" icon="check"><Bi k="st_eligible" /></StatusBadge>} />
        <h2 id={`t-${s.scheme_id}`} className="scheme-title"><Bi text={s.title} en={s.title_en} /></h2>
        {s.app_id ? (
          <p className="muted"><Bi k="app_id" />: <code>{s.app_id}</code></p>
        ) : first && <p className="muted">{tr(lang, "why_line", { text: whyLine(first, lang) })}</p>}
      </summary>
      <div className="compact-body">
        <WhyBox scheme={s} />
        <RuleLink s={s} />
        {!s.app_id && <ApplyButton s={s} reviewing={reviewing} />}
      </div>
    </details>
  );
}

function QuestionCard({ s }) {
  const { answerField, busy, connectVoice } = useCase();
  const label = useLabel();
  const field = s.missing_fields[0];
  const [value, setValue] = useState("");
  const n = s.missing_fields.length;
  const id = `ans-${s.scheme_id}`;
  return (
    <article className="card scheme scheme-question" aria-labelledby={`t-${s.scheme_id}`}>
      <CardTop s={s} badge={<StatusBadge kind="white"><Bi k={n === 1 ? "st_unknown_1" : "st_unknown_n"} vars={{ n }} /></StatusBadge>} />
      <h2 id={`t-${s.scheme_id}`} className="scheme-title"><Bi text={s.title} en={s.title_en} /></h2>
      <div className="question-row">
        <label htmlFor={id} className="question-text"><Bi text={field.question} en={field.question_en} /></label>
        <button type="button" className="round-btn round-btn-gold" aria-label={label("mic_label_off")} onClick={connectVoice}>
          <Icon name="mic" />
        </button>
      </div>
      <form className="inline-answer" onSubmit={(e) => {
        e.preventDefault();
        const v = parseInt(value, 10);
        if (Number.isFinite(v)) answerField(field.field, v, `${field.label}: ${v}`);
      }}>
        <input id={id} inputMode="numeric" pattern="[0-9]*" value={value} aria-label={`${field.label} · ${field.label_en}`}
          onChange={(e) => setValue(e.target.value.replace(/\D/g, ""))} placeholder={field.field === "annual_income" ? "120000" : "62"} />
        <button type="submit" className="btn btn-secondary" disabled={busy || !value}><Bi k="answer" /></button>
      </form>
    </article>
  );
}

export default function Schemes() {
  const { summary, lang, navigate } = useCase();
  const pick = useText();
  const schemes = summary?.schemes || [];
  const reviewing = summary?.pause?.type === "confirm" ? summary.pause.preview?.scheme_id : null;
  const profile = summary?.profile || [];
  const open = schemes.filter((s) => s.status === "eligible" && !s.app_id);
  const applied = schemes.filter((s) => s.app_id);
  const unknown = schemes.filter((s) => s.status === "unknown" && !s.app_id && s.missing_fields?.length);
  const notEligible = schemes.filter((s) => s.status === "not_eligible" && !s.app_id);
  const spoken = open.length
    ? [tr(lang, open.length === 1 ? "talk_result_1" : "talk_result", { n: open.length }), ...open.map((s) => pick(s.title, s.title_en))].join(". ")
    : "";

  return (
    <main className="screen schemes">
      <ScreenHeader k="schemes_title" back="talk" action={spoken ? <SpeakButton text={spoken} round /> : null} />
      {!profile.length ? (
        <Empty k="schemes_empty" action={<button type="button" className="btn btn-primary" onClick={() => navigate("talk")}><Bi k="go_talk" dual /></button>} />
      ) : (
        <>
          <p className="based-on">{tr(lang, "based_on", { facts: profile.map((p) => `${pick(p.label, p.label_en)} ${pick(p.text, p.text_en)}`).join(" · ") })}</p>
          {open.map((s, i) => (i === 0
            ? <FeaturedCard key={s.scheme_id} s={s} reviewing={reviewing} />
            : <CompactCard key={s.scheme_id} s={s} reviewing={reviewing} />))}
          {unknown.map((s) => <QuestionCard key={s.scheme_id} s={s} />)}
          {applied.map((s) => <CompactCard key={s.scheme_id} s={s} reviewing={reviewing} />)}
          {notEligible.length > 0 && (
            <details className="card not-eligible-group">
              <summary>
                <Bi k={notEligible.length === 1 ? "not_eligible_group_1" : "not_eligible_group"} vars={{ n: notEligible.length }} />
                <Icon name="chevronRight" className="chev" />
              </summary>
              {notEligible.map((s) => (
                <div key={s.scheme_id} className="ne-scheme">
                  <h3><Bi text={s.title} en={s.title_en} /></h3>
                  <WhyBox scheme={s} compact />
                  <RuleLink s={s} />
                </div>
              ))}
            </details>
          )}
        </>
      )}
    </main>
  );
}
