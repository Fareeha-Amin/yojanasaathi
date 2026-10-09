// Pre-fill + OTP. Phase 4 (browser agent) is not built yet, so the step list is a
// placeholder until the agent sends real progress. What this screen already renders:
//   pause {type: "otp", ...}        -> "Your turn: enter the OTP" (typed or spoken; resumes /turn)
//   pause {type: "safe_stop", ...}  -> stop + hand-over card (message / step / screenshot if given)
//   summary.progress or ui {type: "progress", steps: [{key, label?, label_en?, status, screenshot?}]}
// `status` is "waiting" | "running" | "done"; `screenshot` is a URL the agent serves.

import { useState } from "react";
import { Bi, useT } from "../components.jsx";
import { useCase } from "../case.jsx";

const PLAN = ["step_open", "step_login", "step_personal", "step_bank", "step_docs", "step_stop"];

function Steps({ steps }) {
  return (
    <ol className="agent-steps">
      {steps.map((s, i) => (
        <li key={s.key || i} className={`agent-step agent-step-${s.status}`}>
          <span className="step-dot" aria-hidden="true">{s.status === "done" ? "✓" : i + 1}</span>
          <div>
            {s.k ? <Bi k={s.k} block /> : <Bi text={s.label} en={s.label_en} block />}
            <span className="muted small"><Bi k={`step_${s.status}`} /></span>
            {s.screenshot && <img src={s.screenshot} alt={`Screenshot: ${s.label_en || s.label || ""}`} className="shot" />}
          </div>
        </li>
      ))}
    </ol>
  );
}

function OtpCard({ pause }) {
  const { send, busy } = useCase();
  const t = useT();
  const [code, setCode] = useState("");
  return (
    <section className="card otp-card" aria-labelledby="otp-h">
      <h2 id="otp-h"><Bi k="otp_title" block /></h2>
      <p><Bi k="otp_body" block /></p>
      {pause.masked_phone && <p className="muted">{pause.masked_phone}</p>}
      <form className="row" onSubmit={(e) => {
        e.preventDefault();
        if (code) send(code, { shown: "••••••" });
        setCode("");
      }}>
        <label htmlFor="otp" className="sr-only">{t("otp_label")} · OTP code</label>
        <input id="otp" inputMode="numeric" autoComplete="one-time-code" maxLength={8} value={code}
          onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))} className="otp-input" placeholder="• • • • • •" />
        <button type="submit" className="btn btn-primary" disabled={busy || code.length < 4}><Bi k="otp_send" /></button>
      </form>
      <p className="note-lock"><Bi k="otp_never" /></p>
    </section>
  );
}

export default function Prefill() {
  const { summary, navigate } = useCase();
  const pause = summary?.pause;
  const progress = summary?.progress?.steps;
  const steps = progress?.length ? progress : PLAN.map((k) => ({ key: k, k, status: "waiting" }));
  return (
    <main className="screen">
      <h1><Bi k="prefill_title" block /></h1>
      {pause?.type === "otp" && <OtpCard pause={pause} />}
      {pause?.type === "safe_stop" && (
        <section className="card safe-stop" role="alert" aria-labelledby="ss-h">
          <h2 id="ss-h"><Bi k="safe_stop_title" block /></h2>
          <p><Bi k="safe_stop_body" block /></p>
          {pause.message && <p className="muted">{pause.message}</p>}
          {pause.screenshot && <img src={pause.screenshot} alt="Screenshot of the portal where I stopped" className="shot" />}
        </section>
      )}
      {!progress?.length && (
        <p className="placeholder"><Bi k="prefill_placeholder" block /></p>
      )}
      <Steps steps={steps} />
      <button type="button" className="btn btn-secondary" onClick={() => navigate("talk")}>
        <Bi k="finish_later" />
      </button>
    </main>
  );
}
