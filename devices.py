"""Device capabilities and validated commands, independent of Home Assistant."""
from __future__ import annotations

import copy
import json
from typing import Any

from extended_devices import extended_command, extended_view
from channel_metadata import dp_names

from device_schema import merge_definition, reported_online
from environment_controls import Environment


class InputError(ValueError):
    """Invalid or unsupported user command."""


def normalize(device: dict) -> dict:
    result = dict(device)
    dps = result.get("dps")
    if dps is None and isinstance(result.get("dataPointInfo"), dict):
        dps = result["dataPointInfo"].get("dps")
    if isinstance(dps, str):
        try:
            dps = json.loads(dps)
        except (ValueError, TypeError):
            dps = {}
    result["dps"] = {str(k): v for k, v in dps.items()} if isinstance(dps, dict) else {}
    names = dp_names(result)
    if names:
        result["_channel_names"] = names
    if isinstance(result.get("roomName"), str) and result["roomName"].strip():
        result["_room"] = result["roomName"].strip()
    return result


def device_view(device: dict) -> dict:
    result = {
        "id": str(device["devId"]), "name": str(device.get("name") or "未命名设备"),
        "room": str(device.get("_room") or "未分配房间"), "kind":"other",
        "online":reported_online(device), "power":None, "temperature":None, "target":None,
        "mode":None, "modes":[], "fan_speed":None, "fan_speeds":[],
        "min_temp":None, "max_temp":None, "temp_step":None,
        "can_power":False, "can_temperature":False, "can_mode":False, "can_fan":False,
        "note":"此设备尚未识别到可控制的功能",
    }
    environment = Environment.inspect(device)
    if environment:
        result.update(environment.view(device.get("dps") or {}))
    result.update(extended_view(device))
    return result


def command_dps(device: dict, action: str, value: Any) -> dict:
    if reported_online(device) is False:
        raise InputError("设备当前离线，请恢复连接后再操作。")
    try:
        extended = extended_command(device, action, value)
    except ValueError as err:
        raise InputError(str(err)) from err
    if extended is not None:
        return extended
    environment = Environment.inspect(device)
    if environment:
        try:
            return environment.command(device.get("dps") or {}, action, value)
        except ValueError as err:
            raise InputError(str(err)) from err
    raise InputError("此设备尚不支持该操作。")


async def discover(client, rows=None, progress=None) -> tuple[dict[str, dict], list[str]]:
    report = progress or (lambda message: None)
    if rows is None:
        rows = await client.inventory()
    devices = [normalize(item) for item in rows if isinstance(item, dict) and item.get("devId")]
    report("正在读取房间和设备功能配置…")
    rooms, schemas, warnings = {}, {}, []
    for gid in dict.fromkeys(item.get("_group_id") for item in devices if item.get("_group_id") is not None):
        try:
            rooms.update(await client.rooms(gid))
        except Exception:
            warnings.append("部分房间名称未能读取")
        schemas.update(await client.product_definitions(gid))
    await client.hydrate(devices, progress=lambda done, total: report(f"正在读取设备功能：{done} / {total} 台…"))
    pairs = list(dict.fromkeys(
        (str(item["productId"]), str(item.get("productVer") or item.get("productVersion")))
        for item in devices if item.get("productId") and (item.get("productVer") or item.get("productVersion"))
    ) )
    report("正在加载控制面板配置…")
    beans = await client.panel_definitions(pairs) if pairs else {}
    if client.last_version_note or client.last_schema_note:
        warnings.append("部分设备功能配置未能完整读取，可在连接设置中重新读取")
    result = {}
    for device in devices:
        pid = str(device.get("productId") or "")
        version = str(device.get("productVer") or device.get("productVersion") or "")
        schema = dict(schemas.get(pid) or {})
        if version:
            merge_definition(schema, beans.get(f"{pid}|{version}"))
        device["_schema"] = schema
        device["_room"] = rooms.get(device["devId"]) or device.get("_room")
        result[str(device["devId"])] = device
    return result, list(dict.fromkeys(warnings))


def demo_devices() -> dict[str, dict]:
    """Explicit simulation data; no account or cloud requests involved."""
    ac = {"switch": "1", "temp_set": "2", "temp_current": "3", "mode": "4", "fan_speed_enum": "5",
          "@range:mode": '["cold","hot","wind","auto","wet"]',
          "@range:fan_speed_enum": '["auto","level_1","level_2","level_3"]',
          "@limit:temp_set": '{"min":16,"max":30,"step":1,"scale":0}',
          "@limit:temp_current": '{"scale":0}'}
    heater = {"switch": "1", "temp_set": "2", "temp_current": "3",
              "@limit:temp_set": '{"min":10,"max":35,"step":1,"scale":0}'}
    fan = {"switch": "1", "fan_speed_enum": "5", "@range:fan_speed_enum": '["level1","level2"]'}
    rows = [
        ("demo-living-ac", "客厅空调", "客厅", ac, {"1": True, "2": 26, "3": 25.5, "4": "cold", "5": "auto"}, True),
        ("demo-living-fan", "客厅新风", "客厅", fan, {"1": True, "5": "level1"}, True),
        ("demo-bedroom-heat", "主卧地暖", "主卧", heater, {"1": False, "2": 24, "3": 23}, True),
        ("demo-bedroom-ac", "主卧空调", "主卧", ac, {"1": False, "2": 25, "3": 24, "4": "cold", "5": "level_1"}, True),
        ("demo-study-ac", "书房空调", "书房", ac, {"1": False, "2": 26, "3": 24, "4": "cold", "5": "level_2"}, False),
        ("demo-gateway", "小海网关", "客厅", {}, {}, True),
    ]
    return {item[0]: {"devId": item[0], "name": item[1], "_room": item[2], "_schema": copy.deepcopy(item[3]),
                       "dps": dict(item[4]), "isOnline": item[5], "_group_id": "demo"} for item in rows}
