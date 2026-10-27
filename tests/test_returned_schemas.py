"""Representative functional descriptions and synthetic device identifiers."""
from collections import Counter
import copy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from devices import InputError, command_dps, device_view, normalize
from rooms import room_for


def fixtures():
    return json.loads((Path(__file__).parent / "fixtures" / "returned_devices.json").read_text(encoding="utf-8"))["devices"]


class ReturnedSchemaChecks(unittest.TestCase):
    def setUp(self):
        self.rows = fixtures()

    def test_classification_of_all_observed_devices(self):
        self.assertEqual(Counter(device_view(row)["kind"] for row in self.rows),
                         {"climate": 3, "heating": 1, "fan": 1, "light": 9, "curtain": 4, "switch": 11, "gateway": 1})

    def test_light_channels_and_brightness_units(self):
        multi, light = self.rows[1], self.rows[5]
        self.assertEqual(command_dps(multi, "channel_power", {"channel":"switch_led_4", "on":True}), {"101":True})
        self.assertEqual(command_dps(multi, "brightness", {"channel":"switch_led_4", "percent":50}), {"102":50})
        for percent, raw in ((0,0), (50,128), (100,255)):
            self.assertEqual(command_dps(light, "brightness", {"channel":"switch_led_1", "percent":percent}), {"2":raw})
        for bad in (True, -1, 101, float("nan"), "50"):
            with self.assertRaises(InputError):
                command_dps(light, "brightness", {"channel":"switch_led_1", "percent":bad})
        light["dps"]["2"] = 128
        self.assertEqual(device_view(light)["channels"][0]["brightness"], 50)
        self.assertFalse(light["dps"]["1"], "Brightness changes must not implicitly power on")

    def test_twelve_way_module_numeric_order_and_mapping(self):
        module = self.rows[13]
        self.assertEqual([c["key"] for c in device_view(module)["channels"]], [f"switch_{i}" for i in range(1,13)])
        self.assertEqual(command_dps(module, "channel_power", {"channel":"switch_10", "on":True}), {"104":True})
        self.assertEqual(command_dps(module, "channel_power", {"channel":"switch_12", "on":False}), {"106":False})
        with self.assertRaises(InputError):
            command_dps(module, "channel_power", {"channel":"device_obj_conf_id", "on":True})

    def test_scene_mode_and_relay_mode_are_distinct(self):
        panel = self.rows[24]
        channels = device_view(panel)["channels"]
        self.assertEqual(channels[2]["kind"], "scene")
        self.assertFalse(channels[2]["can_power"])
        self.assertEqual(command_dps(panel, "scene", "scene_3"), {"3":"scene"})
        self.assertEqual(command_dps(panel, "channel_power", {"channel":"switch_6", "on":False}), {"113":False})
        with self.assertRaises(InputError):
            command_dps(panel, "channel_power", {"channel":"switch_3", "on":True})
        with self.assertRaises(InputError):
            command_dps(panel, "scene", "scene_1")
        panel["dps"].pop("102")
        self.assertFalse(device_view(panel)["channels"][0]["can_power"])

    def test_curtain_stop_and_position_step(self):
        curtain = self.rows[7]
        self.assertEqual(command_dps(curtain, "curtain", "open"), {"1":"open"})
        self.assertEqual(command_dps(curtain, "curtain", "close"), {"1":"close"})
        self.assertEqual(command_dps(curtain, "curtain", "stop"), {"4":"stop"})
        self.assertEqual(command_dps(curtain, "position", 50), {"2":50})
        for value in (15, True, -1, 101, "50"):
            with self.assertRaises(InputError):
                command_dps(curtain, "position", value)
        curtain["_schema"]["@mode:control_2"] = "ro"
        self.assertNotIn("stop", device_view(curtain)["curtain_actions"])
        with self.assertRaises(InputError):
            command_dps(curtain, "curtain", "stop")

    def test_gateway_has_no_household_controls(self):
        gateway = self.rows[29]
        view = device_view(gateway)
        self.assertEqual(view["kind"], "gateway")
        self.assertFalse(view["can_fan"])
        self.assertFalse(view["can_power"])
        for action, value in (("power",True), ("fan","level_1"), ("factory_reset",True)):
            with self.assertRaises(InputError):
                command_dps(gateway, action, value)

    def test_room_sources_and_local_override_priority(self):
        rooms = [room_for(row, {}) for row in self.rows]
        self.assertEqual(sum(source == "name" for _, source in rooms), 24)
        self.assertEqual(sum(source == "group" for _, source in rooms), 6)
        self.assertEqual(len({room for room, source in rooms if source == "name"}), 7)
        row = self.rows[0]
        self.assertEqual(room_for(row, {}), ("主卧", "name"))
        row["_room"] = "云端房间"
        self.assertEqual(room_for(row, {}), ("云端房间", "cloud"))
        self.assertEqual(room_for(row, {row["devId"]:"书房"}), ("书房", "local"))

    def test_missing_light_limits_and_offline_rejected(self):
        light = self.rows[5]
        light["_schema"].pop("@limit:bright_value_1")
        self.assertFalse(device_view(light)["channels"][0]["can_brightness"])
        with self.assertRaises(InputError):
            command_dps(light, "brightness", {"channel":"switch_led_1", "percent":50})
        light["isOnline"] = False
        with self.assertRaises(InputError):
            command_dps(light, "channel_power", {"channel":"switch_led_1", "on":True})

    def test_custom_names_follow_the_actual_channel_and_active_scene(self):
        light = normalize({**self.rows[1], "dpName":{"1":"客厅灯带", "7":"餐厅筒灯", "15":"走廊灯", "101":"书房灯带"}})
        self.assertEqual([c["label"] for c in device_view(light)["channels"]], ["客厅灯带","餐厅筒灯","走廊灯","书房灯带"])
        panel = normalize({**self.rows[24], "dataPointInfo":{"dpName":{"3":"回家模式", "110":"备用开关", "113":"玄关灯"}}})
        self.assertEqual(device_view(panel)["channels"][2]["label"], "回家模式")
        self.assertEqual(device_view(panel)["channels"][5]["label"], "玄关灯")
        module = normalize({**self.rows[13], "dpName":json.dumps({"106":"厨房射灯"})})
        self.assertEqual(device_view(module)["channels"][11]["label"], "厨房射灯")


if __name__ == "__main__":
    unittest.main()
