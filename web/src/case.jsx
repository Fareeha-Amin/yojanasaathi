// The case this browser works on: session, screen summary, transcript, voice, and every
// action the screens can take. Screens read it with useCase(); tests provide their own value.
//
// One case, two channels: typed text goes to POST /turn; voice goes through the Pipecat bot,
// which POSTs /turn for the same case and sends the result back as an RTVI "turn" message.
// Both end in applyTurn(): add the bubbles, open the screen the turn calls for (confirm ->
// Review, otp / safe_stop -> Pre-fill, submitted -> My applications, eligibility -> Schemes),
// and reload the summary (the screens' source of truth).
// Speech: with voice on, the bot speaks (its own replies, plus "speak" for web-side ones);
// with voice off, typed-turn replies (setting "Speak replies", default on), "Read aloud" and
// "Replay" use POST /tts (Bulbul, same voice), browser speech only as the last fallback.

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import * as defaultApi from "./api.js";
import { maskAadhaar } from "./format.js";
import { LANGS, scriptLang, tr } from "./i18n.js";
import { browserSpeak, speakViaTts, stopSpeech } from "./speech.js";

export const CaseContext = createContext(null);
export const useCase = () => useContext(CaseContext);

export const ROUTES = ["", "talk", "schemes", "documents", "prefill", "review", "applications", "profile"];
const LANG_KEY = "ys.lang";
const SPEAK_KEY = "ys.speakReplies";
const THINKING_TIMEOUT_MS = 12000; // speech the STT dropped: stop showing "thinking"
const DEDUPE_MS = 20000; // the same Saathi line twice within this window is shown once
const LIVE = new Set(["listening", "user", "thinking", "bot"]);

export function routeFromHash(hash) {
  const r = (hash || "").replace(/^#\/?/, "").split(/[?/]/)[0];
  return ROUTES.includes(r) ? r : "";
}

/** The screen a turn's result belongs on (null = stay). Phase 4 adds ui.type "progress". */
export function routeForTurn(res) {
  const p = res?.pause?.type;
  if (p === "confirm") return "review";
  if (p === "otp" || p === "safe_stop") return "prefill";
  const u = res?.ui?.type;
  if (u === "submitted") return "applications";
  if (u === "progress") return "prefill";
  if (u === "eligibility") return "schemes";
  if (u === "form") return "talk"; // the portal's form questions are asked in the conversation
  return null;
}

/** Text the agent's parsers understand for an inline answer on the Schemes screen. */
export function answerText(field, value) {
  return field === "age" ? `I am ${value} years old` : `my annual income is ${value}`;
}

function storedSpeakReplies() {
  try {
    return localStorage.getItem(SPEAK_KEY) !== "0";
  } catch {
    return true;
  }
}

function storedLang() {
  try {
    const l = localStorage.getItem(LANG_KEY);
    return LANGS.includes(l) ? l : null;
  } catch {
    return null;
  }
}

// The Pipecat client is ~500 kB: load it on the first mic tap, not with the app.
function lazyCreateVoice(opts) {
  let v = null;
  const load = async () => (v ??= (await import("./voice.js")).createVoice(opts));
  return {
    async connect() {
      await (await load()).connect();
    },
    speak(text) {
      return v ? v.speak(text) : false;
    },
    async resumeAudio() {
      if (v) await v.resumeAudio();
    },
    async disconnect() {
      if (v) await v.disconnect();
    },
  };
}

export function CaseProvider({ children, api = defaultApi, createVoice = lazyCreateVoice }) {
  const [lang, setLangState] = useState(() => storedLang() ?? "kn");
  const [session, setSession] = useState(null);
  const [summary, setSummary] = useState(null);
  const [messages, setMessages] = useState([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [notice, setNotice] = useState(null);
  const [route, setRoute] = useState(() => routeFromHash(window.location.hash));
  const [voice, setVoice] = useState({ status: "off" });
  const [installEvent, setInstallEvent] = useState(null);
  const [audioBlocked, setAudioBlocked] = useState(null); // () => play(), from a tap
  const [speakReplies, setSpeakRepliesState] = useState(storedSpeakReplies);

  const sessionRef = useRef(null);
  const langRef = useRef(lang);
  const busyRef = useRef(false);
  const voiceRef = useRef(null);
  const levelRef = useRef(0);
  const voiceStatusRef = useRef("off");
  const idRef = useRef(0);
  const thinkingTimer = useRef(null);
  const speakRepliesRef = useRef(speakReplies);

  useEffect(() => {
    const onHash = () => setRoute(routeFromHash(window.location.hash));
    window.addEventListener("hashchange", onHash);
    const onInstall = (e) => {
      e.preventDefault();
      setInstallEvent(e);
    };
    window.addEventListener("beforeinstallprompt", onInstall);
    return () => {
      window.removeEventListener("hashchange", onHash);
      window.removeEventListener("beforeinstallprompt", onInstall);
    };
  }, []);

  useEffect(() => {
    document.documentElement.lang = lang;
  }, [lang]);

  const navigate = useCallback((r) => {
    const hash = r ? `#/${r}` : "#/";
    if (window.location.hash !== hash) window.location.hash = hash;
    setRoute(r || "");
    window.scrollTo?.(0, 0);
  }, []);

  const refresh = useCallback(async (s = sessionRef.current, l = langRef.current) => {
    if (!s) return null;
    try {
      const out = await api.summary(s, l);
      setSummary(out);
      setError(null);
      return out;
    } catch (e) {
      setError(e.status === 401 ? "session" : "agent");
      return null;
    }
  }, [api]);

  const start = useCallback(async () => {
    try {
      const s = await api.ensureSession();
      sessionRef.current = s;
      setSession(s);
      setError(null);
      await refresh(s);
    } catch {
      setError("agent");
    }
  }, [api, refresh]);

  useEffect(() => {
    start();
  }, [start]);

  const setLang = useCallback((l, { reload = true } = {}) => {
    if (!LANGS.includes(l)) return;
    langRef.current = l;
    setLangState(l);
    try {
      localStorage.setItem(LANG_KEY, l);
    } catch {
      /* ignore */
    }
    if (reload) refresh(sessionRef.current, l);
  }, [refresh]);

  const addMessage = useCallback((m) => {
    idRef.current += 1;
    const msg = { id: idRef.current, ...m };
    setMessages((ms) => [...ms, msg]);
    return msg;
  }, []);

  /** A Saathi line, unless the same line was just shown (a voice "turn"/"say" and an HTTP
   * reply can carry the same text). */
  const addAgentMessage = useCallback((m) => {
    const now = Date.now();
    setMessages((ms) => {
      const dup = ms.slice(-4).some((x) => x.from === "agent" && x.text === m.text && now - x.at < DEDUPE_MS);
      if (dup) return ms;
      idRef.current += 1;
      return [...ms, { id: idRef.current, from: "agent", at: now, ...m }];
    });
  }, []);

  const applyTurn = useCallback((res, { userText, via }) => {
    if (userText) addMessage({ from: "user", text: maskAadhaar(userText), via });
    if (res.reply) addAgentMessage({ text: res.reply, en: res.subtitle || null, via });
    const r = routeForTurn(res);
    if (r) navigate(r);
    refresh();
  }, [addMessage, addAgentMessage, navigate, refresh]);

  /** Read text aloud: the bot when voice is live, else /tts, else the browser. */
  const speak = useCallback(async (text, l = langRef.current) => {
    if (!text) return false;
    if (voiceRef.current && LIVE.has(voiceStatusRef.current)) return voiceRef.current.speak(text);
    const s = sessionRef.current;
    if (s && api.tts) {
      try {
        const out = await speakViaTts((t, lg) => api.tts(s, t, lg), text, l);
        if (out.state === "blocked") setAudioBlocked(() => out.retry);
        return true;
      } catch {
        /* /tts not configured or failed: the browser's voice, if it has one */
      }
    }
    return browserSpeak(text, l);
  }, [api]);

  /** After a web-side turn: the bot speaks it (voice on), or /tts if "Speak replies". */
  const speakReply = useCallback((text) => {
    if (!text) return;
    if (voiceRef.current && LIVE.has(voiceStatusRef.current)) voiceRef.current.speak(text);
    else if (speakRepliesRef.current) speak(text);
  }, [speak]);

  /** One web-side turn: typed text or a button that stands for words ("ಹೌದು", a title). */
  const send = useCallback(async (text, { shown } = {}) => {
    const msg = (text || "").trim();
    const s = sessionRef.current;
    if (!msg || busyRef.current || !s) return null;
    const typed = scriptLang(msg);
    if (typed && typed !== langRef.current) setLang(typed, { reload: false });
    const l = typed ?? langRef.current;
    busyRef.current = true;
    setBusy(true);
    try {
      const res = await api.turn(s, msg, l);
      applyTurn(res, { userText: shown ?? msg, via: "text" });
      speakReply(res.reply);
      return res;
    } catch {
      addMessage({ from: "agent", text: tr(langRef.current, "error_agent"), en: tr("en", "error_agent"), error: true });
      return null;
    } finally {
      busyRef.current = false;
      setBusy(false);
    }
  }, [api, applyTurn, addMessage, setLang, speakReply]);

  const edit = useCallback(async (field, value, shown) => {
    const s = sessionRef.current;
    if (!s || busyRef.current) return null;
    busyRef.current = true;
    setBusy(true);
    try {
      const res = await api.edit(s, field, value);
      applyTurn(res, { userText: shown, via: "text" });
      speakReply(res.reply);
      return res;
    } finally {
      busyRef.current = false;
      setBusy(false);
    }
  }, [api, applyTurn, speakReply]);

  // --- voice ------------------------------------------------------------------------
  const clearThinking = () => {
    clearTimeout(thinkingTimer.current);
    thinkingTimer.current = null;
  };

  const setVoiceStatus = useCallback((v) => {
    voiceStatusRef.current = v.status;
    setVoice(v);
  }, []);

  const connectVoice = useCallback(async () => {
    const s = sessionRef.current;
    if (voiceRef.current || !s) return;
    stopSpeech();
    setAudioBlocked(null);
    setVoiceStatus({ status: "connecting" });
    let lastReason = null;
    const mine = () => voiceRef.current === v;
    const v = createVoice({
      caseId: s.case_id,
      on: {
        state: (st) => {
          if (mine() && st === "ready") setVoiceStatus({ status: "listening" });
        },
        user: (speaking) => {
          if (!mine()) return;
          clearThinking();
          setVoiceStatus({ status: speaking ? "user" : "thinking" });
          if (!speaking) {
            thinkingTimer.current = setTimeout(() => {
              if (voiceStatusRef.current === "thinking") setVoiceStatus({ status: "listening" });
            }, THINKING_TIMEOUT_MS);
          }
        },
        bot: (speaking) => {
          if (!mine()) return;
          clearThinking();
          setVoiceStatus({ status: speaking ? "bot" : "listening" });
        },
        level: (l) => {
          levelRef.current = l;
        },
        turn: (msg) => {
          clearThinking();
          if (msg.lang) setLang(msg.lang, { reload: false });
          applyTurn(msg, { userText: msg.text, via: "voice" });
        },
        say: (msg) => addAgentMessage({ text: msg.text, en: msg.subtitle || null, via: "voice" }),
        audioBlocked: () => setAudioBlocked(() => () => voiceRef.current?.resumeAudio()),
        audioPlaying: () => setAudioBlocked(null),
        error: (reason) => {
          lastReason = reason || "failed";
          if (mine()) setVoiceStatus({ status: "error", reason: lastReason });
        },
        disconnected: () => {
          clearThinking();
          if (mine()) {
            voiceRef.current = null;
            setVoiceStatus(lastReason ? { status: "error", reason: lastReason } : { status: "off" });
          }
        },
      },
    });
    voiceRef.current = v;
    try {
      await v.connect();
    } catch (e) {
      if (mine()) voiceRef.current = null;
      setVoiceStatus({ status: "error", reason: e?.reason || "failed" });
    }
  }, [createVoice, applyTurn, addAgentMessage, setLang, setVoiceStatus]);

  const disconnectVoice = useCallback(async () => {
    const v = voiceRef.current;
    voiceRef.current = null;
    clearThinking();
    setAudioBlocked(null);
    setVoiceStatus({ status: "off" });
    if (v) {
      try {
        await v.disconnect();
      } catch {
        /* already gone */
      }
    }
  }, [setVoiceStatus]);

  useEffect(() => () => {
    voiceRef.current?.disconnect().catch(() => {});
  }, []);

  // --- data actions ---------------------------------------------------------------------
  const withSession = (fn) => async (...args) => {
    const s = sessionRef.current;
    if (!s) throw new Error("no session");
    return fn(s, ...args);
  };

  const actions = useMemo(() => ({
    navigate,
    refresh,
    retry: start,
    setLang,
    send,
    edit,
    speak,
    connectVoice,
    disconnectVoice,
    pickScheme: (scheme) => send(scheme.title),
    answerField: (field, value, shown) => send(answerText(field, value), { shown }),
    confirm: (yes) => send(tr(langRef.current, yes ? "yes_word" : "no_word")),
    setConsent: withSession(async (s, c) => {
      const out = await api.setConsent(s, c);
      await refresh();
      return out;
    }),
    upload: withSession(async (s, docType, file, last4) => {
      const out = await api.uploadDocument(s, docType, file, last4);
      await refresh();
      return out;
    }),
    removeDocument: withSession(async (s, id) => {
      await api.deleteDocument(s, id);
      await refresh();
    }),
    loadMyData: withSession((s) => api.myData(s)),
    deleteMyData: withSession(async (s) => {
      await disconnectVoice();
      await api.deleteMyData(s);
      const ns = await api.newSession();
      sessionRef.current = ns;
      setSession(ns);
      setMessages([]);
      await refresh(ns);
      setNotice("deleted");
      navigate("");
    }),
    install: async () => {
      if (!installEvent) return;
      installEvent.prompt();
      setInstallEvent(null);
    },
    dismissNotice: () => setNotice(null),
    resumeAudio: async () => {
      const retry = audioBlocked;
      setAudioBlocked(null);
      try {
        await retry?.();
      } catch {
        /* still blocked: the button comes back on the next attempt */
      }
    },
    setSpeakReplies: (on) => {
      speakRepliesRef.current = on;
      setSpeakRepliesState(on);
      try {
        localStorage.setItem(SPEAK_KEY, on ? "1" : "0");
      } catch {
        /* ignore */
      }
    },
  }), [navigate, refresh, start, setLang, send, edit, speak, connectVoice, disconnectVoice, api, installEvent, audioBlocked]);

  const value = {
    lang, session, summary, messages, busy, error, notice, route, voice, levelRef,
    canInstall: !!installEvent, audioBlocked: !!audioBlocked, speakReplies, ...actions,
  };
  return <CaseContext.Provider value={value}>{children}</CaseContext.Provider>;
}
