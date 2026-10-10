"""A local fake of the mock portal (github.com/ayush81233/mock at 9cf1d89) for the browser
tests: the same data-testids, page flow and API shapes, on 127.0.0.1. OTP is 123456.

Pages (plain HTML + JS, re-rendered per step like the React app):
  /citizen-access   otp-mobile-input, otp-send-button -> otp-code-input, otp-submit-button,
                    otp-resend-button, otp-error-message, otp-success-message;
                    success: localStorage citizen_token, then /my-services
  /apply/<id>       step 1 apply-full-name-input, apply-email-input, apply-address-input,
                    apply-step1-next-btn; step 2 apply-field-<name> (radios
                    apply-field-<name>-<option>), apply-step2-back-btn/-next-btn; step 3
                    doc-file-input-<slug>, doc-uploaded-badge-<slug>, apply-step3-*;
                    step 4 apply-declaration-checkbox (shares declaration_consent with step
                    2, as in ApplyScheme.jsx), apply-submit-btn (disabled until ticked),
                    apply-error-banner; step 5 apply-confirmation-app-number
API (/api): status, auth/request-otp, auth/verify-otp, schemes/<id>, applications (create
or reuse DRAFT), applications/mine, .../documents (JSON + base64 here: no multipart),
.../submit, agent/v1/schemes/<id>/requirements (X-Agent-API-Key), auth/agent-delegation.

Knobs for tests: rename (testid -> other), dialog_at_step, offhost_url, extra_field (drift),
submit_error, refuse (mobiles). Records: requests, uploads (bytes), submissions, otp_sent.
"""

import base64
import json
import secrets
import socket
import threading
import time
from typing import Any

import uvicorn
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from agent import rules

OTP = "123456"
AGENT_KEY = "yjs_ag_test_key_not_a_secret"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _scheme_json(sid: str) -> dict[str, Any]:
    s = rules.load_schemes()[sid]
    return {"id": sid, "title": s["title"], "category": s["category"],
            "application_fields": [{k: v for k, v in f.items() if k != "label"} | {"label": f["label"]["en"]}
                                   for f in s["application_fields"]],
            "documents": [d["label"]["en"] for d in s["documents"]]}


ACCESS_PAGE = """<!doctype html><html><head><title>Citizen access (fake)</title></head><body>
<h1>Citizen Access</h1><div id="root"></div>
<script>
const API = location.origin + "/api";
let state = {step: "mobile", mobile: "", error: "", success: "", loading: false};
function render() {
  const r = document.getElementById("root");
  let h = "";
  if (state.step === "mobile") {
    h += '<form id="f1"><input data-testid="otp-mobile-input" id="mobile" maxlength="10" value="' + state.mobile + '">'
       + '<button type="submit" data-testid="otp-send-button">Send OTP</button></form>';
  } else {
    h += '<form id="f2"><p>Sent to +91 ' + state.mobile + '</p><input data-testid="otp-code-input" id="otp" maxlength="6">'
       + '<button type="submit" data-testid="otp-submit-button">Verify</button>'
       + '<button type="button" data-testid="otp-resend-button" id="resend">Resend</button></form>';
  }
  if (state.error) h += '<div data-testid="otp-error-message" role="alert">' + state.error + '</div>';
  if (state.success) h += '<div data-testid="otp-success-message">' + state.success + '</div>';
  r.innerHTML = h;
  const f1 = document.getElementById("f1");
  if (f1) {
    document.getElementById("mobile").addEventListener("input", e => { state.mobile = e.target.value.replace(/\\D/g, ""); });
    f1.addEventListener("submit", async e => { e.preventDefault(); state.error = ""; state.success = ""; render();
      const res = await fetch(API + "/auth/request-otp/", {method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({mobile: state.mobile})});
      const data = await res.json();
      if (res.ok) { state.step = "otp"; state.success = "OTP sent"; } else { state.error = data.error; }
      render(); });
  }
  const f2 = document.getElementById("f2");
  if (f2) {
    f2.addEventListener("submit", async e => { e.preventDefault(); const otp = document.getElementById("otp").value;
      state.error = ""; state.success = ""; render(); document.getElementById("otp").value = otp;
      const res = await fetch(API + "/auth/verify-otp/", {method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({mobile: state.mobile, otp: otp})});
      const data = await res.json();
      if (res.ok) { localStorage.setItem("citizen_token", data.token); localStorage.setItem("citizen", JSON.stringify(data.citizen));
        location.href = "/my-services"; }
      else { state.error = data.error; render(); } });
    document.getElementById("resend").addEventListener("click", async () => { state.error = ""; state.success = "";
      const res = await fetch(API + "/auth/request-otp/", {method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({mobile: state.mobile})});
      if (res.ok) state.success = "OTP sent"; else state.error = (await res.json()).error;
      render(); });
  }
}
render();
</script></body></html>"""

APPLY_PAGE = """<!doctype html><html><head><title>Apply (fake)</title></head><body>
<h1>Apply</h1>__OFFHOST__<div id="root">Loading...</div>
<script>
const API = location.origin + "/api";
const SID = location.pathname.split("/").filter(Boolean)[1];
const DIALOG_AT = __DIALOG_AT__;
const token = localStorage.getItem("citizen_token");
const H = () => ({"Content-Type": "application/json", "Authorization": "Token " + token});
let scheme = null, app = null, form = {}, docs = {}, step = 1, error = "", errs = {};
const slug = n => n.replace(/\\s+/g, "-").toLowerCase();
const esc = s => String(s ?? "").replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;");
function go(n) { step = n; error = ""; render(); if (DIALOG_AT === n) alert("Please check the form"); }
function render() {
  const r = document.getElementById("root");
  let h = "";
  if (error) h += '<div data-testid="apply-error-banner" role="alert">' + esc(error) + '</div>';
  if (step === 1) {
    h += '<input data-testid="apply-full-name-input" id="s1n" value="' + esc(form.full_name) + '">'
       + '<input data-testid="apply-email-input" id="s1e" value="' + esc(form.email) + '">'
       + '<textarea data-testid="apply-address-input" id="s1a">' + esc(form.address) + '</textarea>'
       + '<button data-testid="apply-step1-next-btn" id="n1">Next</button>';
  } else if (step === 2) {
    for (const f of scheme.application_fields) {
      const v = form[f.name];
      const tid = "apply-field-" + f.name;
      h += '<div class="field"><label>' + esc(f.label) + '</label>';
      if (f.type === "select") {
        h += '<select data-testid="' + tid + '" data-f="' + f.name + '"><option value="">Select</option>'
           + f.options.map(o => '<option value="' + esc(o) + '"' + (v === o ? " selected" : "") + '>' + esc(o) + '</option>').join("") + '</select>';
      } else if (f.type === "radio") {
        h += f.options.map(o => '<label><input type="radio" name="' + f.name + '" value="' + esc(o) + '" data-testid="' + tid + '-' + esc(o)
           + '" data-f="' + f.name + '"' + (v === o ? " checked" : "") + '>' + esc(o) + '</label>').join("");
      } else if (f.type === "checkbox") {
        h += '<input type="checkbox" data-testid="' + tid + '" data-f="' + f.name + '"' + (v ? " checked" : "") + '>';
      } else if (f.type === "textarea") {
        h += '<textarea data-testid="' + tid + '" data-f="' + f.name + '">' + esc(v) + '</textarea>';
      } else {
        h += '<input type="' + f.type + '" data-testid="' + tid + '" data-f="' + f.name + '" value="' + esc(v) + '">';
      }
      if (errs[f.name]) h += '<span class="err-msg">' + esc(errs[f.name]) + '</span>';
      h += '</div>';
    }
    h += '<button data-testid="apply-step2-back-btn" id="b2">Back</button><button data-testid="apply-step2-next-btn" id="n2">Next</button>';
  } else if (step === 3) {
    for (const d of scheme.documents) {
      const s = slug(d);
      h += '<div data-testid="doc-upload-card-' + s + '"><h4>' + esc(d) + '</h4>';
      if (docs[d]) h += '<span data-testid="doc-uploaded-badge-' + s + '">Uploaded</span>';
      else h += '<label>Upload<input type="file" style="display:none" data-testid="doc-file-input-' + s + '" data-doc="' + esc(d) + '"></label>';
      h += '</div>';
    }
    h += '<button data-testid="apply-step3-back-btn" id="b3">Back</button><button data-testid="apply-step3-next-btn" id="n3">Next</button>';
  } else if (step === 4) {
    h += '<ul>' + scheme.application_fields.map(f => '<li>' + esc(f.label) + ': ' + esc(form[f.name]) + '</li>').join("") + '</ul>'
       + '<label><input type="checkbox" data-testid="apply-declaration-checkbox" id="decl"' + (form.declaration_consent ? " checked" : "") + '> I declare</label>'
       + '<button data-testid="apply-step4-back-btn" id="b4">Back</button>'
       + '<button data-testid="apply-submit-btn" id="sub"' + (form.declaration_consent ? "" : " disabled") + '>Submit</button>';
  } else if (step === 5) {
    h += '<p>Submitted</p><span data-testid="apply-confirmation-app-number">' + esc(app.application_number) + '</span>';
  }
  r.innerHTML = h;
  bind();
}
function bind() {
  const on = (id, fn) => { const e = document.getElementById(id); if (e) e.addEventListener("click", fn); };
  const s1 = (id, k) => { const e = document.getElementById(id); if (e) e.addEventListener("input", ev => { form[k] = ev.target.value; }); };
  s1("s1n", "full_name"); s1("s1e", "email"); s1("s1a", "address");
  document.querySelectorAll("[data-f]").forEach(e => {
    const k = e.dataset.f;
    if (e.type === "checkbox") e.addEventListener("change", () => { form[k] = e.checked; });
    else if (e.type === "radio") e.addEventListener("change", () => { form[k] = e.value; });
    else e.addEventListener(e.tagName === "SELECT" ? "change" : "input", () => { form[k] = e.value; });
  });
  document.querySelectorAll("[data-doc]").forEach(e => e.addEventListener("change", async () => {
    const file = e.files[0]; const d = e.dataset.doc;
    if (file.size > 5 * 1024 * 1024) { alert("File size exceeds 5MB limit."); return; }
    if (![".pdf", ".jpg", ".jpeg", ".png"].includes(file.name.slice(file.name.lastIndexOf(".")).toLowerCase())) { alert("Invalid file format."); return; }
    const b64 = await new Promise(ok => { const fr = new FileReader(); fr.onload = () => ok(fr.result.split(",")[1]); fr.readAsDataURL(file); });
    const res = await fetch(API + "/applications/" + app.application_number + "/documents/", {method: "POST", headers: H(),
      body: JSON.stringify({document_type: d, file_name: file.name, content_base64: b64})});
    if (res.ok) docs[d] = true; else error = (await res.json()).error;
    render(); }));
  on("n1", () => go(2));
  on("b2", () => go(1));
  on("n2", () => { errs = {}; for (const f of scheme.application_fields) { const v = form[f.name];
      if (f.required && (v === undefined || v === null || (typeof v === "string" && !v.trim()) || v === false)) errs[f.name] = f.label + " is required"; }
    if (Object.keys(errs).length) render(); else go(3); });
  on("b3", () => go(2));
  on("n3", () => { for (const d of scheme.documents) { if (d.toLowerCase().includes("where applicable")) continue;
      if (!docs[d]) { error = "Required: " + d; render(); return; } } go(4); });
  on("b4", () => go(3));
  const decl = document.getElementById("decl");
  if (decl) decl.addEventListener("change", () => { form.declaration_consent = decl.checked; render(); });
  on("sub", async () => {
    const res = await fetch(API + "/applications/" + app.application_number + "/submit/", {method: "POST", headers: H(),
      body: JSON.stringify({form_data: form})});
    const data = await res.json();
    if (res.ok) { app = data.application; go(5); } else { error = data.error; render(); } });
}
(async () => {
  if (!token) { document.getElementById("root").innerHTML = "<p>Please log in</p>"; return; }
  scheme = await (await fetch(API + "/schemes/" + SID + "/")).json();
  const res = await fetch(API + "/applications/", {method: "POST", headers: H(), body: JSON.stringify({scheme_id: SID, form_data: {}})});
  app = await res.json();
  form = app.form_data || {};
  for (const d of app.documents || []) docs[d] = true;
  if (app.status !== "DRAFT" && app.status !== "CORRECTION_REQUIRED") step = 5;
  render();
})();
</script></body></html>"""


def _multipart(body: bytes, content_type: str) -> tuple[str, bytes]:
    """(document_type, file bytes) from a multipart/form-data body (no python-multipart needed)."""
    boundary = content_type.split("boundary=")[1].strip('"').encode()
    fields: dict[str, bytes] = {}
    file = b""
    crlf = bytes([13, 10])
    for part in body.split(b"--" + boundary)[1:-1]:
        head, _, content = part.lstrip(crlf).partition(crlf + crlf)
        content = content.removesuffix(crlf)
        name = head.split(b'name="')[1].split(b'"')[0].decode()
        if b"filename=" in head:
            file = content
        else:
            fields[name] = content
    return fields["document_type"].decode(), file


class FakePortal:
    def __init__(self) -> None:
        self.rename: dict[str, str] = {}
        self.dialog_at_step: int | None = None
        self.offhost_url: str | None = None
        self.extra_field: dict[str, Any] | None = None
        self.submit_error: str | None = None
        self.refuse: set[str] = set()
        self.requests: list[tuple[str, str]] = []
        self.uploads: list[tuple[str, str, bytes]] = []
        self.submissions: list[tuple[str, str]] = []  # (scheme, number)
        self.otp_sent: list[str] = []  # mobiles
        self._tokens: dict[str, str] = {}  # token -> mobile
        self._apps: list[dict[str, Any]] = []
        self.app = self._build()
        self.port = _free_port()
        self.url = f"http://127.0.0.1:{self.port}"
        self.api = f"{self.url}/api"
        self._server: uvicorn.Server | None = None

    def reset(self) -> None:
        self.rename, self.dialog_at_step, self.offhost_url = {}, None, None
        self.extra_field, self.submit_error, self.refuse = None, None, set()

    # --- server -----------------------------------------------------------------------

    def start(self) -> "FakePortal":
        self._server = uvicorn.Server(uvicorn.Config(self.app, host="127.0.0.1", port=self.port,
                                                     log_level="warning", lifespan="off"))
        threading.Thread(target=self._server.run, name="fake-portal", daemon=True).start()
        for _ in range(200):
            if self._server.started:
                return self
            time.sleep(0.05)
        raise RuntimeError("fake portal did not start")

    def stop(self) -> None:
        if self._server:
            self._server.should_exit = True

    def _page(self, html: str) -> HTMLResponse:
        for old, new in self.rename.items():
            html = html.replace(old, new)
        return HTMLResponse(html)

    def _mobile(self, authorization: str | None) -> str:
        tok = (authorization or "").removeprefix("Token ").strip()
        if tok not in self._tokens:
            raise HTTPException(401, "Invalid token.")
        return self._tokens[tok]

    def _app_json(self, a: dict[str, Any]) -> dict[str, Any]:
        return {"application_number": a["number"], "scheme": a["scheme"], "status": a["status"],
                "form_data": a["form_data"], "documents": sorted(a["docs"])}

    def _find(self, number: str, mobile: str) -> dict[str, Any]:
        for a in self._apps:
            if a["number"] == number and a["mobile"] == mobile:
                return a
        raise HTTPException(404, "Application not found.")

    def _build(self) -> FastAPI:
        app = FastAPI()
        portal = self

        @app.middleware("http")
        async def record(request: Request, call_next):
            portal.requests.append((request.method, request.url.path))
            return await call_next(request)

        @app.get("/citizen-access")
        def access() -> HTMLResponse:
            return portal._page(ACCESS_PAGE)

        @app.get("/my-services")
        def services() -> HTMLResponse:
            return portal._page("<!doctype html><html><body><h1>My services</h1></body></html>")

        @app.get("/apply/{sid}")
        def apply(sid: str) -> HTMLResponse:
            off = f'<img src="{portal.offhost_url}" alt="">' if portal.offhost_url else ""
            return portal._page(APPLY_PAGE.replace("__OFFHOST__", off)
                                .replace("__DIALOG_AT__", json.dumps(portal.dialog_at_step)))

        @app.get("/")
        def home() -> HTMLResponse:
            return portal._page("<!doctype html><html><body>Demo portal, not a government website</body></html>")

        @app.get("/api/status/")
        def status() -> dict:
            return {"status": "success"}

        @app.post("/api/auth/request-otp/")
        async def request_otp(request: Request):
            mobile = (await request.json()).get("mobile", "")
            if len(mobile) != 10 or mobile in portal.refuse:
                return JSONResponse({"error": "This mobile number is not allowed."}, 400)
            portal.otp_sent.append(mobile)
            return {"status": "pending"}

        @app.post("/api/auth/verify-otp/")
        async def verify_otp(request: Request):
            body = await request.json()
            if body.get("otp") != OTP:
                return JSONResponse({"error": "Incorrect or expired OTP."}, 400)
            tok = secrets.token_hex(20)
            portal._tokens[tok] = body["mobile"]
            return {"token": tok, "citizen": {"mobile": body["mobile"], "full_name": ""}}

        @app.get("/api/schemes/{sid}/")
        def scheme(sid: str) -> dict:
            return _scheme_json(sid)

        @app.post("/api/applications/")
        async def create(request: Request, authorization: str | None = Header(None)):
            mobile = portal._mobile(authorization)
            sid = (await request.json())["scheme_id"]
            for a in portal._apps:
                if a["mobile"] == mobile and a["scheme"] == sid and a["status"] == "DRAFT":
                    return portal._app_json(a)
            a = {"number": "YJS-" + secrets.token_hex(5).upper(), "mobile": mobile, "scheme": sid,
                 "status": "DRAFT", "form_data": {}, "docs": set()}
            portal._apps.append(a)
            return JSONResponse(portal._app_json(a), 201)

        @app.get("/api/applications/mine/")
        def mine(authorization: str | None = Header(None)) -> dict:
            mobile = portal._mobile(authorization)
            return {"count": 0, "results": [portal._app_json(a) for a in portal._apps if a["mobile"] == mobile]}

        @app.post("/api/applications/{number}/documents/")
        async def upload(number: str, request: Request, authorization: str | None = Header(None)):
            a = portal._find(number, portal._mobile(authorization))
            ctype = request.headers.get("content-type", "")
            if ctype.startswith("multipart/form-data"):  # the real portal (Phase 6 corrections)
                doc_type, data = _multipart(await request.body(), ctype)
            else:  # the fake's own JSON form, used by the browser page's upload
                body = await request.json()
                doc_type, data = body["document_type"], base64.b64decode(body["content_base64"])
            portal.uploads.append((number, doc_type, data))
            a["docs"].add(doc_type)
            return {"document_type": doc_type, "file_size": len(data)}

        @app.post("/api/applications/{number}/submit/")
        async def submit(number: str, request: Request, authorization: str | None = Header(None)):
            a = portal._find(number, portal._mobile(authorization))
            if portal.submit_error:
                return JSONResponse({"error": portal.submit_error}, 400)
            if a["status"] not in ("DRAFT", "CORRECTION_REQUIRED"):
                return JSONResponse({"error": "This application has already been submitted."}, 400)
            a["form_data"].update((await request.json()).get("form_data") or {})
            s = _scheme_json(a["scheme"])
            for f in s["application_fields"]:
                v = a["form_data"].get(f["name"])
                if f["required"] and (v is None or v is False or (isinstance(v, str) and not v.strip())):
                    return JSONResponse({"error": f"{f['label']} is required."}, 400)
            for d in s["documents"]:
                if "where applicable" not in d.lower() and d not in a["docs"]:
                    return JSONResponse({"error": f"{d} is required."}, 400)
            a["status"] = "SUBMITTED"
            portal.submissions.append((a["scheme"], a["number"]))
            return {"application": portal._app_json(a)}

        @app.get("/api/agent/v1/schemes/{sid}/requirements/")
        def requirements(sid: str, x_agent_api_key: str | None = Header(None)):
            if x_agent_api_key != AGENT_KEY:
                return JSONResponse({"error": "Invalid or revoked Agent API Key."}, 401)
            s = _scheme_json(sid)
            fields = s["application_fields"] + ([portal.extra_field] if portal.extra_field else [])
            return {"id": sid, "title": s["title"], "category": s["category"],
                    "application_fields": fields, "required_documents": s["documents"]}

        @app.post("/api/auth/agent-delegation/")
        async def delegation(request: Request, authorization: str | None = Header(None)):
            portal._mobile(authorization)
            body = await request.json()
            return JSONResponse({"delegation_token": "yjs_del_" + secrets.token_hex(16),
                                 "scopes": body.get("scopes"), "expires_at": "2026-10-10T12:00:00Z"}, 201)

        return app


class Tracker:
    """Another host (another port = another origin): it must never be contacted."""

    def __init__(self) -> None:
        self.hits = 0
        app = FastAPI()

        @app.get("/{path:path}")
        def any_path(path: str):
            self.hits += 1
            return HTMLResponse("x")

        self.port = _free_port()
        self.url = f"http://127.0.0.1:{self.port}"
        self._server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=self.port, log_level="warning",
                                                     lifespan="off"))

    def start(self) -> "Tracker":
        threading.Thread(target=self._server.run, daemon=True).start()
        for _ in range(200):
            if self._server.started:
                return self
            time.sleep(0.05)
        raise RuntimeError("tracker did not start")

    def stop(self) -> None:
        self._server.should_exit = True
