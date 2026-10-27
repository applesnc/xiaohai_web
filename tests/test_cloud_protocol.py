"""Fixed wire vectors and functional contracts; only synthetic credentials."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from cloudio.wire import ClientProfile, envelope, password_proof, request_signature
from device_schema import merge_definition, product_catalogs, reported_online, room_membership, version_values
from devices import InputError, command_dps, demo_devices, device_view, normalize


class WireChecks(unittest.TestCase):
    def setUp(self):
        self.fields = {"a":"fixture.read", "clientId":"fixture-app", "v":"1.0", "time":"1700000000",
            "et":"0.0.1", "requestId":"fixture-request", "gid":"100", "postData":'{"message":"hello","count":3}'}

    def test_fixed_protocol_vectors(self):
        self.assertEqual(request_signature(self.fields, "fixture-signing-secret"),
                         "3235aa910706d82f24d6a8dccc43f2828231150151ee852102cd02771513fd3c")
        self.assertEqual(password_proof("only-a-fixture"), "ff756bf48c387f125d517185681d7423")

    def test_routing_is_unsigned_but_body_and_session_are_signed(self):
        signed = request_signature(self.fields, "test-key")
        self.assertEqual(signed, request_signature({**self.fields,"gid":"200","ignored":"","sign":"old"},"test-key"))
        for update in ({"sid":"new-session"}, {"postData":'{"message":"other","count":3}'}):
            self.assertNotEqual(signed, request_signature({**self.fields, **update},"test-key"))

    def test_envelope_uses_explicit_profile_and_keeps_unicode(self):
        profile = ClientProfile("https://example.test/api.json", "fixture-app", "test-key")
        form = envelope(profile, "fixture.read", {"name":"\u5ba2\u5385"}, group=20, session_id="fixture-session")
        self.assertEqual(form["gid"], "20")
        self.assertEqual(json.loads(form["postData"]), {"name":"\u5ba2\u5385"})
        self.assertEqual(form["sign"], request_signature(form,"test-key"))

    def test_profile_missing_or_insecure_remote_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/"profile.json"
            with self.assertRaises(ValueError):
                ClientProfile.load(path)
            path.write_text(json.dumps({"endpoint":"http://example.test/api.json", "client_id":"fixture", "signing_secret":"test"}),encoding="utf-8")
            with self.assertRaises(ValueError):
                ClientProfile.load(path)


class SchemaContractChecks(unittest.TestCase):
    def test_product_schema_json_and_write_permissions(self):
        records = [{"id":1,"code":"switch","mode":"ro","property":{"type":"bool"}},
                   {"id":2,"code":"temp_set","mode":"rw","property":{"min":160,"max":300,"step":5,"scale":1}}]
        raw = {"productId":"fixture-product", "schemaInfo":{"schema":json.dumps(records)}}
        catalog = product_catalogs(raw)["fixture-product"]
        device = {"devId":"fixture", "dps":{"1":False,"2":245}, "isOnline":True, "_schema":catalog}
        self.assertFalse(device_view(device)["can_power"])
        self.assertEqual(device_view(device)["target"],24.5)
        self.assertEqual(command_dps(device,"temperature",25.5),{"2":255})
        with self.assertRaises(InputError):
            command_dps(device,"power",True)

    def test_declared_mask_filters_gears_without_guessing_dp(self):
        device = demo_devices()["demo-living-ac"]
        device["_schema"].update(device_config="107")
        device["dps"]["107"] = "10100" + "0"*30
        self.assertEqual([item["value"] for item in device_view(device)["fan_speeds"]], ["auto","level_1","level_3"])

    def test_room_shapes_and_module_online_states(self):
        self.assertEqual(room_membership({"rooms":[{"name":"\u5ba2\u5385","devices":[{"devId":"short-id"}]}]},{}), {"short-id":"\u5ba2\u5385"})
        self.assertEqual(room_membership([{"bizId":"device","roomId":10}],{"10":"Room"}), {"device":"Room"})
        self.assertTrue(reported_online({"isOnline":False,"moduleMap":{"one":{"isOnline":False},"two":{"isOnline":True}}}))
        self.assertIsNone(reported_online({"isOnline":"false"}))

    def test_versions_and_nested_dp_state(self):
        self.assertEqual(version_values({"list":[{"devId":"device","productInfo":{"productVer":"2.1"}}]}),{"device":"2.1"})
        self.assertEqual(normalize({"devId":"device","dataPointInfo":{"dps":{"1":False}}})["dps"],{"1":False})


if __name__ == "__main__":
    unittest.main()
