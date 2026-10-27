"""End-to-end HTTP checks against a local simulated Xiaohai cloud only."""
import asyncio
import copy
import json
import socket
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from aiohttp import web, CookieJar
from aiohttp.test_utils import TestClient, TestServer

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from devices import InputError, command_dps, demo_devices, device_view
from server import COOKIE, SESSIONS, create_app
from local_store import AccountStore
from test_returned_schemas import fixtures
from cloudio.wire import ClientProfile


class DeviceChecks(unittest.TestCase):
    def setUp(self):
        self.device = demo_devices()["demo-living-ac"]

    def test_offline_commands_rejected(self):
        self.device["isOnline"] = False
        with self.assertRaises(InputError):
            command_dps(self.device, "power", True)

    def test_temperature_range_type_and_step(self):
        for value in (15, 31, 24.5, True, float("nan"), "24"):
            with self.subTest(value=value), self.assertRaises(InputError):
                command_dps(self.device, "temperature", value)
        self.assertEqual(command_dps(self.device, "temperature", 24), {"2": 24})

    def test_scaled_temperature(self):
        self.device["_schema"]["@limit:temp_set"] = '{"min":160,"max":300,"step":5,"scale":1}'
        self.device["dps"]["2"] = 245
        self.assertEqual(device_view(self.device)["target"], 24.5)
        self.assertEqual(command_dps(self.device, "temperature", 25.5), {"2": 255})

    def test_unknown_limits_are_read_only(self):
        self.device["_schema"].pop("@limit:temp_set")
        self.assertFalse(device_view(self.device)["can_temperature"])
        with self.assertRaises(InputError):
            command_dps(self.device, "temperature", 25)

    def test_schema_missing_does_not_guess_speed_commands(self):
        self.device["_schema"] = {}
        self.device["dps"] = {"12": "level1"}
        self.assertFalse(device_view(self.device)["can_fan"])

    def test_mode_and_speed_do_not_power_on(self):
        self.device["dps"]["1"] = False
        self.assertEqual(command_dps(self.device, "mode", "hot"), {"4": "hot"})
        self.assertEqual(command_dps(self.device, "fan", "level_2"), {"5": "level_2"})
        with self.assertRaises(InputError):
            command_dps(self.device, "mode", "invented")


class DashboardChecks(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.published = []
        self.login_count = 0
        self.reject_login = False
        self.expire_session_once = False
        self.reject_write = False
        self.report_write = True
        self.cloud_fail = False
        self.cloud_actions = []
        self.gate_action = None
        self.gate = asyncio.Event()
        self.gate_entered = asyncio.Event()
        self.extra_devices = []
        self.missing_versions = False
        self.version_active = self.version_peak = 0
        self.cloud_device = {"devId": "fixture-ac", "name": "测试空调", "productId": "fixture-product",
                             "productVer": "1.0", "parentId": "fixture-gateway", "isOnline": True,
                             "dps": {"1": True, "2": 26, "3": 24, "4": "cold", "5": "auto"}}
        cloud = web.Application()
        cloud.router.add_post("/api.json", self.cloud_request)
        self.cloud = TestServer(cloud)
        await self.cloud.start_server()
        profile = ClientProfile(str(self.cloud.make_url("/api.json")), "fixture-app", "fixture-signing-secret")
        self.api_patch = patch("cloudio.client.ClientProfile.load", return_value=profile)
        self.api_patch.start()
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        self.origin = f"http://127.0.0.1:{port}"
        self.cache_temp = tempfile.TemporaryDirectory()
        self.app = create_app(port, cache_dir=self.cache_temp.name)
        self.client = TestClient(TestServer(self.app, port=port), cookie_jar=CookieJar(unsafe=True))
        await self.client.start_server()
        self.headers = {"Origin": self.origin, "X-Local-Request": "1"}

    async def asyncTearDown(self):
        self.gate.set()
        await self.client.close()
        await self.cloud.close()
        self.api_patch.stop()
        self.cache_temp.cleanup()

    async def cloud_request(self, request):
        form = await request.post()
        action = form.get("a")
        self.cloud_actions.append(action)
        data = json.loads(form["postData"]) if form.get("postData") else {}
        if action == self.gate_action:
            self.gate_entered.set()
            await self.gate.wait()
        if self.cloud_fail:
            return web.json_response({"success": False, "errorCode": "TEMPORARY_FAILURE"})
        if self.expire_session_once and action == "tuya.m.location.list":
            self.expire_session_once = False
            return web.json_response({"success": False, "errorCode": "USER_SESSION_INVALID"})
        if action in {"tuya.m.user.mobile.token.get", "tuya.m.user.email.token.create"}:
            result = {"token": "fixture-token"}
        elif action in {"tuya.m.user.mobile.passwd.login", "tuya.m.user.email.password.login"}:
            self.login_count += 1
            if self.reject_login:
                return web.json_response({"success": False, "errorCode": "USER_PASSWD_WRONG"})
            result = {"sid": "fixture-cloud-session"}
        elif action == "tuya.m.location.list":
            result = [{"groupId": 1}]
        elif action == "tuya.m.my.group.device.list":
            result = copy.deepcopy([self.cloud_device, *self.extra_devices])
        elif action == "tuya.m.room.list":
            result = [{"id": 10, "name": "测试房间"}]
        elif action == "tuya.m.my.group.device.sort.list":
            result = [{"bizId": "fixture-ac", "bizType": 6, "roomId": 10}]
        elif action == "tuya.m.product.standard.config.list":
            result = []
        elif action == "tuya.m.device.ext.prop.list":
            result = [] if self.missing_versions else [{"devId": "fixture-ac", "productVer": "1.0"}]
        elif action == "tuya.m.device.get":
            self.version_active += 1
            self.version_peak = max(self.version_peak, self.version_active)
            try:
                await asyncio.sleep(0.02)
                result = {"devId": data["devId"], "productVer": "1.0"}
            finally:
                self.version_active -= 1
        elif action == "tuya.m.device.ref.info.list":
            schema = [
                {"id": "1", "code": "switch", "property": {"type": "bool"}},
                {"id": "2", "code": "temp_set", "property": {"type": "value", "min": 16, "max": 30, "scale": 0, "step": 1}},
                {"id": "3", "code": "temp_current", "property": {"type": "value", "scale": 0}},
                {"id": "4", "code": "mode", "property": {"type": "enum", "range": ["cold", "hot", "wind"]}},
                {"id": "5", "code": "fan_speed_enum", "property": {"type": "enum", "range": ["auto", "level_1", "level_2", "level_3"]}},
            ]
            result = [{"id": "fixture-product", "productVer": "1.0", "schemaInfo": {"schema": json.dumps(schema)}}]
        elif action == "tuya.m.device.dp.publish":
            self.published.append(data)
            if self.reject_write:
                return web.json_response({"success": False, "errorCode": "DEVICE_REJECTED"})
            if self.report_write:
                self.cloud_device["dps"].update(json.loads(data["dps"]))
            result = True
        else:
            return web.json_response({"success": False, "errorCode": "UNEXPECTED_TEST_ACTION"})
        return web.json_response({"success": True, "result": result})

    async def post(self, path, data):
        return await self.client.post(path, json=data, headers=self.headers)

    async def login(self, demo=False, wait_ready=True, username="tester@example.test"):
        data = {"demo": True} if demo else {"username": username, "password": "only-a-local-fixture", "country_code": "86"}
        response = await self.post("/api/login", data)
        self.assertEqual(response.status, 200, await response.text())
        result = await response.json()
        if wait_ready and not demo:
            async with asyncio.timeout(3):
                while result["loading"]:
                    await asyncio.sleep(0.01)
                    result = await (await self.client.get("/api/state")).json()
            self.assertTrue(result["ready"], result.get("warning"))
        return result

    async def test_slow_discovery_does_not_block_login_state_or_logout(self):
        self.gate_action = "tuya.m.room.list"
        data = await asyncio.wait_for(self.login(wait_ready=False), 1)
        self.assertTrue(data["connected"])
        self.assertTrue(data["loading"])
        await asyncio.wait_for(self.gate_entered.wait(), 1)
        state = await asyncio.wait_for(self.client.get("/api/state"), 0.5)
        self.assertTrue((await state.json())["loading"])
        control = await self.post("/api/devices/fixture-ac/control", {"action": "power", "value": False})
        self.assertEqual(control.status, 400)
        self.assertFalse(self.published)
        session = next(iter(self.app[SESSIONS].values()))
        await asyncio.wait_for(self.post("/api/logout", {}), 0.5)
        self.assertFalse(self.app[SESSIONS])
        self.assertTrue(session.http.closed)
        self.assertFalse(session.tasks)

    async def test_discovery_timeout_preserves_login_and_allows_retry(self):
        self.gate_action = "tuya.m.room.list"
        with patch("server.DISCOVERY_TIMEOUT", 0.1):
            await self.login(wait_ready=False)
            await asyncio.sleep(0.2)
        data = await (await self.client.get("/api/state")).json()
        self.assertTrue(data["connected"])
        self.assertFalse(data["loading"])
        self.assertFalse(data["ready"])
        self.assertTrue(data["stale"])
        self.gate.set()
        result = await self.post("/api/refresh", {"discover": True})
        self.assertTrue((await result.json())["loading"])
        async with asyncio.timeout(2):
            while next(iter(self.app[SESSIONS].values())).loading:
                await asyncio.sleep(0.01)
        data = await (await self.client.get("/api/state")).json()
        self.assertTrue(data["ready"])
        self.assertEqual(self.login_count, 1)

    async def test_fallback_version_reads_are_bounded_and_complete(self):
        self.missing_versions = True
        self.cloud_device.pop("productVer")
        self.extra_devices = [{**copy.deepcopy(self.cloud_device), "devId": f"fixture-{i}"} for i in range(12)]
        result = await self.login()
        self.assertEqual(len(result["devices"]), 13)
        self.assertEqual(self.cloud_actions.count("tuya.m.device.get"), 13)
        self.assertGreater(self.version_peak, 1)
        self.assertLessEqual(self.version_peak, 4)

    async def test_account_cache_survives_logout_and_is_isolated(self):
        first = await self.login(username="13800000088")
        await self.post("/api/logout", {})
        self.gate_action = "tuya.m.location.list"
        cached = await self.login(wait_ready=False, username="13800000088")
        self.assertEqual(cached["devices"][0]["id"], first["devices"][0]["id"])
        self.assertTrue(cached["stale"])
        self.assertFalse(cached["ready"])
        await self.post("/api/logout", {})
        second = await self.login(wait_ready=False, username="13811111188")
        self.assertEqual(first["account"], second["account"], "Fixture must collide when masked")
        self.assertNotEqual(first["account_key"], second["account_key"])
        self.assertEqual(second["devices"], [])
        contents = "".join(path.read_text(encoding="utf-8") for path in Path(self.cache_temp.name).glob("*.json"))
        for secret in ("only-a-local-fixture", "fixture-cloud-session", "13800000088", "13811111188"):
            self.assertNotIn(secret, contents)

    async def test_cache_write_failure_does_not_fail_cloud_connection(self):
        with patch.object(AccountStore, "save", side_effect=OSError("fixture")):
            data = await self.login()
        self.assertTrue(data["ready"])
        self.assertTrue(data["warning"])

    async def test_unauthenticated_state_and_control(self):
        result = await self.client.get("/api/state")
        self.assertEqual(await result.json(), {"connected": False})
        result = await self.post("/api/devices/fixture-ac/control", {"action": "power", "value": True})
        self.assertEqual(result.status, 401)

    async def test_cross_origin_and_host_rejected(self):
        result = await self.client.post("/api/login", json={"demo": True})
        self.assertEqual(result.status, 403)
        result = await self.client.post("/api/login", json={"demo": True}, headers={"Origin": "https://evil.example", "X-Local-Request": "1"})
        self.assertEqual(result.status, 403)
        result = await self.client.get("/api/state", headers={"Host": "evil.example"})
        self.assertEqual(result.status, 403)
        self.assertFalse(self.app[SESSIONS])

    async def test_cloud_discovery_and_credential_boundaries(self):
        data = await self.login()
        self.assertFalse(data["demo"])
        self.assertEqual(data["devices"][0]["room"], "测试房间")
        self.assertTrue(data["devices"][0]["can_temperature"])
        self.assertNotIn("only-a-local-fixture", json.dumps(data))
        self.assertNotIn("fixture-cloud-session", json.dumps(data))
        cookies = self.client.session.cookie_jar.filter_cookies(self.client.make_url("/"))
        self.assertIn(COOKIE, cookies)
        self.assertNotIn("tuya.m.device.get", self.cloud_actions, "Existing cloud versions must skip per-device fallback")

    async def test_invalid_login_leaves_no_session(self):
        self.reject_login = True
        result = await self.post("/api/login", {"username":"tester@example.test", "password":"bad"})
        self.assertEqual(result.status, 401)
        self.assertFalse(self.app[SESSIONS])

    async def test_gateway_routing_and_real_readback(self):
        await self.login()
        result = await self.post("/api/devices/fixture-ac/control", {"action":"temperature", "value":25})
        data = await result.json()
        self.assertEqual(result.status, 200)
        self.assertEqual(data["snapshot"]["devices"][0]["target"], 26, "Must not optimistically fake cloud state")
        self.assertEqual(data["snapshot"]["devices"][0]["command"]["status"], "pending")
        self.assertEqual(self.published[0]["gwId"], "fixture-gateway")
        self.assertEqual(json.loads(self.published[0]["dps"]), {"2":25})
        await asyncio.sleep(1.15)
        data = await (await self.client.get("/api/state")).json()
        self.assertEqual(data["devices"][0]["target"], 25)
        self.assertEqual(data["devices"][0]["command"]["status"], "confirmed")

    async def test_duplicate_click_and_unconfirmed_state(self):
        self.report_write = False
        await self.login()
        payload = {"action":"temperature", "value":25}
        self.assertEqual((await self.post("/api/devices/fixture-ac/control", payload)).status, 200)
        self.assertEqual((await self.post("/api/devices/fixture-ac/control", payload)).status, 400)
        session = next(iter(self.app[SESSIONS].values()))
        session.commands["fixture-ac"]["time"] -= 36
        data = await (await self.client.get("/api/state")).json()
        self.assertEqual(data["devices"][0]["target"], 26)
        self.assertEqual(data["devices"][0]["command"]["status"], "unconfirmed")
        self.assertEqual(len(self.published), 1)

    async def test_rejected_write_does_not_change_state(self):
        self.reject_write = True
        await self.login()
        result = await self.post("/api/devices/fixture-ac/control", {"action":"power", "value":False})
        self.assertEqual(result.status, 502)
        data = await (await self.client.get("/api/state")).json()
        self.assertTrue(data["devices"][0]["power"])
        self.assertIsNone(data["devices"][0]["command"])

    async def test_expired_cloud_session_reauthenticates(self):
        await self.login()
        self.expire_session_once = True
        result = await self.post("/api/refresh", {})
        self.assertEqual(result.status, 200)
        self.assertEqual(self.login_count, 2)

    async def test_failed_refresh_disables_control(self):
        await self.login()
        self.cloud_fail = True
        self.assertEqual((await self.post("/api/refresh", {})).status, 502)
        data = await (await self.client.get("/api/state")).json()
        self.assertTrue(data["stale"])
        self.assertEqual((await self.post("/api/devices/fixture-ac/control", {"action":"power","value":False})).status, 400)
        self.assertEqual(len(self.published), 0)

    async def test_invalid_payloads_do_not_reach_cloud(self):
        await self.login()
        for payload in ({"action":"temperature","value":80}, {"action":"power","value":"false"}, {"action":"raw","value":{"1":True}}):
            result = await self.post("/api/devices/fixture-ac/control", payload)
            self.assertEqual(result.status, 400)
        self.assertFalse(self.published)

    async def test_demo_and_logout(self):
        data = await self.login(demo=True)
        self.assertTrue(data["demo"])
        self.assertEqual(len(data["devices"]), 6)
        self.assertEqual(self.login_count, 0)
        result = await self.post("/api/devices/demo-living-ac/control", {"action":"power","value":False})
        self.assertEqual(result.status, 200)
        self.assertFalse((await result.json())["snapshot"]["devices"][0]["power"])
        await self.post("/api/logout", {})
        self.assertFalse(self.app[SESSIONS])
        self.assertFalse((await (await self.client.get("/api/state")).json())["connected"])

    async def test_static_and_private_file_boundary(self):
        result = await self.client.get("/")
        self.assertEqual(result.status, 200)
        self.assertIn("小海", await result.text())
        self.assertEqual(result.headers["X-Frame-Options"], "DENY")
        self.assertEqual((await self.client.get("/static/server.py")).status, 404)

    async def test_room_assignment_persists_and_does_not_write_cloud(self):
        await self.login()
        result = await self.post("/api/devices/fixture-ac/room", {"room":"书房"})
        self.assertEqual(result.status, 200)
        self.assertEqual((await result.json())["devices"][0]["room"], "书房")
        await self.post("/api/refresh", {})
        await self.post("/api/logout", {})
        data = await self.login()
        self.assertEqual(data["devices"][0]["room"], "书房")
        self.assertEqual(data["devices"][0]["room_source"], "local")
        self.assertFalse(self.published)
        data = await (await self.post("/api/devices/fixture-ac/room", {"room":""})).json()
        self.assertEqual(data["devices"][0]["room"], "测试房间")

    async def test_slow_background_refresh_does_not_queue_control(self):
        await self.login()
        session = next(iter(self.app[SESSIONS].values()))
        session.updated -= 31
        self.gate_action = "tuya.m.location.list"
        result = await asyncio.wait_for(self.client.get("/api/state"), 0.5)
        self.assertTrue((await result.json())["refreshing"])
        await asyncio.wait_for(self.gate_entered.wait(), 0.5)
        control = await asyncio.wait_for(self.post("/api/devices/fixture-ac/control", {"action":"temperature", "value":25}), 0.5)
        self.assertEqual(control.status, 200)
        self.assertEqual(len(self.published), 1)
        self.gate.set()
        await session.refresh_task
        self.assertFalse(session.commands["fixture-ac"]["observed"], "A refresh started before the command cannot confirm it")

    async def test_curtain_stop_overrides_pending_open(self):
        await self.login()
        session = next(iter(self.app[SESSIONS].values()))
        session.devices["fixture-ac"] = {**fixtures()[7], "devId":"fixture-ac"}
        opened = await self.post("/api/devices/fixture-ac/control", {"action":"curtain", "value":"open"})
        data = await opened.json()
        self.assertEqual(opened.status, 200)
        self.assertEqual(data["snapshot"]["devices"][0]["command"]["status"], "pending", "Old equal DP is not fresh readback")
        stopped = await self.post("/api/devices/fixture-ac/control", {"action":"curtain", "value":"stop"})
        self.assertEqual(stopped.status, 200)
        self.assertEqual(json.loads(self.published[-1]["dps"]), {"4":"stop"})

    async def test_scene_trigger_never_confirms_from_old_echo(self):
        await self.login()
        session = next(iter(self.app[SESSIONS].values()))
        session.devices["fixture-ac"] = {**fixtures()[24], "devId":"fixture-ac"}
        for _ in range(2):
            result = await self.post("/api/devices/fixture-ac/control", {"action":"scene", "value":"scene_3"})
            self.assertEqual(result.status, 200)
            self.assertEqual((await result.json())["snapshot"]["devices"][0]["command"]["status"], "sent")
        self.assertEqual(len(self.published), 2)
        self.assertEqual(json.loads(self.published[-1]["dps"]), {"3":"scene"})


if __name__ == "__main__":
    unittest.main()
