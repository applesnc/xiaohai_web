"""Browser acceptance checks using the dashboard's explicit demo mode only."""
import json
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts"
OUT.mkdir(exist_ok=True)


def main():
    results = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width":1440,"height":1100}, device_scale_factor=1)
        errors, console_errors, requests = [], [], []
        page.on("pageerror", lambda err: errors.append(str(err)))
        page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)
        page.on("request", lambda request: requests.append(request.url))
        page.goto("http://127.0.0.1:8766", wait_until="networkidle")
        page.locator("#empty-connect").wait_for(state="visible")
        page.screenshot(path=str(OUT / "desktop-welcome.png"), full_page=True)
        page.locator("#empty-connect").click()
        assert page.locator("#connection-dialog").is_visible()
        page.locator("#username").fill("tester@example.test")
        page.locator("#password").fill("browser-fixture-never-submitted")
        page.locator("#toggle-password").click()
        assert page.locator("#password").get_attribute("type") == "text"
        page.locator("#password").fill("")
        page.screenshot(path=str(OUT / "desktop-connect.png"), full_page=True)
        page.locator('[data-close="connection-dialog"]').click()
        results.append("Connection dialog and password visibility")
        page.locator("#empty-demo").click()
        page.locator(".device-card").first.wait_for(state="visible")
        assert page.locator(".device-card").count() == 6
        assert page.locator("#demo-banner").is_visible()
        page.locator(".toast").wait_for(state="hidden")
        page.screenshot(path=str(OUT / "desktop-demo.png"), full_page=True)
        results.append("Explicit demo with six devices and mode banner")
        ac = page.locator('[data-id="demo-living-ac"]')
        ac.locator('[data-action="temperature"][data-direction="-1"]').click()
        page.wait_for_function("() => document.querySelector('[data-id=\"demo-living-ac\"] .temperature-display').textContent.includes('25')")
        ac.locator('select[data-action="mode"]').select_option("hot")
        page.wait_for_function("() => document.querySelector('[data-id=\"demo-living-ac\"] select[data-action=\"mode\"]').value === 'hot'")
        ac.locator('select[data-action="fan"]').select_option("level_2")
        page.wait_for_function("() => document.querySelector('[data-id=\"demo-living-ac\"] select[data-action=\"fan\"]').value === 'level_2'")
        ac.locator('[data-action="power"]').click()
        page.wait_for_function("() => document.querySelector('[data-id=\"demo-living-ac\"] [data-action=\"power\"]').getAttribute('aria-checked') === 'false'")
        assert page.locator('[data-id="demo-study-ac"] [data-action="power"]').is_disabled()
        results.append("Temperature, mode, speed, power and offline guard")
        ac.locator("[data-favorite]").click()
        page.locator('[data-view="favorites"]').click()
        assert page.locator(".device-card").count() == 1
        page.locator('[data-view="all"]').click()
        page.locator('[data-room="主卧"]').click()
        assert page.locator(".device-card").count() == 2
        page.locator('[data-view="all"]').click()
        page.locator("#search").fill("新风")
        assert page.locator(".device-card").count() == 1
        page.locator("#search").fill("")
        page.locator('[data-type="heating"]').click()
        assert page.locator(".device-card").count() == 1
        page.locator('[data-type="all"]').click()
        page.locator("#online-filter").click()
        assert page.locator(".device-card").count() == 5
        page.locator("#online-filter").click()
        results.append("Favorites, rooms, search, type and online filters")
        page.locator("#activity-open").click()
        assert page.locator(".event").count() >= 5
        page.locator('[data-close="activity-dialog"]').click()
        page.locator("#refresh").click()
        page.wait_for_function("() => !document.querySelector('#refresh').disabled")
        results.append("Activity log and manual refresh")
        page.set_viewport_size({"width":390,"height":844})
        page.screenshot(path=str(OUT / "mobile-demo.png"), full_page=True)
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), "Mobile horizontal overflow"
        page.locator("#room-select").select_option("主卧")
        assert page.locator(".device-card").count() == 2
        page.locator("#room-select").select_option("")
        page.locator("#mobile-activity").click()
        assert page.locator("#activity-dialog").is_visible()
        page.locator('[data-close="activity-dialog"]').click()
        results.append("390px responsive layout, room filter and activity")
        cookies = page.context.cookies()
        cookie = next(item for item in cookies if item["name"] == "xiaohai_local_session")
        assert cookie["httpOnly"] and cookie["sameSite"] == "Strict"
        storage = page.evaluate("JSON.stringify(localStorage)")
        assert "browser-fixture-never-submitted" not in storage
        assert all(url.startswith("http://127.0.0.1:8766") for url in requests), "Unexpected external frontend request"
        page.locator("#connection-open").click()
        page.locator("#disconnect").click()
        page.locator("#empty-state").wait_for(state="visible")
        page.screenshot(path=str(OUT / "mobile-welcome.png"), full_page=True)
        results.append("HttpOnly session, no credential storage, no external assets, logout")
        assert not errors, errors
        assert not console_errors, console_errors
        browser.close()
    report = {"passed":True,"checks":results,"page_errors":errors,"console_errors":console_errors,"network_scope":"loopback only; no real account used"}
    (OUT / "browser-results.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(report,ensure_ascii=True,indent=2))


if __name__ == "__main__":
    main()
