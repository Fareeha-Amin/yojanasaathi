// YojanaSaathi web app (Phase 5): landing + 6 screens + privacy, voice and text on one case.
// Hash routes (#/talk, #/schemes, ...): no router dependency, works from the PWA's start_url.

import { BottomNav, Bi, FloatingVoice, Header, TapToHear, micText } from "./components.jsx";
import { CaseProvider, useCase } from "./case.jsx";
import Applications from "./screens/Applications.jsx";
import Documents from "./screens/Documents.jsx";
import Landing from "./screens/Landing.jsx";
import Prefill from "./screens/Prefill.jsx";
import Profile from "./screens/Profile.jsx";
import Review from "./screens/Review.jsx";
import Schemes from "./screens/Schemes.jsx";
import Talk from "./screens/Talk.jsx";

const SCREENS = {
  "": Landing, talk: Talk, schemes: Schemes, documents: Documents, prefill: Prefill,
  review: Review, applications: Applications, profile: Profile,
};

function Shell() {
  const { route, error, retry, voice } = useCase();
  const Screen = SCREENS[route] || Landing;
  const inApp = route !== "";
  return (
    <div className={`app ${inApp ? "in-app" : "on-landing"} ${inApp && route !== "talk" ? "with-dock" : ""}`}>
      <a href="#main" className="skip">Skip to content</a>
      <Header />
      {error === "agent" && (
        <div className="banner banner-error" role="alert">
          <Bi k="error_agent" block />
          <button type="button" className="btn btn-sm btn-secondary" onClick={retry}><Bi k="retry" /></button>
        </div>
      )}
      {voice.status === "error" && (
        <div className="banner banner-warn" role="alert"><Bi k={micText("error", voice.reason)} block /></div>
      )}
      <TapToHear />
      <div id="main" className="main">
        <Screen />
      </div>
      {inApp && route !== "talk" && <FloatingVoice />}
      {inApp && <BottomNav />}
      {!inApp && (
        <footer className="site-footer">
          <Bi k="footer" block />
        </footer>
      )}
    </div>
  );
}

export default function App(props) {
  return (
    <CaseProvider {...props}>
      <Shell />
    </CaseProvider>
  );
}
