// Shared UI pieces: bilingual text, header, navigation, mic button, voice dock.

import { useEffect, useRef } from "react";
import { useCase } from "./case.jsx";
import { LANGS, LANG_NAMES, LANG_SHORT, tr } from "./i18n.js";

/**
 * Kannada / Hindi first, English underneath (design rule). Either a UI string key `k`
 * (+ `vars`; `envars` when a value differs in English, e.g. a scheme title), or text from
 * the agent: `text` in the case language + `en`.
 */
export function Bi({ k, vars, envars, text, en, className = "", block = false }) {
  const { lang } = useCase();
  const local = k ? tr(lang, k, vars) : text;
  const english = k ? tr("en", k, envars ?? vars) : en;
  // Always a <span> (valid inside <p>, <button>, <label>); `block` only changes display.
  return (
    <span className={`bi ${block ? "bi-block" : ""} ${className}`}>
      <span className="bi-main" lang={lang}>{local}</span>
      {lang !== "en" && english && english !== local && (
        <span className="bi-en" lang="en">{english}</span>
      )}
    </span>
  );
}

/** Plain string in the UI language (aria-labels, placeholders). */
export function useT() {
  const { lang } = useCase();
  return (k, vars) => tr(lang, k, vars);
}

export function Logo({ size = 40 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 64 64" aria-hidden="true" className="logo">
      <rect width="64" height="64" rx="16" fill="#0B5D4B" />
      <path d="M17 20h30a5 5 0 0 1 5 5v14a5 5 0 0 1-5 5H31l-9 8v-8h-5a5 5 0 0 1-5-5V25a5 5 0 0 1 5-5z" fill="#fff" />
      <circle cx="25" cy="32" r="3" fill="#0B5D4B" />
      <circle cx="32" cy="32" r="3" fill="#F2B21B" />
      <circle cx="39" cy="32" r="3" fill="#0B5D4B" />
    </svg>
  );
}

export function LangSwitch() {
  const { lang, setLang } = useCase();
  const t = useT();
  return (
    <div className="lang-switch" role="group" aria-label={`${t("lang_switch")} · Language`}>
      {LANGS.map((l) => (
        <button key={l} type="button" lang={l} aria-pressed={lang === l} aria-label={LANG_NAMES[l]}
          className={lang === l ? "on" : ""} onClick={() => setLang(l)}>
          {LANG_SHORT[l]}
        </button>
      ))}
    </div>
  );
}

export function Header() {
  const { navigate, route } = useCase();
  const t = useT();
  return (
    <header className="topbar">
      <a href="#/" className="brand" onClick={(e) => { e.preventDefault(); navigate(""); }}>
        <Logo size={36} />
        <span className="brand-name">
          <span className="brand-local">{t("app_name")}</span>
          <span className="brand-en" lang="en">YojanaSaathi</span>
        </span>
      </a>
      <div className="topbar-right">
        <LangSwitch />
        {route !== "" && (
          <a href="#/profile" className={`icon-btn ${route === "profile" ? "on" : ""}`}
            aria-label={`${t("nav_profile")} · Privacy`}
            onClick={(e) => { e.preventDefault(); navigate("profile"); }}>
            <svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true">
              <path d="M12 2 4 5v6c0 5 3.4 9.4 8 11 4.6-1.6 8-6 8-11V5l-8-3z" fill="none" stroke="currentColor" strokeWidth="2" strokeLinejoin="round" />
              <path d="m8.5 12 2.5 2.5 4.5-5" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          </a>
        )}
      </div>
    </header>
  );
}

export function DemoStrip() {
  return (
    <div className="demo-strip" role="note">
      <Bi k="demo_label" />
    </div>
  );
}

const NAV = [
  ["talk", "nav_talk", "M12 3a3 3 0 0 0-3 3v6a3 3 0 0 0 6 0V6a3 3 0 0 0-3-3zM5 11a7 7 0 0 0 14 0M12 18v3"],
  ["schemes", "nav_schemes", "M4 5h16M4 12h16M4 19h10"],
  ["documents", "nav_documents", "M7 3h7l5 5v13H7zM14 3v5h5"],
  ["review", "nav_review", "M5 12l4 4L19 6"],
  ["applications", "nav_applications", "M4 6h16v12H4zM4 10h16"],
];

export function BottomNav() {
  const { route, navigate, summary } = useCase();
  const t = useT();
  const reviewWaiting = summary?.pause?.type === "confirm";
  return (
    <nav className="bottomnav" aria-label={`${t("nav_main")} · Main`}>
      {NAV.map(([r, k, d]) => (
        <a key={r} href={`#/${r}`} aria-current={route === r ? "page" : undefined}
          className={route === r ? "on" : ""}
          onClick={(e) => { e.preventDefault(); navigate(r); }}>
          <span className="nav-icon">
            <svg viewBox="0 0 24 24" width="24" height="24" aria-hidden="true">
              <path d={d} fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
            {r === "review" && reviewWaiting && <span className="dot" aria-hidden="true" />}
          </span>
          <Bi k={k} className="nav-label" />
        </a>
      ))}
    </nav>
  );
}

const MIC_TEXT = {
  off: "mic_connect", connecting: "mic_connecting", listening: "mic_listening", user: "mic_user",
  thinking: "mic_thinking", bot: "mic_bot",
};

/** The i18n key for a voice state; an error names its reason (mic_err_blocked, ...). */
export function micText(status, reason) {
  if (status === "error") return `mic_err_${reason || "failed"}`;
  return MIC_TEXT[status] || "mic_connect";
}

/** Live level bars from the mic (reads a ref every frame: no React re-render per sample). */
export function Waveform({ bars = 7 }) {
  const { levelRef, voice } = useCase();
  const el = useRef(null);
  useEffect(() => {
    let raf = 0;
    const tick = () => {
      const node = el.current;
      if (node) {
        const active = voice.status === "user" || voice.status === "listening";
        const level = active ? Math.min(1, (levelRef?.current || 0) * 4) : voice.status === "bot" ? 0.5 : 0;
        [...node.children].forEach((b, i) => {
          const wobble = 0.55 + 0.45 * Math.abs(Math.sin(Date.now() / 160 + i * 1.3));
          b.style.transform = `scaleY(${0.15 + level * wobble})`;
        });
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [levelRef, voice.status]);
  return (
    <div className="waveform" ref={el} aria-hidden="true">
      {Array.from({ length: bars }, (_, i) => <span key={i} />)}
    </div>
  );
}

export function MicButton({ big = false }) {
  const { voice, connectVoice, disconnectVoice } = useCase();
  const t = useT();
  const on = !["off", "error"].includes(voice.status);
  return (
    <button type="button" className={`mic ${big ? "mic-big" : ""} mic-${voice.status}`}
      aria-pressed={on}
      aria-label={on ? `${t("mic_label_on")} · ${tr("en", "mic_label_on")}` : `${t("mic_label_off")} · ${tr("en", "mic_label_off")}`}
      onClick={() => (on ? disconnectVoice() : connectVoice())}>
      <svg viewBox="0 0 24 24" width={big ? 48 : 28} height={big ? 48 : 28} aria-hidden="true">
        {on && voice.status !== "connecting" ? (
          <rect x="7" y="7" width="10" height="10" rx="2" fill="currentColor" />
        ) : (
          <path d="M12 3a3 3 0 0 0-3 3v6a3 3 0 0 0 6 0V6a3 3 0 0 0-3-3zM5 11a7 7 0 0 0 14 0M12 18v3"
            fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
        )}
      </svg>
    </button>
  );
}

export function MicStatus() {
  const { voice } = useCase();
  return (
    <div className={`mic-status mic-status-${voice.status}`} role="status" aria-live="polite">
      <Bi k={micText(voice.status, voice.reason)} block />
    </div>
  );
}

/** Autoplay was blocked: one tap plays Saathi's voice (bot audio or read-aloud). */
export function TapToHear() {
  const { audioBlocked, resumeAudio } = useCase();
  if (!audioBlocked) return null;
  return (
    <button type="button" className="btn btn-primary tap-to-hear" onClick={resumeAudio}>
      <svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true">
        <path d="M4 9v6h4l5 4V5L8 9H4zM16 8.5a5 5 0 0 1 0 7M18.5 6a8.5 8.5 0 0 1 0 12" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
      <Bi k="tap_to_hear" />
    </button>
  );
}

/** On every screen but Talk: keep talking (e.g. say "ಹೌದು" on the review screen). */
export function VoiceDock() {
  const { voice, messages, summary, navigate } = useCase();
  const t = useT();
  const last = [...messages].reverse().find((m) => m.from === "agent");
  const caption = last ? { text: last.text, en: last.en } : summary?.last_reply ? { text: summary.last_reply.text, en: summary.last_reply.en } : null;
  return (
    <aside className="voice-dock" aria-label="Voice">
      <MicButton />
      <div className="dock-body">
        <div className="dock-state">
          {voice.status !== "off" && <Waveform bars={5} />}
          <Bi k={micText(voice.status, voice.reason)} />
        </div>
        {caption && (
          <p className="dock-caption" aria-live="polite">
            <Bi text={caption.text} en={caption.en} />
          </p>
        )}
      </div>
      <button type="button" className="icon-btn" aria-label={`${t("type_instead")} · Type instead`}
        onClick={() => navigate("talk")}>
        <svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true">
          <rect x="3" y="6" width="18" height="12" rx="2" fill="none" stroke="currentColor" strokeWidth="2" />
          <path d="M7 10h.01M11 10h.01M15 10h.01M7 14h10" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
        </svg>
      </button>
    </aside>
  );
}

export function SpeakButton({ text, label = "read_aloud" }) {
  const { speak } = useCase();
  const t = useT();
  return (
    <button type="button" className="btn btn-ghost btn-sm" onClick={() => speak(text)}
      aria-label={`${t(label)} · ${tr("en", label)}`}>
      <svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true">
        <path d="M4 9v6h4l5 4V5L8 9H4zM16 8.5a5 5 0 0 1 0 7M18.5 6a8.5 8.5 0 0 1 0 12" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
      <Bi k={label} />
    </button>
  );
}

export function StatusBadge({ kind, children }) {
  return <span className={`badge badge-${kind}`}>{children}</span>;
}

export function Empty({ k, children }) {
  return (
    <div className="empty">
      <Bi k={k} block />
      {children}
    </div>
  );
}
