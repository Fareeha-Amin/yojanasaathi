import { Bi, Logo } from "../components.jsx";
import { useCase } from "../case.jsx";

const STEPS = ["how_1", "how_2", "how_3", "how_4"];
const TRUST = ["trust_rules", "trust_yes", "trust_otp", "trust_docs"];

export default function Landing() {
  const { navigate, connectVoice, notice, dismissNotice, canInstall, install } = useCase();

  const startTalking = () => {
    navigate("talk");
    connectVoice(); // the click is the user gesture the mic permission needs
  };
  const typeInstead = () => {
    navigate("talk");
    setTimeout(() => document.getElementById("msg")?.focus(), 50);
  };

  return (
    <main className="landing">
      {notice === "deleted" && (
        <div className="notice" role="status">
          <Bi k="deleted" block />
          <button type="button" className="btn btn-ghost btn-sm" onClick={dismissNotice}><Bi k="close" /></button>
        </div>
      )}
      <section className="hero">
        <Logo size={72} />
        <p className="hero-tagline"><Bi k="tagline" block /></p>
        <h1><Bi k="hero_title" block /></h1>
        <p className="lead"><Bi k="hero_body" block /></p>
        <div className="hero-actions">
          <button type="button" className="btn btn-primary btn-xl" onClick={startTalking}>
            <svg viewBox="0 0 24 24" width="26" height="26" aria-hidden="true">
              <path d="M12 3a3 3 0 0 0-3 3v6a3 3 0 0 0 6 0V6a3 3 0 0 0-3-3zM5 11a7 7 0 0 0 14 0M12 18v3" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
            </svg>
            <Bi k="start_talking" />
          </button>
          <button type="button" className="btn btn-secondary btn-lg" onClick={typeInstead}>
            <Bi k="type_instead" />
          </button>
        </div>
      </section>

      <section className="card phone-card" aria-labelledby="phone-h">
        <h2 id="phone-h"><Bi k="phone_title" block /></h2>
        <p className="phone-number" aria-disabled="true">
          <svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true">
            <path d="M5 4h4l2 5-2.5 1.5a11 11 0 0 0 5 5L15 13l5 2v4a2 2 0 0 1-2 2A16 16 0 0 1 3 6a2 2 0 0 1 2-2z" fill="none" stroke="currentColor" strokeWidth="2" strokeLinejoin="round" />
          </svg>
          <Bi k="phone_number" />
        </p>
        <p className="muted"><Bi k="phone_soon" block /></p>
      </section>

      <section aria-labelledby="how-h">
        <h2 id="how-h"><Bi k="how_title" block /></h2>
        <ol className="steps">
          {STEPS.map((k, i) => (
            <li key={k} className={i === 3 ? "step-key" : ""}>
              <span className="step-n" aria-hidden="true">{i + 1}</span>
              <Bi k={k} block />
            </li>
          ))}
        </ol>
      </section>

      <section aria-labelledby="trust-h">
        <h2 id="trust-h"><Bi k="trust_title" block /></h2>
        <ul className="trust">
          {TRUST.map((k) => (
            <li key={k}>
              <svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true">
                <path d="m5 12 4 4 10-10" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
              <Bi k={k} block />
            </li>
          ))}
        </ul>
      </section>

      {canInstall && (
        <button type="button" className="btn btn-secondary" onClick={install}><Bi k="install_app" /></button>
      )}
    </main>
  );
}
