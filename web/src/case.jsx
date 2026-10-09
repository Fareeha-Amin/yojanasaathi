// The case this browser works on: session, screen summary, transcript, voice, and every
// action the screens can take. Screens read it with useCase(); tests provide their own value.
//
// One case, two channels: typed text goes to POST /turn; voice goes through the Pipecat bot,
// which POSTs /turn for the same case and sends the result back as an RTVI "turn" message.
// Both end in applyTurn(): add the bubbles, open the screen the turn calls for (confirm ->
// Review, otp / safe_stop -> Pre-fill, submitted -> My applications, eligibility -> Schemes),
// and reload the summary (the screens' source of truth).

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import * as defaultApi from "./api.js";
import { maskAadhaar } from "./format.js";
import { LANGS, scriptLang, tr } from "./i18n.js";

export const CaseContext = createContext(null);
export const useCase = () => useContext(CaseContext);

export const ROUTES = ["", "talk", "schemes", "documents", "prefill", "review", "applications", "profile"];
const LANG_KEY = "ys.lang";
const THINKING_TIMEOUT_MS = 12000; // speech the STT dropped: stop showing "thinking"

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
  return null;
}

/** Text the agent's parsers understand for an inline answer on the Schemes screen. */
export function answerText(field, value) {
  return field === "age" ? `I am ${value} years old` : `my annual income is ${value}`;
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
    async disconnect() {
      if (v) await v.disconnect();
    },
  };
}

function browserSpeak(text, lang) {
  const synth = typeof window !== "undefined" ? window.speechSynthesis : null;
  if (!synth || !text) return false;
  const voice = synth.getVoices().find((v) => v.lang?.toLowerCase().startsWith(lang));
  if (!voice) return false;
  synth.cancel();
  const u = new SpeechSynthesisUtterance(text);
  u.voice = voice;
  u.lang = voice.lang;
  synth.speak(u);
  return true;
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

  const sessionRef = useRef(null);
  const langRef = useRef(lang);
  const busyRef = useRef(false);
  const voiceRef = useRef(null);
  const levelRef = useRef(0);
  const idRef = useRef(0);
  const thinkingTimer = useRef(null);

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

  const applyTurn = useCallback((res, { userText, via }) => {
    if (userText) addMessage({ from: "user", text: maskAadhaar(userText), via });
    if (res.reply) addMessage({ from: "agent", text: res.reply, en: res.subtitle || null, via });
    const r = routeForTurn(res);
    if (r) navigate(r);
    refresh();
  }, [addMessage, navigate, refresh]);

  const speak = useCallback((text) => {
    if (!text) return false;
    if (voiceRef.current) return voiceRef.current.speak(text);
    return browserSpeak(text, langRef.current);
  }, []);

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
      if (voiceRef.current && res.reply) voiceRef.current.speak(res.reply);
      return res;
    } catch {
      addMessage({ from: "agent", text: tr(langRef.current, "error_agent"), en: tr("en", "error_agent"), error: true });
      return null;
    } finally {
      busyRef.current = false;
      setBusy(false);
    }
  }, [api, applyTurn, addMessage, setLang]);

  const edit = useCallback(async (field, value, shown) => {
    const s = sessionRef.current;
    if (!s || busyRef.current) return null;
    busyRef.current = true;
    setBusy(true);
    try {
      const res = await api.edit(s, field, value);
      applyTurn(res, { userText: shown, via: "text" });
      if (voiceRef.current && res.reply) voiceRef.current.speak(res.reply);
      return res;
    } finally {
      busyRef.current = false;
      setBusy(false);
    }
  }, [api, applyTurn]);

  // --- voice ------------------------------------------------------------------------
  const clearThinking = () => {
    clearTimeout(thinkingTimer.current);
    thinkingTimer.current = null;
  };

  const connectVoice = useCallback(async () => {
    const s = sessionRef.current;
    if (voiceRef.current || !s) return;
    setVoice({ status: "connecting" });
    const v = createVoice({
      caseId: s.case_id,
      on: {
        state: (st) => {
          if (st === "ready") setVoice({ status: "listening" });
        },
        user: (speaking) => {
          clearThinking();
          setVoice({ status: speaking ? "user" : "thinking" });
          if (!speaking) {
            thinkingTimer.current = setTimeout(
              () => setVoice((x) => (x.status === "thinking" ? { status: "listening" } : x)),
              THINKING_TIMEOUT_MS);
          }
        },
        bot: (speaking) => {
          clearThinking();
          setVoice((x) => (x.status === "off" ? x : { status: speaking ? "bot" : "listening" }));
        },
        level: (l) => {
          levelRef.current = l;
        },
        turn: (msg) => {
          clearThinking();
          if (msg.lang) setLang(msg.lang, { reload: false });
          applyTurn(msg, { userText: msg.text, via: "voice" });
        },
        error: () => {},
        disconnected: () => {
          clearThinking();
          if (voiceRef.current === v) {
            voiceRef.current = null;
            setVoice({ status: "off" });
          }
        },
      },
    });
    voiceRef.current = v;
    try {
      await v.connect();
    } catch {
      voiceRef.current = null;
      setVoice({ status: "error" });
    }
  }, [createVoice, applyTurn, setLang]);

  const disconnectVoice = useCallback(async () => {
    const v = voiceRef.current;
    voiceRef.current = null;
    clearThinking();
    setVoice({ status: "off" });
    if (v) {
      try {
        await v.disconnect();
      } catch {
        /* already gone */
      }
    }
  }, []);

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
    newCase: async () => {
      await disconnectVoice();
      const ns = await api.newSession();
      sessionRef.current = ns;
      setSession(ns);
      setMessages([]);
      await refresh(ns);
      navigate("talk");
    },
    install: async () => {
      if (!installEvent) return;
      installEvent.prompt();
      setInstallEvent(null);
    },
    dismissNotice: () => setNotice(null),
  }), [navigate, refresh, start, setLang, send, edit, speak, connectVoice, disconnectVoice, api, installEvent]);

  const value = {
    lang, session, summary, messages, busy, error, notice, route, voice, levelRef,
    canInstall: !!installEvent, ...actions,
  };
  return <CaseContext.Provider value={value}>{children}</CaseContext.Provider>;
}
