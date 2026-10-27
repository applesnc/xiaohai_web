"""New controls with anonymized cloud schemas; browser API is fully mocked."""
import json
from pathlib import Path
import sys
import time

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from devices import command_dps, device_view
from rooms import room_for
from test_returned_schemas import fixtures


def main():
    rows = {row["devId"]: row for row in fixtures()}
    rows["fixture-1"]["_channel_names"] = {"1":"Living strip", "7":"Dining spot", "15":"Hall strip", "101":"Study strip"}
    overrides, commands, writes, errors = {}, {}, [], []

    def snapshot():
        devices = []
        for row in rows.values():
            view = device_view(row)
            view["room"], view["room_source"] = room_for(row, overrides)
            view["command"] = commands.get(row["devId"])
            devices.append(view)
        return {"connected":True, "demo":False, "account":"Browser fixture", "account_key":"schema-fixture",
                "devices":devices, "events":[], "updated_at":time.time(), "stale":False,
                "loading":False, "ready":True, "progress":"", "warning":""}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width":1440,"height":1100})
        page.on("pageerror", lambda error: errors.append(str(error)))

        def intercept(route):
            path = route.request.url.split("/api/", 1)[1]
            data = route.request.post_data_json if route.request.method == "POST" else {}
            if path.startswith("devices/"):
                _, ident, operation = path.split("/")
                if operation == "room":
                    if data["room"]:
                        overrides[ident] = data["room"]
                    else:
                        overrides.pop(ident, None)
                    result = snapshot()
                else:
                    dps = command_dps(rows[ident], data["action"], data["value"])
                    writes.append({"id":ident, "action":data["action"], "dps":dps})
                    rows[ident]["dps"].update(dps)
                    commands[ident] = {"status":"sent" if data["action"] == "scene" else "pending" if data["action"] == "curtain" and data["value"] == "open" else "confirmed"}
                    result = {"accepted":True, "snapshot":snapshot()}
            else:
                result = snapshot()
            route.fulfill(status=200, content_type="application/json", body=json.dumps(result))

        page.route("**/api/**", intercept)
        page.goto("http://127.0.0.1:8766/", wait_until="networkidle")
        assert page.locator(".device-card").count() == 30
        assert page.locator("#stat-rooms").inner_text() == "7"
        assert page.locator("#room-nav .room-button").count() == 8
        page.screenshot(path=str(ROOT / "artifacts" / "desktop-all-devices-fixture.png"))
        for kind, count in (("light",9),("curtain",4),("switch",11),("gateway",1)):
            page.locator(f'[data-type="{kind}"]').click()
            assert page.locator(".device-card").count() == count
        assert page.locator('[data-action="power"], [data-action="fan"]').count() == 0, "Gateway has no invented fan control"
        page.locator('[data-type="light"]').click()
        light = page.locator('[data-id="fixture-5"]')
        light.locator('[data-action="brightness"]').evaluate("e => { e.value = 50; e.dispatchEvent(new Event('input', {bubbles:true})); e.dispatchEvent(new Event('change', {bubbles:true})); }")
        page.wait_for_function("() => !busy.size")
        assert writes[-1]["dps"] == {"2":128}
        multi = page.locator('[data-id="fixture-1"]')
        assert "Study strip" in multi.inner_text()
        multi.locator('[data-channel="switch_led_4"][data-action="channel_power"]').click()
        page.wait_for_function("() => !busy.size")
        assert writes[-1]["dps"] == {"101":True}
        page.screenshot(path=str(ROOT / "artifacts" / "desktop-lights-fixture.png"))
        page.locator('[data-type="curtain"]').click()
        curtain = page.locator('[data-id="fixture-7"]')
        curtain.locator('[data-value="open"]').click()
        page.wait_for_function("() => !busy.size")
        assert curtain.locator('[data-value="close"]').is_disabled()
        assert curtain.locator('[data-value="stop"]').is_enabled()
        curtain.locator('[data-value="stop"]').click()
        page.wait_for_function("() => !busy.size")
        assert writes[-1]["dps"] == {"4":"stop"}
        page.locator('[data-type="switch"]').click()
        panel = page.locator('[data-id="fixture-24"]')
        panel.locator('[data-action="scene"]').click()
        page.wait_for_function("() => !busy.size")
        assert writes[-1]["dps"] == {"3":"scene"}
        assert "\u573a\u666f\u8bf7\u6c42\u5df2\u53d1\u9001" in panel.locator(".device-status").inner_text()
        module = page.locator('[data-id="fixture-13"]')
        module.locator('[data-channel="switch_12"]').click()
        page.wait_for_function("() => !busy.size")
        assert writes[-1]["dps"] == {"106":True}
        page.locator('[data-type="all"]').click()
        page.locator('[data-id="fixture-0"] [data-room-device]').click()
        page.locator("#room-name").fill("\u4e66\u623f")
        page.locator("#room-save").click()
        page.locator("#room-dialog").wait_for(state="hidden")
        assert "\u4e66\u623f" in page.locator('[data-id="fixture-0"] .room-tag').inner_text()
        page.reload(wait_until="networkidle")
        assert "\u4e66\u623f" in page.locator('[data-id="fixture-0"] .room-tag').inner_text()
        page.set_viewport_size({"width":390,"height":844})
        page.locator('[data-type="curtain"]').click()
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        page.screenshot(path=str(ROOT / "artifacts" / "mobile-curtain-fixture.png"), full_page=True)
        assert not errors, errors
        browser.close()
    print(json.dumps({"passed":True, "checks":["30 classified devices", "7 inferred rooms plus whole-home group",
        "0-255 brightness conversion", "multi-channel toggles", "curtain stop during pending command",
        "scene action", "12th relay", "room assignment and reload", "mobile layout"], "real_cloud_writes":0}))


if __name__ == "__main__":
    main()
