"""HVAC controls derived from named fields and declared engineering units."""
from __future__ import annotations

from dataclasses import dataclass
import json
import math
import re

from device_schema import numeric

MODE_TITLES = {"auto":"自动", "cold":"制冷", "cool":"制冷", "hot":"制热", "heat":"制热",
               "wind":"送风", "fan_only":"送风", "wet":"除湿", "dry":"除湿"}


def decode(value, fallback):
    if isinstance(value, type(fallback)):
        return value
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, type(fallback)) else fallback
    except (TypeError, ValueError):
        return fallback


@dataclass(frozen=True)
class Point:
    ident: str
    choices: tuple
    limits: dict
    writable: bool

    @property
    def divisor(self):
        scale = self.limits.get("scale", 0)
        return 10 ** scale if type(scale) is int and 0 <= scale <= 6 else 1

    def reading(self, states):
        raw = states.get(self.ident)
        return raw / self.divisor if numeric(raw) else None

    def temperature_range(self):
        values = [self.limits.get(key) for key in ("min", "max", "step")]
        if not all(numeric(value) for value in values) or values[0] >= values[1] or values[2] <= 0:
            return None
        return tuple(value / self.divisor for value in values)


def point(schema, *names):
    for name in names:
        ident = schema.get(name)
        if isinstance(ident, (str, int)):
            choices = decode(schema.get("@range:" + name), [])
            limits = decode(schema.get("@limit:" + name), {})
            return Point(str(ident), tuple(value for value in choices if isinstance(value, str)), limits,
                         schema.get("@mode:" + name, "rw") in {"rw", "wr", "w"})
    return None


@dataclass
class Environment:
    kind: str
    power: Point | None
    target: Point | None
    current: Point | None
    mode: Point | None
    fan: Point | None
    speeds: tuple

    @classmethod
    def inspect(cls, device):
        schema = device.get("_schema") or {}
        target = point(schema, "temp_set", "temp_set_c")
        current = point(schema, "temp_current", "temp_current_c", "temp_indoor")
        fan = point(schema, "fan_speed_enum", "fan_speed", "windspeed", "speed_enum")
        mode = point(schema, "mode")
        power = point(schema, "switch", "switch_led")
        if target:
            kind = "climate" if mode or fan else "heating"
        elif fan:
            kind = "fan"
        elif power:
            kind = "switch"
        else:
            return None
        speeds = list(fan.choices) if fan else []
        # A declared binary mask disables corresponding numbered gear values.
        config = point(schema, "device_config")
        mask = (device.get("dps") or {}).get(config.ident) if config else None
        if kind == "climate" and isinstance(mask, str) and len(mask) >= 5 and set(mask[:5]) <= {"0", "1"}:
            speeds = [token for token in speeds if not (match := re.fullmatch(r"level_(\d+)", token))
                      or int(match[1]) > 5 or int(match[1]) < 1 or mask[int(match[1])-1] == "1"]
        speeds = tuple(value for value in speeds if value not in {"off", "close"})
        return cls(kind, power, target, current, mode, fan, speeds)

    def view(self, states):
        temperature_range = self.target.temperature_range() if self.target else None
        mode_choices = [{"value":token, "label":MODE_TITLES[token]} for token in self.mode.choices if token in MODE_TITLES] if self.mode else []
        speed_choices = []
        ordinal = 0
        for token in self.speeds:
            if token == "auto":
                label = "自动"
            elif token == "mute":
                label = "静音"
            else:
                ordinal += 1
                label = f"{ordinal} 档"
            speed_choices.append({"value":token, "label":label})
        power = states.get(self.power.ident) if self.power else None
        return {"kind":self.kind, "power":power if isinstance(power, bool) else None,
                "temperature":self.current.reading(states) if self.current else None,
                "target":self.target.reading(states) if self.target else None,
                "mode":states.get(self.mode.ident) if self.mode else None, "modes":mode_choices,
                "fan_speed":states.get(self.fan.ident) if self.fan else None, "fan_speeds":speed_choices,
                "min_temp":temperature_range[0] if temperature_range else None,
                "max_temp":temperature_range[1] if temperature_range else None,
                "temp_step":temperature_range[2] if temperature_range else None,
                "can_power":bool(self.power and self.power.writable),
                "can_temperature":bool(self.target and self.target.writable and temperature_range),
                "can_mode":bool(self.mode and self.mode.writable and mode_choices),
                "can_fan":bool(self.fan and self.fan.writable and speed_choices),
                "note":"云端未提供完整温度范围，暂不开放调温" if self.target and not temperature_range else ""}

    def command(self, states, action, value):
        view = self.view(states)
        if action == "power" and view["can_power"] and isinstance(value, bool):
            return {self.power.ident:value}
        if action == "temperature" and view["can_temperature"]:
            if not numeric(value) or not view["min_temp"] <= value <= view["max_temp"]:
                raise ValueError("温度超出了这台设备支持的范围。")
            position = (value - view["min_temp"]) / view["temp_step"]
            if not math.isclose(position, round(position), abs_tol=0.00001):
                raise ValueError("温度不符合设备的调节步长。")
            raw = value * self.target.divisor
            return {self.target.ident:round(raw) if math.isclose(raw, round(raw), abs_tol=0.00001) else raw}
        if action == "mode" and view["can_mode"] and isinstance(value, str) and value in {choice["value"] for choice in view["modes"]}:
            return {self.mode.ident:value}
        if action == "fan" and view["can_fan"] and isinstance(value, str) and value in self.speeds:
            return {self.fan.ident:value}
        raise ValueError("设备当前不支持这个控制值。")
