"""Browser-only fixtures verify loading UI; all API requests are intercepted."""
import copy
import json
from pathlib import Path
import sys
import time

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from devices import demo_devices, device_view


def main():
    device = device_view(demo_devices()["demo-living-ac"])
    device["name"] = "Browser fixture"
    device["command"] = None
    state = {"connected": False}
    fixture = {"connected": True, "demo": False, "account": "138****88", "account_key": "fixture-account-a",
               "devices": [device], "events": [], "updated_at": time.time(), "stale": True,
               "loading": True, "ready": False, "progress": "Loading 0 / 12", "warning": ""}
    errors = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.on("pageerror", lambda error: errors.append(str(error)))

        def intercept(route):
            nonlocal state
            path = route.request.url.rsplit("/", 1)[-1]
            if path == "login":
                state = copy.deepcopy(fixture)
            elif path == "logout":
                state = {"connected": False}
            elif path != "state":
                raise AssertionError("Unexpected browser fixture API: " + path)
            route.fulfill(status=200, content_type="application/json", body=json.dumps(state))

        page.route("**/api/**", intercept)
        page.goto("http://127.0.0.1:8766/", wait_until="networkidle")

        def login():
            page.locator("#empty-connect").click()
            page.locator("#username").fill("browser@example.test")
            page.locator("#password").fill("fixture-only")
            page.locator("#login-submit").click()
            page.locator("#connection-dialog").wait_for(state="hidden")
            page.locator(".device-card").wait_for()

        def poll():
            page.wait_for_function("() => !polling")
            page.evaluate("() => { lastPoll = 0; return poll(); }")

        login()
        assert page.locator("#password").input_value() == ""
        assert "0 / 12" in page.locator("#warning-banner").inner_text()
        assert page.locator('[data-action="power"]').is_disabled()
        assert page.locator("#refresh").is_disabled()
        state["progress"] = "Loading 8 / 12"
        poll()
        assert "8 / 12" in page.locator("#warning-banner").inner_text()
        (ROOT / "artifacts").mkdir(exist_ok=True)
        page.screenshot(path=str(ROOT / "artifacts" / "desktop-loading-fixture.png"), full_page=True)
        state.update(loading=False, ready=True, stale=False, progress="")
        poll()
        assert page.locator("#warning-banner").is_hidden()
        assert page.locator('[data-action="power"]').is_enabled()
        page.locator("[data-favorite]").click()
        assert "demo-living-ac" in page.evaluate("localStorage.getItem('xiaohai-favorites:fixture-account-a')")
        page.locator("#connection-open").click()
        page.locator("#disconnect").click()
        page.locator("#empty-connect").wait_for()
        fixture["account_key"] = "fixture-account-b"
        login()
        assert page.evaluate("favorites.size") == 0, "Masked-identical accounts must not share favorites"
        state.update(loading=False, ready=False, warning="Fixture discovery failed; retry available")
        poll()
        assert page.locator('[data-action="power"]').is_disabled()
        page.locator("#connection-open").click()
        assert page.locator("#rediscover").is_enabled()
        assert not errors, errors
        browser.close()
    print(json.dumps({"passed": True, "checks": ["login closes before discovery", "loading progress updates",
        "cached controls disabled until ready", "account favorites isolated", "retry available after failure"],
        "cloud_requests": 0}))


if __name__ == "__main__":
    main()
