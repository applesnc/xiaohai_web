"""Explicit cloud-schema controls for lights, channel panels and curtains."""
from __future__ import annotations

import json
import math
import re


def parsed(schema, prefix, code, default):
    try:
        value = schema.get(prefix + code, default)
        return json.loads(value) if isinstance(value, str) else value
    except (ValueError, TypeError):
        return default


def limits(schema, code):
    value = parsed(schema, "@limit:", code, {})
    if not isinstance(value, dict):
        return None
    parts = [value.get(key) for key in ("min", "max", "step")]
    if not all(type(part) in (int, float) and math.isfinite(part) for part in parts):
        return None
    low, high, step = parts
    return (low, high, step) if low < high and step > 0 else None


def writable(schema, code):
    return code in schema and schema.get("@mode:" + code, "rw") in {"rw", "wr", "w"}


def capabilities(device):
    schema, dps = device.get("_schema") or {}, device.get("dps") or {}
    names = device.get("_channel_names") or {}
    if {"master_state", "factory_reset", "proxy_apply"} <= schema.keys():
        return {"kind": "gateway", "channels": [], "gateway_state": dps.get(schema["master_state"])}
    if "control" in schema and "percent_control" in schema:
        actions = {}
        for code in ("control", "control_2"):
            if writable(schema, code):
                for value in parsed(schema, "@range:", code, []):
                    if value in {"open", "close", "stop"}:
                        actions[value] = schema[code]
        limit = limits(schema, "percent_control") if writable(schema, "percent_control") else None
        raw = dps.get(schema.get("percent_control"))
        percent = round((raw - limit[0]) * 100 / (limit[1] - limit[0]), 2) if limit and type(raw) in (int, float) else None
        return {"kind": "curtain", "channels": [], "actions": actions,
                "position_dp": schema.get("percent_control"), "limit": limit,
                "position": percent, "position_step": limit[2] * 100 / (limit[1] - limit[0]) if limit else None}
    light_keys = [key for key in schema if re.fullmatch(r"switch_led(?:_\d+)?", key)]
    switch_keys = [key for key in schema if re.fullmatch(r"switch_\d+", key)]
    if not light_keys and not switch_keys:
        return None
    channels = []
    for key in sorted(light_keys or switch_keys, key=lambda key: int(key.rsplit("_", 1)[-1]) if key.rsplit("_", 1)[-1].isdigit() else 1):
        suffix = key.rsplit("_", 1)[-1]
        index = int(suffix) if suffix.isdigit() else 1
        mode_code, scene_code = f"mode_{index}", f"scene_{index}"
        mode = dps.get(schema.get(mode_code)) if mode_code in schema else key
        channel = {"key": key, "label": f"第 {index} 路", "kind": "switch", "on": dps.get(schema[key]),
                   "can_power": writable(schema, key) and type(dps.get(schema[key])) is bool,
                   "can_scene": False, "can_brightness": False, "power_dp": schema[key]}
        if not light_keys and mode_code in schema:
            if mode == scene_code:
                channel.update(kind="scene", label=f"第 {index} 键 · 场景", can_power=False,
                               can_scene=writable(schema, scene_code) and "scene" in parsed(schema, "@range:", scene_code, []),
                               scene_code=scene_code, scene_dp=schema.get(scene_code))
            elif mode != key:
                channel.update(kind="unknown", label=f"第 {index} 键 · 模式未知", can_power=False)
        if light_keys:
            bright_code = f"bright_value_{index}" if suffix.isdigit() else "bright_value"
            limit = limits(schema, bright_code) if writable(schema, bright_code) else None
            raw = dps.get(schema.get(bright_code))
            percent = round((raw - limit[0]) * 100 / (limit[1] - limit[0])) if limit and type(raw) in (int, float) else None
            channel.update(kind="light", brightness=percent, can_brightness=bool(limit),
                           brightness_dp=schema.get(bright_code), limit=limit)
        # Scene mode must use the scene DP's custom name first. A panel can
        # assign different labels to its relay function and its scene function.
        name_codes = [scene_code, key] if channel["kind"] == "scene" else [key]
        if light_keys:
            name_codes.append(bright_code)
        label = next((names.get(schema.get(code)) or names.get(code) for code in name_codes
                      if names.get(schema.get(code)) or names.get(code)), None)
        if label:
            channel["label"] = label
            channel["named"] = True
        else:
            channel["named"] = False
        channels.append(channel)
    return {"kind": "light" if light_keys else "switch", "channels": channels}


def extended_view(device):
    spec = capabilities(device)
    if not spec:
        return {}
    result = {"kind": spec["kind"], "can_power": False, "can_temperature": False,
              "can_mode": False, "can_fan": False, "power": None, "channels": [], "note": ""}
    if spec["kind"] == "gateway":
        result.update(gateway_state={"normal": "正常", "alarm": "有告警"}.get(spec["gateway_state"], "状态未知"),
                      note="网关仅显示运行状态。")
    elif spec["kind"] == "curtain":
        result.update(curtain_actions=list(spec["actions"]), position=spec["position"],
                      can_position=bool(spec["limit"]), position_step=spec["position_step"],
                      note="目标位置是云端设定值；实际开合请以现场为准。")
    else:
        public = {"key", "label", "kind", "on", "can_power", "can_scene", "can_brightness", "brightness", "scene_code", "named"}
        result["channels"] = [{key: value for key, value in channel.items() if key in public} for channel in spec["channels"]]
        states = [channel["on"] for channel in spec["channels"] if channel["kind"] != "scene" and type(channel["on"]) is bool]
        result["power"] = any(states) if states else None
    return result


def percentage(value):
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 100:
        raise ValueError("百分比必须是 0 到 100 之间的数字。")
    return value


def raw_percent(value, limit, strict=False):
    low, high, step = limit
    raw = low + percentage(value) * (high - low) / 100
    steps = (raw - low) / step
    if strict and not math.isclose(steps, round(steps), abs_tol=0.00001):
        raise ValueError("位置不符合这台窗帘支持的步长。")
    raw = min(high, low + math.floor(steps + 0.5) * step)
    return int(raw) if float(raw).is_integer() else raw


def extended_command(device, action, value):
    spec = capabilities(device)
    if not spec:
        return None
    if action == "curtain" and spec["kind"] == "curtain" and isinstance(value, str) and value in spec["actions"]:
        return {spec["actions"][value]: value}
    if action == "position" and spec["kind"] == "curtain" and spec["limit"]:
        return {spec["position_dp"]: raw_percent(value, spec["limit"], strict=True)}
    if action == "scene":
        for channel in spec["channels"]:
            if channel.get("can_scene") and channel.get("scene_code") == value:
                return {channel["scene_dp"]: "scene"}
    if action in {"channel_power", "brightness"} and isinstance(value, dict):
        channel = next((item for item in spec["channels"] if item["key"] == value.get("channel")), None)
        if channel:
            if action == "channel_power" and channel["can_power"] and type(value.get("on")) is bool:
                return {channel["power_dp"]: value["on"]}
            if action == "brightness" and channel["can_brightness"]:
                return {channel["brightness_dp"]: raw_percent(value.get("percent"), channel["limit"])}
    raise ValueError("此通道当前不支持该操作，请刷新后检查开关或场景模式。")
