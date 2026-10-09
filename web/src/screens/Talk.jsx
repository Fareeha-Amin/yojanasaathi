// Talk (design page 1): "What I know so far" chips, the conversation (Saathi's English
// line under each message), "Why I ask" under the question, and the voice dock: keyboard,
// big marigold mic with a level ring, pause, and the state line.

import { useEffect, useRef, useState } from "react";
import { Bi, MicButton, MicStatus, Skeleton, SpeakButton, Waveform, useLabel } from "../components.jsx";
import { useCase } from "../case.jsx";
import { Icon } from "../icons.jsx";

const LIVE = new Set(["listening", "user", "thinking", "bot"]);

function KnownBar({ summary }) {
  const asking = summary?.asking;
  const profile = summary?.profile || [];
  return (
    <section className="known-bar" aria-labelledby="known-h">
      <h2 id="known-h" className="known-title"><Bi k="known_title" /></h2>
      <ul className="chips">
        {profile.map((p) => (
          <li key={p.field} className={`chip ${p.unsure ? "chip-unsure" : "chip-known"}`}>
            <Icon name={p.unsure ? "alert" : "check"} size={16} strokeWidth={2.5} />
            <Bi text={`${p.label} ${p.text}`} />
          </li>
        ))}
        {asking?.kind === "field" && (
          <li className="chip chip-asking"><Bi text={`${asking.label} ?`} /></li>
        )}
        {!profile.length && asking?.kind !== "field" && <li className="chip chip-empty"><Bi k="known_empty" /></li>}
      </ul>
    </section>
  );
}

function SaathiMessage({ text, en, tag }) {
  return (
    <li className="msg msg-agent">
      {tag && <span className="msg-tag"><Bi k={tag} /></span>}
      <div className="bubble"><span lang="und">{text}</span></div>
      <div className="msg-foot">
        {en && en !== text && <p className="msg-en" lang="en">{en}</p>}
        <SpeakButton text={text} label="replay" round />
      </div>
    </li>
  );
}

function UserMessage({ m }) {
  return (
    <li className="msg msg-user">
      <div className="bubble">{m.text}</div>
      {m.via === "voice" && <p className="msg-meta"><Icon name="mic" size={14} /> <Bi k="via_voice" /></p>}
    </li>
  );
}

function ResultCard({ summary }) {
  const { navigate } = useCase();
  const eligible = (summary?.schemes || []).filter((s) => s.status === "eligible" && !s.app_id);
  if (!eligible.length) return null;
  return (
    <li className="result-card">
      <span className="badge badge-ok"><Icon name="check" size={16} strokeWidth={2.5} />
        <Bi k={eligible.length === 1 ? "talk_result_1" : "talk_result"} vars={{ n: eligible.length }} />
      </span>
      <p className="result-titles">{eligible.slice(0, 2).map((s) => s.title).join(" · ")}</p>
      <button type="button" className="btn btn-secondary btn-sm" onClick={() => navigate("schemes")}>
        <Bi k="see_schemes" /> <Icon name="chevronRight" size={18} />
      </button>
    </li>
  );
}

export default function Talk() {
  const { summary, messages, send, busy, voice, navigate, disconnectVoice } = useCase();
  const label = useLabel();
  const live = LIVE.has(voice.status);
  const [typingPref, setTypingPref] = useState(null);
  const typing = typingPref ?? !live;
  const [text, setText] = useState("");
  const end = useRef(null);
  const asking = summary?.asking;
  const reviewWaiting = summary?.pause?.type === "confirm";

  useEffect(() => {
    end.current?.scrollIntoView?.({ block: "end", behavior: window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
  }, [messages.length, busy, typing]);

  const submit = async (e) => {
    e.preventDefault();
    const value = text;
    if (!value.trim()) return;
    setText("");
    await send(value);
  };

  const lastAgent = messages.reduce((i, m, idx) => (m.from === "agent" ? idx : i), -1);
  const showWelcome = messages.length === 0 && summary?.last_reply;
  const why = asking?.kind === "field" && asking.why ? (
    <li className="why-note">
      <h3 className="why-title"><Icon name="help" size={16} /> <Bi k="why_title" /></h3>
      <p>{asking.why}</p>
    </li>
  ) : null;

  return (
    <main className="talk">
      {summary ? <KnownBar summary={summary} /> : <div className="known-bar"><Skeleton rows={1} /></div>}

      {reviewWaiting && (
        <button type="button" className="banner banner-action" onClick={() => navigate("review")}>
          <Icon name="checkCircle" />
          <Bi k="review_ready" block />
          <span className="banner-cta"><Bi k="open_review" /> <Icon name="chevronRight" size={18} /></span>
        </button>
      )}

      <ol className="transcript" aria-live="polite" aria-label="Conversation">
        {!summary && <li><Skeleton rows={2} /></li>}
        {summary && messages.length === 0 && !showWelcome && (
          <li className="talk-empty">
            <p><Bi k="talk_empty" block /></p>
            <p className="muted"><Bi k="talk_hint" /></p>
          </li>
        )}
        {showWelcome && <SaathiMessage text={summary.last_reply.text} en={summary.last_reply.en} tag="welcome_back" />}
        {showWelcome && why}
        {messages.map((m) => (
          m.from === "agent"
            ? <SaathiMessage key={m.id} text={m.text} en={m.error ? null : m.en} />
            : <UserMessage key={m.id} m={m} />
        ))}
        {messages.length > 0 && lastAgent === messages.length - 1 && why}
        {!reviewWaiting && summary && <ResultCard summary={summary} />}
        {busy && (
          <li className="msg msg-agent" role="status">
            <div className="bubble bubble-busy"><span className="dots" aria-hidden="true"><i /><i /><i /></span> <Bi k="busy" /></div>
          </li>
        )}
        <li ref={end} aria-hidden="true" className="transcript-end" />
      </ol>

      <div className="talk-dock">
        <Waveform bars={11} />
        <div className="mic-row">
          <button type="button" className={`round-btn round-btn-lg ${typing ? "on" : ""}`} aria-pressed={typing}
            aria-label={label("type_instead")} onClick={() => setTypingPref(!typing)}>
            <Icon name="keyboard" size={26} />
          </button>
          <MicButton />
          <button type="button" className="round-btn round-btn-lg" aria-label={label("pause_voice")}
            disabled={!live} onClick={disconnectVoice}>
            <Icon name="pause" size={24} />
          </button>
        </div>
        <MicStatus long />
        {typing && (
          <form className="composer" onSubmit={submit}>
            <label htmlFor="msg" className="sr-only">{label("type_label")}</label>
            <input id="msg" value={text} onChange={(e) => setText(e.target.value)} autoComplete="off"
              placeholder={label("type_placeholder").split(" · ")[0]} enterKeyHint="send" />
            <button type="submit" className="btn btn-primary" disabled={busy || !text.trim()} aria-label={label("send")}>
              <Icon name="chevronRight" size={24} />
            </button>
          </form>
        )}
      </div>
    </main>
  );
}
