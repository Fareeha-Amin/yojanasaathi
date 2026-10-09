import { useEffect, useRef, useState } from "react";
import { Bi, MicButton, MicStatus, SpeakButton, Waveform, useT } from "../components.jsx";
import { useCase } from "../case.jsx";

function Chips({ profile }) {
  if (!profile?.length) return <p className="muted"><Bi k="known_empty" /></p>;
  return (
    <ul className="chips">
      {profile.map((p) => (
        <li key={p.field} className={`chip ${p.unsure ? "chip-unsure" : ""}`}>
          <span className="chip-label"><Bi text={p.label} en={p.label_en} /></span>
          <strong className="chip-value">{p.text}</strong>
          {p.unsure && <span className="chip-flag"><Bi k="unsure_chip" /></span>}
        </li>
      ))}
    </ul>
  );
}

function Bubble({ m }) {
  return (
    <li className={`bubble bubble-${m.from} ${m.error ? "bubble-error" : ""}`}>
      <span className="sr-only">{m.from === "user" ? "You:" : "Saathi:"}</span>
      <Bi text={m.text} en={m.en} block />
      <div className="bubble-meta">
        {m.from === "user" && m.via === "voice" && <span className="muted"><Bi k="via_voice" /></span>}
        {m.from === "agent" && !m.error && <SpeakButton text={m.text} label="replay" />}
      </div>
    </li>
  );
}

export default function Talk() {
  const { summary, messages, send, busy, voice, navigate } = useCase();
  const t = useT();
  const [text, setText] = useState("");
  const end = useRef(null);
  const asking = summary?.asking;
  const reviewWaiting = summary?.pause?.type === "confirm";

  useEffect(() => {
    end.current?.scrollIntoView?.({ block: "end", behavior: "smooth" });
  }, [messages.length, busy]);

  const submit = async (e) => {
    e.preventDefault();
    const value = text;
    if (!value.trim()) return;
    setText("");
    await send(value);
  };

  const showWelcome = messages.length === 0 && summary?.last_reply;

  return (
    <main className="screen talk">
      <section className={`mic-panel mic-panel-${voice.status}`} aria-label="Voice">
        <MicButton big />
        {voice.status !== "off" && voice.status !== "error" && <Waveform bars={9} />}
        <MicStatus />
        {voice.status === "off" && <p className="hint"><Bi k="talk_hint" block /></p>}
      </section>

      {reviewWaiting && (
        <button type="button" className="banner banner-action" onClick={() => navigate("review")}>
          <Bi k="review_ready" block />
          <span className="banner-cta"><Bi k="open_review" /> →</span>
        </button>
      )}

      <section className="card" aria-labelledby="known-h">
        <h2 id="known-h" className="h-sm"><Bi k="known_title" /></h2>
        <Chips profile={summary?.profile} />
      </section>

      {asking?.kind === "field" && (
        <section className="card question-card" aria-labelledby="q-h">
          <h2 id="q-h" className="h-sm"><Bi k="current_question" /></h2>
          <p className="question"><Bi text={asking.question} en={asking.question_en} block /></p>
          {asking.why && (
            <div className="why-note">
              <h3 className="why-title"><Bi k="why_title" /></h3>
              <p><Bi text={asking.why} en={asking.why_en} block /></p>
            </div>
          )}
        </section>
      )}

      <ol className="transcript" aria-live="polite" aria-label="Conversation">
        {showWelcome && (
          <li className="bubble bubble-agent">
            <span className="bubble-tag"><Bi k="welcome_back" /></span>
            <Bi text={summary.last_reply.text} en={summary.last_reply.en} block />
            <div className="bubble-meta"><SpeakButton text={summary.last_reply.text} label="replay" /></div>
          </li>
        )}
        {messages.map((m) => <Bubble key={m.id} m={m} />)}
        {busy && <li className="bubble bubble-agent bubble-busy" role="status"><Bi k="busy" /></li>}
        <li ref={end} aria-hidden="true" className="transcript-end" />
      </ol>

      {summary?.schemes?.some((s) => s.status === "eligible") && (
        <button type="button" className="btn btn-secondary" onClick={() => navigate("schemes")}>
          <Bi k="see_schemes" />
        </button>
      )}

      <form className="composer" onSubmit={submit}>
        <label htmlFor="msg" className="sr-only">{t("type_label")} · Type your answer</label>
        <input id="msg" value={text} onChange={(e) => setText(e.target.value)} autoComplete="off"
          placeholder={t("type_placeholder")} enterKeyHint="send" />
        <button type="submit" className="btn btn-primary" disabled={busy || !text.trim()}>
          <Bi k="send" />
        </button>
      </form>

      <aside className="card phone-card phone-card-sm">
        <Bi k="phone_title" block />
        <p className="phone-number"><Bi k="phone_number" /></p>
        <p className="muted small"><Bi k="phone_soon" block /></p>
      </aside>
    </main>
  );
}
