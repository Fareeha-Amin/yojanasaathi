"""Live check of the mock portal, safe to run any time:

    .\\.venv\\Scripts\\python.exe -m agent.portal.smoke

1. settings (URLs, agent key) are present and well formed (values are never printed)
2. warm-up: wake the portal API (Render sleeps) and time it
3. requirements of all 4 schemes via the agent API
4. drift: each answer compared with rules/ and our field map
5. the browser opens /citizen-access and finds the mobile input + send button

It sends NO OTP (the mobile is never typed) and submits nothing. Exit 0 = all good.
"""

import sys
import time

from agent import config, rules
from agent.portal import drift
from agent.portal.api import PortalAPI
from agent.portal.browser import PlaywrightDriver

OK, BAD = "ok  ", "FAIL"


def main() -> int:
    failed = 0

    def report(ok: bool, text: str) -> None:
        nonlocal failed
        failed += not ok
        print(f"[{OK if ok else BAD}] {text}")

    warnings = config.portal_url_warnings()
    report(not warnings, "settings: " + ("MOCK_PORTAL_URL, MOCK_PORTAL_API, MOCK_PORTAL_AGENT_KEY look right"
                                         if not warnings else "; ".join(warnings)))
    if warnings:
        return 1

    api = PortalAPI()
    t0 = time.monotonic()
    api.warmup(wait=True)
    report(api.state() == "ready", f"warm-up: portal API {api.state()} after {time.monotonic() - t0:.1f} s")
    if api.state() != "ready":
        return 1

    for sid, scheme in rules.load_schemes().items():
        try:
            diffs = drift.compare(api.requirements(sid), scheme)
        except Exception as e:  # noqa: BLE001
            report(False, f"{sid}: requirements not readable ({type(e).__name__}: {e})")
            continue
        report(not diffs, f"{sid}: requirements match rules/" if not diffs else f"{sid}: DRIFT {diffs}")

    driver = PlaywrightDriver(api)
    try:
        t0 = time.monotonic()
        # The same steps as start_login up to (not including) typing the mobile.
        ok, detail = driver._run(_login_page(driver), config.PORTAL_FIRST_TIMEOUT + 60)
        report(ok, f"login page: {detail} ({time.monotonic() - t0:.1f} s)")
    except Exception as e:  # noqa: BLE001
        report(False, f"login page: {type(e).__name__}: {e}")
    finally:
        driver.shutdown()
    print("smoke: " + ("all good (no OTP sent, nothing submitted)" if not failed else f"{failed} check(s) failed"))
    return 1 if failed else 0


async def _login_page(driver: PlaywrightDriver) -> tuple[bool, str]:
    s = await driver._open("smoke")
    try:
        await driver._goto(s, "/citizen-access", "login")
        await driver._el(s, "otp-mobile-input", "login", timeout=config.PORTAL_FIRST_TIMEOUT * 1000)
        await driver._el(s, "otp-send-button", "login")
        await driver._check(s, "login", r"/citizen-access")
        return True, "otp-mobile-input and otp-send-button found"
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"
    finally:
        await driver._close("smoke", "smoke")


if __name__ == "__main__":
    sys.exit(main())
