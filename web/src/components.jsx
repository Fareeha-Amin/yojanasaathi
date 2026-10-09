// Shared UI pieces: bilingual text, header, navigation, mic, toast, screen header.

import { useEffect, useRef, useState } from "react";
import { useCase } from "./case.jsx";
import { LANGS, LANG_NAMES, tr } from "./i18n.js";
import { Icon } from "./icons.jsx";

/**
 * Text in the selected language. `dual` adds the English line underneath (only for page
 * titles, primary buttons and Saathi's messages; none when English is selected). Either a
 * UI string key `k` (+ `vars`; `envars` when a value differs in English, e.g. a scheme
 * title), or text from the agent: `text` in the case language + `en`.
 */
export function Bi({ k, vars, envars, text, en, className = "", block = false, dual = false }) {
  const { lang } = useCase();
  const local = k ? tr(lang, k, vars) : text;
  const english = k ? tr("en", k, envars ?? vars) : en;
  const showEn = dual && lang !== "en" && english && english !== local;
  if (!showEn && !block) return <span className={className || undefined} lang={lang}>{local}</span>;
  return (
    <span className={`bi ${block ? "bi-block" : ""} ${className}`}>
      <span className="bi-main" lang={lang}>{local}</span>
      {showEn && <span className="bi-en" lang="en">{english}</span>}
    </span>
  );
}

/** Plain string in the UI language (aria-labels, placeholders). */
export function useT() {
  const { lang } = useCase();
  return (k, vars) => tr(lang, k, vars);
}

/** aria-label: the UI language, plus English when that differs. */
export function useLabel() {
  const { lang } = useCase();
  return (k, vars) => (lang === "en" ? tr("en", k, vars) : `${tr(lang, k, vars)} · ${tr("en", k, vars)}`);
}

export function Logo({ size = 40 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 64 64" aria-hidden="true" className="logo">
      <rect width="64" height="64" rx="16" fill="#0B5D4B" />
      <path d="M32 16c-9.4 0-17 6.5-17 14.6 0 4.2 2 8 5.3 10.6L19 48l7.6-3.7c1.7.5 3.5.7 5.4.7 9.4 0 17-6.5 17-14.6S41.4 16 32 16Z"
        fill="none" stroke="#F2B21B" strokeWidth="4.5" strokeLinejoin="round" />
    </svg>
  );
}

export function LangSelect({ id = "lang" }) {
  const { lang, setLang } = useCase();
  return (
    <label className="lang-select" htmlFor={id}>
      <span className="sr-only">{tr(lang, "lang_switch")} · Language</span>
      <select id={id} value={lang} onChange={(e) => setLang(e.target.value)}>
        {LANGS.map((l) => <option key={l} value={l} lang={l}>{LANG_NAMES[l]}</option>)}
      </select>
      <Icon name="chevronDown" size={18} />
    </label>
  );
}

export function Header() {
  const { navigate } = useCase();
  const label = useLabel();
  return (
    <header className="topbar">
      <a href="#/" className="brand" onClick={(e) => { e.preventDefault(); navigate(""); }}>
        <Logo size={40} />
        <span className="brand-name">YojanaSaathi</span>
        <span className="demo-tag-sm" title="Demo project, not a government website">{label("demo_short")}</span>
      </a>
      <LangSelect />
    </header>
  );
}

const NAV = [
  ["talk", "nav_talk", "mic", ["talk", "schemes", "review", "prefill"]],
  ["applications", "nav_applications", "folder", ["applications"]],
  ["documents", "nav_documents", "file", ["documents"]],
  ["profile", "nav_profile", "user", ["profile"]],
];

export function BottomNav() {
  const { route, navigate, summary } = useCase();
  const label = useLabel();
  const reviewWaiting = summary?.pause?.type === "confirm";
  return (
    <nav className="bottomnav" aria-label={label("nav_main")}>
      {NAV.map(([r, k, icon, owns]) => {
        const on = owns.includes(route);
        return (
          <a key={r} href={`#/${r}`} aria-current={on ? "page" : undefined} className={on ? "on" : ""}
            onClick={(e) => { e.preventDefault(); navigate(r); }}>
            <span className="nav-icon">
              <Icon name={icon} size={26} />
              {r === "talk" && reviewWaiting && <span className="dot" aria-hidden="true" />}
            </span>
            <Bi k={k} className="nav-label" />
          </a>
        );
      })}
    </nav>
  );
}

/** A page header: back arrow, title (+ English underneath), an optional action. */
export function ScreenHeader({ k, title, en, sub, back, action, eyebrow }) {
  const { navigate } = useCase();
  const label = useLabel();
  return (
    <header className="screen-head">
      {back && (
        <button type="button" className="round-btn" aria-label={label("back")} onClick={() => navigate(back)}>
          <Icon name="arrowLeft" />
        </button>
      )}
      <div className="screen-title">
        {eyebrow && <p className="eyebrow">{eyebrow}</p>}
        <h1>{k ? <Bi k={k} dual block /> : <Bi text={title} en={en} dual block />}</h1>
        {sub && <p className="screen-sub">{sub}</p>}
      </div>
      {action}
    </header>
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

const LIVE = new Set(["listening", "user", "thinking", "bot"]);

/** Reads the mic level from a ref each frame (no re-render per sample); sets --level. */
function useLevel(el) {
  const { levelRef, voice } = useCase();
  useEffect(() => {
    let raf = 0;
    let smooth = 0;
    const tick = () => {
      const target = voice.status === "user" || voice.status === "listening" ? Math.min(1, (levelRef?.current || 0) * 5)
        : voice.status === "bot" ? 0.35 + 0.25 * Math.abs(Math.sin(Date.now() / 220)) : 0;
      smooth += (target - smooth) * 0.3;
      el.current?.style.setProperty("--level", smooth.toFixed(3));
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [levelRef, voice.status, el]);
}

/** Bars driven by the audio level (--level on the wrapper). */
export function Waveform({ bars = 9 }) {
  const ref = useRef(null);
  useLevel(ref);
  return (
    <div className="waveform" ref={ref} aria-hidden="true">
      {Array.from({ length: bars }, (_, i) => (
        <span key={i} style={{ "--i": Math.abs(i - (bars - 1) / 2) / ((bars - 1) / 2) }} />
      ))}
    </div>
  );
}

export function MicButton({ size = "big" }) {
  const { voice, connectVoice, disconnectVoice } = useCase();
  const label = useLabel();
  const ref = useRef(null);
  useLevel(ref);
  const on = LIVE.has(voice.status) || voice.status === "connecting";
  return (
    <span className={`mic-wrap mic-wrap-${size}`} ref={ref}>
      <span className="mic-ring" aria-hidden="true" />
      <button type="button" className={`mic mic-${size} mic-${voice.status}`} aria-pressed={on}
        aria-label={on ? label("mic_label_on") : label("mic_label_off")}
        onClick={() => (on ? disconnectVoice() : connectVoice())}>
        <Icon name={on && voice.status !== "connecting" ? "stop" : "mic"} size={size === "big" ? 40 : 26} />
      </button>
    </span>
  );
}

export function MicStatus({ long = false }) {
  const { voice } = useCase();
  const key = long && voice.status === "listening" ? "mic_listening_long" : micText(voice.status, voice.reason);
  return (
    <div className={`mic-status mic-status-${voice.status}`} role="status" aria-live="polite">
      <Bi k={key} />
    </div>
  );
}

/** Autoplay was blocked: one tap plays Saathi's voice (bot audio or read-aloud). */
export function TapToHear() {
  const { audioBlocked, resumeAudio } = useCase();
  if (!audioBlocked) return null;
  return (
    <button type="button" className="btn btn-primary tap-to-hear" onClick={resumeAudio}>
      <Icon name="volume" />
      <Bi k="tap_to_hear" dual />
    </button>
  );
}

const TOAST_MS = 6000;

/** Off the Talk screen: a 56px mic above the nav, and Saathi's last reply as a one-line
 * toast (tap to expand; hides after 6 s). */
export function FloatingVoice() {
  const { messages } = useCase();
  const label = useLabel();
  const last = [...messages].reverse().find((m) => m.from === "agent");
  const [shown, setShown] = useState(null);
  const [open, setOpen] = useState(false);
  const timer = useRef(null);
  const seen = useRef(last?.id);

  useEffect(() => {
    if (!last || last.id === seen.current) return undefined;
    seen.current = last.id;
    setShown(last);
    setOpen(false);
    clearTimeout(timer.current);
    timer.current = setTimeout(() => setShown(null), TOAST_MS);
    return undefined;
  }, [last?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => () => clearTimeout(timer.current), []);

  const expand = () => {
    clearTimeout(timer.current);
    setOpen((o) => !o);
  };

  return (
    <div className="floating-voice">
      {shown && (
        <div className={`toast ${open ? "toast-open" : ""}`} role="status" aria-live="polite">
          <button type="button" className="toast-body" onClick={expand} aria-expanded={open}>
            <span className="toast-who">{label("saathi")}</span>
            <Bi text={shown.text} en={shown.en} dual={open} block />
          </button>
          <button type="button" className="toast-close" aria-label={label("close")} onClick={() => setShown(null)}>
            <Icon name="x" size={18} />
          </button>
        </div>
      )}
      <MicButton size="float" />
    </div>
  );
}

export function SpeakButton({ text, label: k = "read_aloud", round = false }) {
  const { speak } = useCase();
  const label = useLabel();
  if (round) {
    return (
      <button type="button" className="round-btn round-btn-tint" onClick={() => speak(text)} aria-label={label(k)}>
        <Icon name="volume" />
      </button>
    );
  }
  return (
    <button type="button" className="btn btn-ghost btn-sm" onClick={() => speak(text)} aria-label={label(k)}>
      <Icon name="volume" size={20} />
      <Bi k={k} />
    </button>
  );
}

export function StatusBadge({ kind, icon, children }) {
  return (
    <span className={`badge badge-${kind}`}>
      {icon && <Icon name={icon} size={16} strokeWidth={2.5} />}
      {children}
    </span>
  );
}

export function Empty({ k, action, icon = "sparkle" }) {
  return (
    <div className="empty">
      <span className="empty-icon"><Icon name={icon} size={28} /></span>
      <Bi k={k} block />
      {action}
    </div>
  );
}

/** Placeholder blocks while the summary loads. */
export function Skeleton({ rows = 3 }) {
  return (
    <div className="skeleton" aria-busy="true" aria-label="Loading">
      {Array.from({ length: rows }, (_, i) => <span key={i} className="sk-block" />)}
    </div>
  );
}
