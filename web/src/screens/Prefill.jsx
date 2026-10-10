// Preparing your application (design page 4), driven by real pause data. Phase 4 (browser
// agent) is not built yet, so until it sends progress the step list is a placeholder.
//   pause {type: "otp", masked_phone?}  -> "Your turn: enter the OTP" (typed or said; resumes /turn)
//   pause {type: "safe_stop", message?, screenshot?}  -> stop + hand-over card
//   summary.progress or ui {type: "progress", steps: [{key, label, label_en, status, screenshot}]}
//   status: "waiting" | "running" | "done". The phone is shown exactly as the portal masks it.

import { useState } from "react";
import { Bi, ScreenHeader, useLabel, useText } from "../components.jsx";
import { useCase } from "../case.jsx";
import { tr } from "../i18n.js";
import { Icon } from "../icons.jsx";

const PLAN = ["step_open", "step_login", "step_personal", "step_bank", "step_docs", "step_stop"];
const OTP_LEN = 6;

function Steps({ steps, paused }) {
  return (
    <section className="card steps-card" aria-labelledby="steps-h">
      <div className="card-top">
        <h2 id="steps-h" className="steps-title"><Bi k="on_demo_portal" /></h2>
        <span className={`pill ${paused ? "pill-amber" : "pill-grey"}`}><Bi k={paused ? "paused" : "not_connected"} /></span>
      </div>
      <ol className="agent-steps">
        {steps.map((s, i) => (
          <li key={s.key || i} className={`agent-step agent-step-${s.status}`}>
            <span className="step-dot" aria-hidden="true">{s.status === "done" && <Icon name="check" size={18} strokeWidth={3} />}</span>
            <span className="step-text">
              {s.k ? <Bi k={s.k} /> : <Bi text={s.label} en={s.label_en} />}
              <span className="sr-only"> · {s.status}</span>
            </span>
            {s.screenshot && <img src={s.screenshot} alt={`Screenshot: ${s.label_en || s.label || ""}`} className="shot" />}
          </li>
        ))}
      </ol>
    </section>
  );
}

function OtpCard({ pause, code, setCode }) {
  const { connectVoice, lang } = useCase();
  const label = useLabel();
  return (
    <section className="card otp-card" aria-labelledby="otp-h">
      <div className="otp-head">
        <span className="otp-icon"><Icon name="shield" /></span>
        <div>
          <h2 id="otp-h"><Bi k="otp_title" /></h2>
          <p className="muted">
            {pause.masked_phone ? tr(lang, "otp_body_phone", { phone: pause.masked_phone }) : <Bi k="otp_body" />}
          </p>
        </div>
      </div>
      <label htmlFor="otp" className="sr-only">{label("otp_label")}</label>
      <div className="otp-boxes">
        <input id="otp" inputMode="numeric" autoComplete="one-time-code" maxLength={8} value={code}
          onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))} className="otp-input" />
        {Array.from({ length: OTP_LEN }, (_, i) => (
          <span key={i} aria-hidden="true" className={`otp-box ${i < code.length ? "filled" : ""} ${i === code.length ? "active" : ""}`}>
            {code[i] || ""}
          </span>
        ))}
      </div>
      <button type="button" className="btn btn-gold btn-block" onClick={connectVoice}>
        <Icon name="mic" /> <Bi k="say_code" />
      </button>
    </section>
  );
}

export default function Prefill() {
  const { summary, navigate, send, busy } = useCase();
  const pick = useText();
  const [code, setCode] = useState("");
  const pause = summary?.pause;
  const progress = summary?.progress?.steps;
  const steps = progress?.length ? progress : PLAN.map((k) => ({ key: k, k, status: "waiting" }));
  const otp = pause?.type === "otp";
  const scheme = (summary?.schemes || []).find((s) => s.scheme_id === summary?.selected);

  const sendCode = () => {
    if (code.length < 4) return;
    send(code, { shown: "••••••" });
    setCode("");
  };

  return (
    <main className="screen prefill">
      <ScreenHeader k="prefill_title" back="talk" eyebrow={scheme ? pick(scheme.title, scheme.title_en) : null} />
      {pause?.type === "safe_stop" && (
        <section className="card safe-stop" role="alert" aria-labelledby="ss-h">
          <h2 id="ss-h"><Icon name="alert" /> <Bi k="safe_stop_title" /></h2>
          <p><Bi k="safe_stop_body" /></p>
          {pause.message && <p className="muted">{pause.message}</p>}
          {pause.screenshot && <img src={pause.screenshot} alt="Screenshot of the portal where I stopped" className="shot" />}
        </section>
      )}
      <Steps steps={steps} paused={otp || pause?.type === "safe_stop"} />
      {!progress?.length && !otp && <p className="placeholder"><Bi k="prefill_placeholder" /></p>}
      {otp && <OtpCard pause={pause} code={code} setCode={setCode} />}
      <p className="note-lock"><Icon name="lock" size={20} /> <Bi k="otp_never" /></p>
      <div className="sticky-cta sticky-cta-two">
        <button type="button" className="btn btn-secondary btn-lg" onClick={() => navigate("talk")}>
          <Bi k="finish_later" />
        </button>
        <button type="button" className="btn btn-primary btn-lg" disabled={!otp || busy || code.length < 4} onClick={sendCode}>
          <Bi k="continue" dual />
        </button>
      </div>
    </main>
  );
}
