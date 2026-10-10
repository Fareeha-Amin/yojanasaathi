"""PlaywrightDriver: the scripted browser agent on the mock portal (design rules 3-7).

Threading: Playwright (async) runs in ONE dedicated thread with its own event loop, a
ProactorEventLoop on Windows (Playwright starts its driver as a subprocess, which needs
Proactor; psycopg stays sync, so nothing else needs a loop). Graph nodes run in FastAPI's
worker threads and call the sync methods here, which hand coroutines to that loop.

One browser context (own cookies + localStorage) per case, kept between turns (the OTP
pause, the confirm pause) and closed after submit, on cancel, or after
BROWSER_IDLE_SECONDS without use. Nothing about a session is stored: the citizen's portal
token lives only in that session's memory and is never logged.

Rules the script keeps:
- only the portal: requests to any host other than MOCK_PORTAL_URL / MOCK_PORTAL_API are
  aborted (and the host logged); a page outside the expected path is a safe-stop
- elements by data-testid only; a missing element, any dialog (alert / confirm) or an
  unexpected page -> SafeStop (hand back, never guess)
- every value typed is read back from the page by its data-testid; a mismatch -> SafeStop
- documents go from the vault to the file input in memory (Playwright file payloads),
  never as a file on disk
- screenshot after every step -> Recorder (encrypted in the vault)
- the OTP is typed into the portal, never logged, never in a screenshot (the field is
  cleared after a wrong code; a right code leaves the page)
- step 4 (review): the declaration box is left UNticked and Submit stays disabled until
  submit() runs, which only the confirm gate's explicit yes reaches
"""

import asyncio
import concurrent.futures
import hashlib
import json
import logging
import re
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from agent import config
from agent.portal import (DocUpload, FillResult, LoginResult, PortalError, SafeStop, SessionGone,
                          Unavailable)
from agent.portal import progress
from agent.portal.api import PortalAPI
from agent.portal.fields import NAME_FIELDS

log = logging.getLogger("yojanasaathi.browser")

APP_NUMBER_RE = re.compile(r"YJS-[0-9A-F]{10}")
EXT = {"application/pdf": "pdf", "image/jpeg": "jpg", "image/png": "png"}
POLL = 0.2  # seconds between checks while waiting for the portal
STEP_MS = 20_000  # an element on an already loaded page
UPLOAD_MS = 90_000  # one document upload (the API may be slow)


def slug(document_name: str) -> str:
    """The portal's test-id slug of a document name (ApplyScheme.jsx: spaces -> "-", lower)."""
    return re.sub(r"\s+", "-", document_name).lower()


@dataclass
class _Session:
    case_id: str
    context: Any
    page: Any
    stage: str = "new"  # new | otp | logged_in | review | done
    scheme: str | None = None
    token: str | None = None  # the citizen's portal token (memory only)
    fingerprint: str | None = None
    last_used: float = field(default_factory=time.monotonic)
    busy: bool = False
    dialog: str | None = None
    blocked: set[str] = field(default_factory=set)
    blocked_logged: set[str] = field(default_factory=set)


class PlaywrightDriver:
    def __init__(self, api: PortalAPI | None = None):
        self.api = api or PortalAPI()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._reaper_future: concurrent.futures.Future | None = None
        self._start_lock = threading.Lock()
        self._pw = None
        self._browser = None
        self._sessions: dict[str, _Session] = {}

    # --- the worker thread -----------------------------------------------------------

    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        with self._start_lock:
            if self._loop is None:
                loop = asyncio.ProactorEventLoop() if sys.platform == "win32" else asyncio.new_event_loop()
                ready = threading.Event()

                def run() -> None:
                    asyncio.set_event_loop(loop)
                    ready.set()
                    loop.run_forever()

                threading.Thread(target=run, name="browser-worker", daemon=True).start()
                ready.wait()
                self._loop = loop
                self._reaper_future = asyncio.run_coroutine_threadsafe(self._reaper(), loop)
            return self._loop

    def _run(self, coro, timeout: float) -> Any:
        fut = asyncio.run_coroutine_threadsafe(coro, self._ensure_loop())
        try:
            return fut.result(timeout)
        except concurrent.futures.TimeoutError:
            fut.cancel()
            raise SafeStop("the portal took too long", detail={"timeout_s": timeout}) from None

    async def _reaper(self) -> None:
        while True:
            await asyncio.sleep(min(30.0, config.BROWSER_IDLE_SECONDS))
            now = time.monotonic()
            for case_id, s in list(self._sessions.items()):
                if not s.busy and now - s.last_used > config.BROWSER_IDLE_SECONDS:
                    await self._close(case_id, "idle")

    async def _ensure_browser(self):
        if self._browser is not None and self._browser.is_connected():
            return self._browser
        from playwright.async_api import async_playwright

        if self._pw is None:
            self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(
            headless=config.BROWSER_HEADLESS, slow_mo=config.BROWSER_SLOWMO_MS,
            args=["--disk-cache-size=1", "--disable-features=Translate"])
        return self._browser

    # --- driver interface (sync, called from graph nodes) -------------------------------

    def state(self) -> str:
        return self.api.state()

    def warmup(self, wait: bool = False) -> None:
        self.api.warmup(wait=wait)

    def requirements(self, scheme_id: str) -> dict[str, Any]:
        return self.api.requirements(scheme_id)

    def start_login(self, case_id: str, mobile: str) -> LoginResult:
        return self._call(case_id, self._start_login(case_id, mobile), config.PORTAL_FIRST_TIMEOUT * 2)

    def verify_otp(self, case_id: str, code: str) -> bool:
        return self._call(case_id, self._verify_otp(case_id, code), config.PORTAL_FIRST_TIMEOUT + 30)

    def resend_otp(self, case_id: str) -> None:
        return self._call(case_id, self._resend_otp(case_id), config.PORTAL_FIRST_TIMEOUT + 30)

    def existing_application(self, case_id: str, scheme_id: str) -> dict[str, Any] | None:
        s = self._sessions.get(case_id)
        if s is None or not s.token:
            raise SessionGone("not logged in to the portal")
        for a in self.api.my_applications(s.token):
            if a.get("scheme") == scheme_id and a.get("status") not in ("DRAFT", None):
                return {"app_id": a.get("application_number"), "status": a.get("status")}
        return None

    def fill(self, case_id: str, scheme: dict[str, Any], values: dict[str, Any],
             docs: list[DocUpload]) -> FillResult:
        return self._call(case_id, self._fill(case_id, scheme, values, docs),
                          config.PORTAL_FIRST_TIMEOUT * 2 + 60 * max(1, len(docs)))

    def fresh(self, case_id: str, scheme_id: str, fingerprint: str) -> bool:
        s = self._sessions.get(case_id)
        return bool(s and s.stage == "review" and s.scheme == scheme_id and s.token
                    and s.fingerprint == fingerprint and not s.page.is_closed())

    def submit(self, case_id: str, scheme_id: str) -> str:
        return self._call(case_id, self._submit(case_id, scheme_id), config.PORTAL_FIRST_TIMEOUT + 60)

    def delegate(self, case_id: str):
        s = self._sessions.get(case_id)
        if s is None or not s.token:
            raise SessionGone("not logged in to the portal")
        return self.api.delegate(s.token)

    # --- Phase 6: corrections (portal citizen API with this session's token) --------------------

    def _token(self, case_id: str) -> str:
        s = self._sessions.get(case_id)
        if s is None or s.page.is_closed() or not s.token or s.stage not in ("logged_in", "review", "done"):
            raise SessionGone("not logged in to the portal")
        s.last_used = time.monotonic()
        return s.token

    def application_status(self, case_id: str, app_id: str) -> str | None:
        return self.api.application_status(self._token(case_id), app_id)

    def replace_document(self, case_id: str, app_id: str, doc: DocUpload) -> None:
        ext = EXT.get(doc.content_type or "")
        read_doc = progress.recorder().read_doc
        if doc.doc_id is None or ext is None or read_doc is None:
            raise SafeStop("document the portal does not take", step="correction", detail={"doc": doc.doc})
        token = self._token(case_id)
        data = read_doc(case_id, doc.doc_id)  # decrypted to memory only, audited as browser_agent
        try:
            self.api.replace_document(token, app_id, doc.name, f"{doc.doc}.{ext}", doc.content_type, data)
        finally:
            del data
        progress.recorder().audit("browser_agent", "document_replaced", case_id, None,
                                  {"doc_type": doc.doc, "app_id": app_id})

    def resubmit(self, case_id: str, app_id: str) -> str:
        return self.api.resubmit(self._token(case_id), app_id)

    def stage(self, case_id: str) -> str | None:
        s = self._sessions.get(case_id)
        if s is None or s.page.is_closed():
            return None
        return s.stage

    def close(self, case_id: str, reason: str = "done") -> None:
        if case_id in self._sessions and self._loop is not None:
            self._run(self._close(case_id, reason), 30)

    def shutdown(self) -> None:
        if self._loop is None:
            return

        async def stop() -> None:
            for case_id in list(self._sessions):
                await self._close(case_id, "shutdown")
            if self._browser is not None:
                await self._browser.close()
            if self._pw is not None:
                await self._pw.stop()

        try:
            self._run(stop(), 30)
        except Exception as e:  # noqa: BLE001
            log.warning("browser shutdown: %s", type(e).__name__)
        if self._reaper_future is not None:
            self._reaper_future.cancel()
            time.sleep(0.1)  # let the loop finish cancelling it
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._loop, self._pw, self._browser = None, None, None

    def _call(self, case_id: str, coro, timeout: float) -> Any:
        """Run one step for a case; Playwright errors become portal errors."""
        try:
            return self._run(self._guard(case_id, coro), timeout)
        except PortalError:
            raise
        except Exception as e:  # noqa: BLE001
            name = type(e).__name__
            if "closed" in str(e).lower() or "Target" in name:
                raise SessionGone(f"browser session ended ({name})") from None
            raise SafeStop(f"browser error ({name})") from None

    async def _guard(self, case_id: str, coro):
        s = self._sessions.get(case_id)
        if s:
            s.busy = True
        try:
            return await coro
        finally:
            s = self._sessions.get(case_id)
            if s:
                s.busy = False
                s.last_used = time.monotonic()
                self._log_blocked(s)

    # --- sessions ------------------------------------------------------------------------

    @staticmethod
    def _hosts() -> set[str]:
        return {urlsplit(u).netloc.lower() for u in (config.MOCK_PORTAL_URL, config.MOCK_PORTAL_API) if u}

    async def _open(self, case_id: str) -> _Session:
        if not config.portal_ready():
            raise Unavailable("portal settings missing or invalid")
        browser = await self._ensure_browser()
        context = await browser.new_context(locale="en-IN", viewport={"width": 1280, "height": 900},
                                            service_workers="block", accept_downloads=False)
        # The portal in English, so the page text matches the seed's English labels.
        await context.add_init_script("try { localStorage.setItem('portal_language', 'en') } catch (e) {}")
        page = await context.new_page()
        s = _Session(case_id, context, page)
        allowed = self._hosts()

        async def route(r, request) -> None:
            u = urlsplit(request.url)
            if u.scheme in ("http", "https") and u.netloc.lower() in allowed:
                await r.continue_()
            else:
                s.blocked.add(u.netloc.lower() or u.scheme)
                await r.abort("blockedbyclient")

        async def on_dialog(dialog) -> None:
            s.dialog = dialog.type  # never its message: it may echo what was typed
            await dialog.dismiss()

        await context.route("**/*", route)
        page.on("dialog", on_dialog)
        page.set_default_timeout(STEP_MS)
        self._sessions[case_id] = s
        return s

    async def _close(self, case_id: str, reason: str) -> None:
        s = self._sessions.pop(case_id, None)
        if s is None:
            return
        try:
            await s.context.close()
        except Exception:  # noqa: BLE001 (already gone)
            pass
        progress.recorder().audit("browser_agent", "portal_session_closed", case_id, s.scheme,
                                  {"reason": reason, "stage": s.stage})

    def _session(self, case_id: str, *stages: str) -> _Session:
        s = self._sessions.get(case_id)
        if s is None or s.page.is_closed() or (stages and s.stage not in stages):
            raise SessionGone("no live portal session at this step",
                              detail={"stage": s.stage if s else None})
        return s

    def _log_blocked(self, s: _Session) -> None:
        for host in sorted(s.blocked - s.blocked_logged):
            s.blocked_logged.add(host)
            progress.recorder().audit("browser_agent", "offhost_blocked", s.case_id, s.scheme, {"host": host})

    # --- page helpers ------------------------------------------------------------------

    async def _shot(self, s: _Session, step: str, status: str) -> str | None:
        try:
            png = await s.page.screenshot(full_page=True)
        except Exception:  # noqa: BLE001
            png = None
        return progress.recorder().step(s.case_id, s.scheme, step, status, png)

    async def _stop(self, s: _Session, step: str, message: str, **detail: Any) -> SafeStop:
        shot = await self._shot(s, step, "error")
        return SafeStop(message, step=step, detail={**detail, "path": urlsplit(s.page.url).path},
                        screenshot=shot)

    async def _check(self, s: _Session, step: str, path: str | None = None) -> None:
        """After every action: no dialog, still on the portal, on the expected page."""
        if s.dialog:
            kind, s.dialog = s.dialog, None
            raise await self._stop(s, step, f"the portal showed a {kind} dialog", dialog=kind)
        u = urlsplit(s.page.url)
        site = urlsplit(config.MOCK_PORTAL_URL or "")
        if u.netloc.lower() != site.netloc.lower():
            raise await self._stop(s, step, "the browser left the portal")
        if path and not re.fullmatch(path, u.path.rstrip("/") or "/"):
            raise await self._stop(s, step, "unexpected page", expected=path)

    async def _el(self, s: _Session, testid: str, step: str, *, timeout: float = STEP_MS,
                  state: str = "visible"):
        loc = s.page.get_by_test_id(testid)
        try:
            await loc.first.wait_for(state=state, timeout=timeout)
        except Exception as e:  # noqa: BLE001 (Playwright TimeoutError)
            await self._check(s, step)  # a dialog explains it better than "missing"
            raise await self._stop(s, step, "expected element missing", testid=testid,
                                   error=type(e).__name__) from None
        if await loc.count() != 1:
            raise await self._stop(s, step, "element is not unique", testid=testid)
        return loc

    async def _visible(self, s: _Session, testid: str) -> bool:
        loc = s.page.get_by_test_id(testid)
        return await loc.count() > 0 and await loc.first.is_visible()

    async def _goto(self, s: _Session, path: str, step: str) -> None:
        try:
            await s.page.goto(config.MOCK_PORTAL_URL + path, wait_until="domcontentloaded",
                              timeout=config.PORTAL_FIRST_TIMEOUT * 1000)
        except Exception as e:  # noqa: BLE001
            raise await self._stop(s, step, "the portal page did not load", error=type(e).__name__) from None

    async def _set(self, s: _Session, testid: str, step: str, value: str) -> str:
        loc = await self._el(s, testid, step)
        await loc.fill(value)
        got = await loc.input_value()
        if got != value:
            raise await self._stop(s, step, "the page shows a different value than typed", testid=testid)
        return got

    # --- login + OTP --------------------------------------------------------------------

    async def _start_login(self, case_id: str, mobile: str) -> LoginResult:
        await self._close(case_id, "restart")
        s = await self._open(case_id)
        progress.start(case_id)
        progress.update(case_id, "login", "running")
        await self._goto(s, "/citizen-access", "login")
        await self._el(s, "otp-mobile-input", "login", timeout=config.PORTAL_FIRST_TIMEOUT * 1000)
        await self._check(s, "login", r"/citizen-access")
        await self._set(s, "otp-mobile-input", "login", mobile)
        await (await self._el(s, "otp-send-button", "login")).click()
        deadline = time.monotonic() + config.PORTAL_FIRST_TIMEOUT
        while time.monotonic() < deadline:
            await self._check(s, "login", r"/citizen-access")
            if await self._visible(s, "otp-code-input"):
                s.stage = "otp"
                await self._shot(s, "login", "done")
                progress.update(case_id, "otp", "running")
                return LoginResult(sent=True)
            if await self._visible(s, "otp-error-message"):
                await self._shot(s, "login", "error")
                await self._close(case_id, "mobile_refused")
                return LoginResult(sent=False, refused=True)
            await asyncio.sleep(POLL)
        raise await self._stop(s, "login", "no OTP screen after sending")

    async def _verify_otp(self, case_id: str, code: str) -> bool:
        s = self._session(case_id, "otp")
        err = s.page.get_by_test_id("otp-error-message")
        had_error = await self._visible(s, "otp-error-message")
        box = await self._el(s, "otp-code-input", "otp")
        await box.fill(code)
        await (await self._el(s, "otp-submit-button", "otp")).click()
        if had_error:  # the old message goes away when the portal starts checking the new code
            try:
                await err.first.wait_for(state="hidden", timeout=5000)
            except Exception:  # noqa: BLE001
                pass
        deadline = time.monotonic() + config.PORTAL_FIRST_TIMEOUT
        while time.monotonic() < deadline:
            await self._check(s, "otp")
            if not urlsplit(s.page.url).path.startswith("/citizen-access"):
                token = await s.page.evaluate("() => window.localStorage.getItem('citizen_token')")
                if not token:
                    raise await self._stop(s, "otp", "logged in, but the portal gave no session token")
                s.token, s.stage = token, "logged_in"
                await self._shot(s, "otp", "done")
                return True
            if await self._visible(s, "otp-error-message"):
                await box.fill("")  # the code must not stay on the page (or in the screenshot)
                await self._shot(s, "otp", "running")
                return False
            await asyncio.sleep(POLL)
        await box.fill("")
        raise await self._stop(s, "otp", "no answer from the portal after the code")

    async def _resend_otp(self, case_id: str) -> None:
        s = self._session(case_id, "otp")
        await (await self._el(s, "otp-resend-button", "otp")).click()
        deadline = time.monotonic() + config.PORTAL_FIRST_TIMEOUT
        while time.monotonic() < deadline:
            await self._check(s, "otp", r"/citizen-access")
            if await self._visible(s, "otp-success-message") or await self._visible(s, "otp-code-input"):
                await asyncio.sleep(POLL)
                if not await self._visible(s, "otp-error-message"):
                    return
            if await self._visible(s, "otp-error-message"):
                raise await self._stop(s, "otp", "the portal could not send a new code")
            await asyncio.sleep(POLL)
        raise await self._stop(s, "otp", "no answer after asking for a new code")

    # --- the application form ---------------------------------------------------------

    async def _fill(self, case_id: str, scheme: dict[str, Any], values: dict[str, Any],
                    docs: list[DocUpload]) -> FillResult:
        s = self._session(case_id, "logged_in", "review")
        sid = scheme["scheme_id"]
        apply_path = rf"/apply/{re.escape(sid)}"
        token = await s.page.evaluate("() => window.localStorage.getItem('citizen_token')")
        if not token:
            raise SessionGone("the portal logged this session out")
        if s.stage == "review" and s.scheme == sid:  # an edit: back to step 1, fill again
            for back, wait in (("apply-step4-back-btn", "apply-step3-next-btn"),
                               ("apply-step3-back-btn", "apply-step2-next-btn"),
                               ("apply-step2-back-btn", "apply-step1-next-btn")):
                await (await self._el(s, back, "review")).click()
                await self._el(s, wait, "review")
                await self._check(s, "review", apply_path)
        else:
            s.scheme = sid
            await self._goto(s, f"/apply/{sid}", "applicant")
            first = s.page.get_by_test_id("apply-full-name-input").or_(
                s.page.get_by_test_id("apply-confirmation-app-number"))
            try:
                await first.first.wait_for(state="visible", timeout=config.PORTAL_FIRST_TIMEOUT * 1000)
            except Exception:  # noqa: BLE001
                await self._check(s, "applicant")
                raise await self._stop(s, "applicant", "expected element missing",
                                       testid="apply-full-name-input") from None
            if await self._visible(s, "apply-confirmation-app-number"):  # already submitted
                number = (await s.page.get_by_test_id("apply-confirmation-app-number").inner_text()).strip()
                await self._shot(s, "applicant", "done")
                return FillResult({}, [], "", already={"app_id": number, "status": "SUBMITTED"})
        s.stage, s.fingerprint = "logged_in", None
        await self._check(s, "applicant", apply_path)
        shown: dict[str, Any] = {}

        # Step 1: applicant details
        progress.update(case_id, "applicant", "running")
        name = next((values[f] for f in NAME_FIELDS if values.get(f)), None)
        if name:
            await self._set(s, "apply-full-name-input", "applicant", name)
        if values.get("address"):
            await self._set(s, "apply-address-input", "applicant", values["address"])
        await self._check(s, "applicant", apply_path)
        await self._shot(s, "applicant", "done")
        await (await self._el(s, "apply-step1-next-btn", "applicant")).click()
        await self._el(s, "apply-step2-next-btn", "details")

        # Step 2: the scheme's own fields
        progress.update(case_id, "details", "running")
        for f in scheme.get("application_fields", []):
            name_, kind = f["name"], f["type"]
            v = values.get(name_)
            if v is None:
                if f.get("required"):
                    raise await self._stop(s, "details", "no value for a required field", field=name_)
                continue
            tid = f"apply-field-{name_}"
            if kind == "select":
                loc = await self._el(s, tid, "details")
                await loc.select_option(value=str(v))
                shown[name_] = await loc.input_value()
            elif kind == "radio":
                loc = await self._el(s, f"{tid}-{v}", "details")
                await loc.check()
                shown[name_] = str(v) if await loc.is_checked() else None
            elif kind == "checkbox":
                loc = await self._el(s, tid, "details")
                await (loc.check() if v else loc.uncheck())
                shown[name_] = await loc.is_checked()
            else:
                shown[name_] = await self._set(s, tid, "details", str(v))
            if shown[name_] != (v if kind == "checkbox" else str(v)):
                raise await self._stop(s, "details", "the page shows a different value than chosen", field=name_)
            await self._check(s, "details", apply_path)
        await self._shot(s, "details", "done")
        await (await self._el(s, "apply-step2-next-btn", "details")).click()
        try:
            await s.page.get_by_test_id("apply-step3-next-btn").first.wait_for(state="visible", timeout=STEP_MS)
        except Exception:  # noqa: BLE001
            await self._check(s, "details")
            raise await self._stop(s, "details", "the portal did not accept the details") from None

        # Step 3: documents, from the vault straight into the file inputs (memory only)
        progress.update(case_id, "documents", "running")
        uploaded: list[str] = []
        read_doc = progress.recorder().read_doc
        for d in docs:
            sl = slug(d.name)
            badge = f"doc-uploaded-badge-{sl}"
            if await self._visible(s, badge):
                uploaded.append(d.name)
                continue
            if d.doc_id is None:
                continue  # optional ("where applicable") and not provided
            inp = await self._el(s, f"doc-file-input-{sl}", "documents", state="attached")
            ext = EXT.get(d.content_type or "")
            if ext is None or read_doc is None:
                raise await self._stop(s, "documents", "document type the portal does not take", doc=d.doc)
            data = read_doc(case_id, d.doc_id)
            try:
                await inp.set_input_files(files=[{"name": f"{d.doc}.{ext}", "mimeType": d.content_type,
                                                  "buffer": data}])
            finally:
                del data
            await self._el(s, badge, "documents", timeout=UPLOAD_MS)
            await self._check(s, "documents", apply_path)
            uploaded.append(d.name)
            progress.recorder().audit("browser_agent", "document_uploaded", case_id, sid, {"doc_type": d.doc})
        await self._shot(s, "documents", "done")
        await (await self._el(s, "apply-step3-next-btn", "documents")).click()
        try:
            await s.page.get_by_test_id("apply-submit-btn").first.wait_for(state="visible", timeout=STEP_MS)
        except Exception:  # noqa: BLE001
            await self._check(s, "documents")
            raise await self._stop(s, "documents", "the portal did not accept the documents") from None

        # Step 4: review. The declaration stays unticked; Submit must be disabled.
        progress.update(case_id, "review", "running")
        decl = await self._el(s, "apply-declaration-checkbox", "review")
        if await decl.is_checked():
            await decl.uncheck()
        if await decl.is_checked() or not await (await self._el(s, "apply-submit-btn", "review")).is_disabled():
            raise await self._stop(s, "review", "Submit is not locked behind the declaration")
        await self._check(s, "review", apply_path)
        s.stage = "review"
        s.fingerprint = hashlib.sha256(json.dumps(
            {"scheme": sid, "values": shown, "documents": uploaded}, sort_keys=True,
            ensure_ascii=False).encode()).hexdigest()
        await self._shot(s, "review", "done")
        progress.update(case_id, "submit", "waiting")
        return FillResult(shown, uploaded, s.fingerprint)

    async def _submit(self, case_id: str, scheme_id: str) -> str:
        s = self._session(case_id, "review")
        if s.scheme != scheme_id:
            raise SessionGone("the open form is for another scheme")
        apply_path = rf"/apply/{re.escape(scheme_id)}"
        await self._check(s, "submit", apply_path)
        progress.update(case_id, "submit", "running")
        decl = await self._el(s, "apply-declaration-checkbox", "submit")
        await decl.check()
        button = await self._el(s, "apply-submit-btn", "submit")
        if not await decl.is_checked() or await button.is_disabled():
            raise await self._stop(s, "submit", "could not tick the declaration")
        await button.click()
        deadline = time.monotonic() + config.PORTAL_FIRST_TIMEOUT
        while time.monotonic() < deadline:
            if s.dialog:
                await self._check(s, "submit")
            if await self._visible(s, "apply-confirmation-app-number"):
                number = (await s.page.get_by_test_id("apply-confirmation-app-number").inner_text()).strip()
                if not APP_NUMBER_RE.fullmatch(number):
                    raise await self._stop(s, "submit", "unexpected application number format")
                s.stage = "done"
                await self._shot(s, "submit", "done")
                return number
            if await self._visible(s, "apply-error-banner"):
                raise await self._stop(s, "submit", "the portal reported an error on submit")
            await asyncio.sleep(POLL)
        raise await self._stop(s, "submit", "no application number after submit")
