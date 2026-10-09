// Display formatting. Money uses Indian grouping (₹1,20,000), like the agent's read-back.

const LOCALE = { kn: "kn-IN", hi: "hi-IN", en: "en-IN" };
const MONEY_FIELDS = new Set(["annual_income"]);

export function money(n) {
  return "₹" + Math.round(Number(n)).toLocaleString("en-IN");
}

/** A rule limit or a citizen value, shown the way the agent says it. */
export function fieldValue(field, v) {
  if (v === null || v === undefined) return null;
  if (MONEY_FIELDS.has(field) && typeof v === "number") return money(v);
  return String(v);
}

export function dateTime(iso, lang) {
  if (!iso) return "";
  try {
    return new Intl.DateTimeFormat(LOCALE[lang] || "en-IN", {
      day: "numeric", month: "short", hour: "2-digit", minute: "2-digit",
    }).format(new Date(iso));
  } catch {
    return iso;
  }
}

// Same rule as agent/privacy.py and voice/lang.py: any 12-digit run is treated as an
// Aadhaar number and shown as its last 4 digits only (the agent masks it again).
const AADHAAR = /(?<![\d+])(\d{4})[ -]?(\d{4})[ -]?(\d{4})(?!\d)/g;
export function maskAadhaar(text) {
  return String(text).replace(AADHAAR, (_m, _a, _b, c) => `XXXX XXXX ${c}`);
}

/** A source_url the browser can open (Phase 4 sets MOCK_PORTAL_URL to the portal). */
export function usableUrl(url) {
  return typeof url === "string" && /^https?:\/\/[^<>{}\s]+$/.test(url) ? url : null;
}
