// Skeleton chat page: proves web -> /turn -> agent -> pause -> resume.
// Replace with the real screens from the "YojanaSaathi UI" design (Talk, Schemes,
// Documents, Pre-fill + OTP, Review, My applications).
import { useState } from "react";

const API = import.meta.env.VITE_API_URL ?? "http://127.0.0.1:8000";
const CASE_ID = "demo-case-1";

const green = "#0B5D4B";
const marigold = "#F2B21B";

export default function App() {
  const [messages, setMessages] = useState([]);
  const [text, setText] = useState("");
  const [pause, setPause] = useState(null);
  const [busy, setBusy] = useState(false);

  async function send(value) {
    const msg = value.trim();
    if (!msg || busy) return;
    setMessages((m) => [...m, { from: "user", text: msg }]);
    setText("");
    setBusy(true);
    try {
      const res = await fetch(`${API}/turn/${CASE_ID}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: msg }),
      });
      const data = await res.json();
      setMessages((m) => [...m, { from: "agent", text: data.reply }]);
      setPause(data.pause);
    } catch {
      setMessages((m) => [...m, { from: "agent", text: "Can't reach the agent. Is the backend running?" }]);
    } finally {
      setBusy(false);
    }
  }

  return (
    <main style={{ maxWidth: 480, margin: "0 auto", padding: 20, fontFamily: "system-ui, sans-serif", color: "#17201C" }}>
      <h1 style={{ fontSize: 24 }}>YojanaSaathi</h1>

      <div style={{ display: "flex", flexDirection: "column", gap: 10, minHeight: 300 }}>
        {messages.map((m, i) => (
          <div
            key={i}
            style={{
              alignSelf: m.from === "user" ? "flex-end" : "flex-start",
              maxWidth: "80%",
              padding: "12px 14px",
              borderRadius: 16,
              background: m.from === "user" ? green : "#F4F2EC",
              color: m.from === "user" ? "#fff" : "#17201C",
              fontSize: 17,
            }}
          >
            {m.text}
          </div>
        ))}
      </div>

      {pause?.type === "confirm" && (
        <div style={{ display: "flex", gap: 10, margin: "16px 0" }}>
          <button onClick={() => send("yes")} style={{ flex: 1, height: 52, borderRadius: 14, border: "none", background: green, color: "#fff", fontSize: 17, fontWeight: 700 }}>
            ಹೌದು, ಸಲ್ಲಿಸಿ · Yes, submit
          </button>
          <button onClick={() => send("no")} style={{ height: 52, padding: "0 16px", borderRadius: 14, border: `1.5px solid ${green}`, background: "#fff", color: green, fontSize: 16 }}>
            Not now
          </button>
        </div>
      )}

      <form onSubmit={(e) => { e.preventDefault(); send(text); }} style={{ display: "flex", gap: 8, marginTop: 16 }}>
        <label htmlFor="msg" style={{ position: "absolute", left: -9999 }}>Message</label>
        <input id="msg" value={text} onChange={(e) => setText(e.target.value)} placeholder="Type, e.g. I'm 62, can I get a pension?"
          style={{ flex: 1, height: 48, padding: "0 14px", borderRadius: 12, border: "1.5px solid #E3E0D6", fontSize: 16 }} />
        <button type="submit" disabled={busy} style={{ height: 48, padding: "0 18px", borderRadius: 12, border: "none", background: marigold, fontWeight: 700, fontSize: 16 }}>
          Send
        </button>
      </form>
    </main>
  );
}
