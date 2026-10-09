// The agent's HTTP API. Same origin through Vite's proxy (/api -> agent :8000), so the app
// also works from a phone on the LAN. VITE_API_URL overrides it (e.g. a hosted agent).
//
// Session: POST /session gives this browser its own case + a JWT (agent/auth.py). The token
// is the only key to the case's screens and data (/cases/{id}/...); /turn is keyed by the
// (random) case ID, like the voice bot. `?session=<token>` in the URL (printed by
// `python -m agent.auth <case>`) opens an existing case, e.g. for a demo.

export const API = import.meta.env.VITE_API_URL ?? "/api";
const KEY = "ys.session";

export class ApiError extends Error {
  constructor(status, detail) {
    super(typeof detail === "string" ? detail : `HTTP ${status}`);
    this.status = status;
    this.detail = detail;
  }
}

function load() {
  try {
    return JSON.parse(localStorage.getItem(KEY) || "null");
  } catch {
    return null;
  }
}

function save(s) {
  try {
    if (s) localStorage.setItem(KEY, JSON.stringify(s));
    else localStorage.removeItem(KEY);
  } catch {
    /* private mode: the session lives for this page only */
  }
  return s;
}

async function req(path, { method = "GET", token, json, body, headers = {} } = {}) {
  const h = { ...headers };
  if (token) h.Authorization = `Bearer ${token}`;
  if (json !== undefined) h["Content-Type"] = "application/json";
  let res;
  try {
    res = await fetch(`${API}${path}`, { method, headers: h, body: json !== undefined ? JSON.stringify(json) : body });
  } catch {
    throw new ApiError(0, "network");
  }
  const text = await res.text();
  let data = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = text;
  }
  if (!res.ok) throw new ApiError(res.status, data?.detail ?? data);
  return data;
}

function tokenFromUrl() {
  try {
    const url = new URL(window.location.href);
    const t = url.searchParams.get("session");
    if (!t) return null;
    url.searchParams.delete("session");
    window.history.replaceState(null, "", url.pathname + url.search + url.hash);
    return t;
  } catch {
    return null;
  }
}

/** This browser's case: refresh the stored token, or start a new case. */
export async function ensureSession() {
  const fromUrl = tokenFromUrl();
  const stored = load();
  const token = fromUrl || stored?.token;
  if (token) {
    try {
      return save(await req("/session", { method: "POST", token }));
    } catch (e) {
      if (e.status !== 401) throw e; // expired / invalid: start a new case below
    }
  }
  return save(await req("/session", { method: "POST" }));
}

export async function newSession() {
  save(null);
  return save(await req("/session", { method: "POST" }));
}

const c = (s) => encodeURIComponent(s.case_id);

export const turn = (s, text, lang) =>
  req(`/turn/${c(s)}`, { method: "POST", json: lang ? { text, lang } : { text } });

export const summary = (s, lang) =>
  req(`/cases/${c(s)}/summary${lang ? `?lang=${lang}` : ""}`, { token: s.token });

export const edit = (s, field, value) =>
  req(`/cases/${c(s)}/edit`, { method: "POST", token: s.token, json: { field, value } });

export const setConsent = (s, consent) =>
  req(`/cases/${c(s)}/consent`, { method: "PUT", token: s.token, json: consent });

export const uploadDocument = (s, docType, file, aadhaarLast4) =>
  req(`/cases/${c(s)}/documents/${encodeURIComponent(docType)}${aadhaarLast4 ? `?aadhaar_last4=${aadhaarLast4}` : ""}`, {
    method: "PUT", token: s.token, body: file, headers: { "Content-Type": file.type },
  });

export const deleteDocument = (s, docId) =>
  req(`/cases/${c(s)}/documents/${encodeURIComponent(docId)}`, { method: "DELETE", token: s.token });

export const myData = (s) => req(`/cases/${c(s)}/data`, { token: s.token });

export const deleteMyData = (s) => req(`/cases/${c(s)}/data`, { method: "DELETE", token: s.token });
