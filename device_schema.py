"""Typed functional descriptions decoded from cloud JSON objects."""
from __future__ import annotations

from dataclasses import dataclass
import json
import math


def objects(value):
    """Walk nested containers, including JSON stored in a schema string."""
    queue = [value]
    while queue:
        item = queue.pop()
        if isinstance(item, str) and item.lstrip()[:1] in {"[", "{"}:
            try:
                item = json.loads(item)
            except ValueError:
                continue
        if isinstance(item, dict):
            yield item
            queue.extend(reversed(list(item.values())))
        elif isinstance(item, list):
            queue.extend(reversed(item))


def numeric(value):
    return type(value) in (int, float) and math.isfinite(value)


@dataclass(frozen=True)
class Signal:
    code: str
    ident: str
    access: str
    options: tuple
    bounds: dict

    @classmethod
    def decode(cls, row):
        code = row.get("code") or row.get("dpCode")
        ident = row.get("id", row.get("dpId", row.get("abilityId")))
        if not isinstance(code, str) or ident is None:
            return None
        spec = row.get("property", row.get("typeSpec", row.get("valueRange", {})))
        if isinstance(spec, str):
            try:
                spec = json.loads(spec)
            except ValueError:
                spec = {}
        if not isinstance(spec, dict):
            spec = {}
        options = spec.get("range", row.get("values", []))
        if not isinstance(options, list) or not all(isinstance(value, str) for value in options):
            options = []
        bounds = {key: spec[key] for key in ("min", "max", "step", "scale") if key in spec and numeric(spec[key])}
        return cls(code, str(ident), str(row.get("mode", row.get("accessMode", "rw"))), tuple(options), bounds)

    def store(self, mapping):
        mapping[self.code] = self.ident
        mapping[f"@mode:{self.code}"] = self.access
        if self.options:
            mapping[f"@range:{self.code}"] = json.dumps(self.options, ensure_ascii=False)
        if self.bounds:
            mapping[f"@limit:{self.code}"] = json.dumps(self.bounds, ensure_ascii=False)


def merge_definition(mapping, payload):
    for node in objects(payload):
        # Containers have an id too. A function description must also carry
        # property/type information, so product rows cannot become controls.
        if not {"property", "typeSpec", "valueRange", "values"}.intersection(node):
            continue
        signal = Signal.decode(node)
        if signal:
            signal.store(mapping)


def product_catalogs(payload):
    result = {}
    for node in objects(payload):
        ident = node.get("productId")
        if ident is not None:
            mapping = result.setdefault(str(ident), {})
            merge_definition(mapping, node)
    return result


def version_values(payload):
    result = {}
    for node in objects(payload):
        ident = node.get("devId")
        if not ident:
            continue
        for candidate in (node, node.get("productInfo"), node.get("product"), node.get("meta")):
            if isinstance(candidate, dict):
                version = candidate.get("productVer", candidate.get("productVersion"))
                if version:
                    result[str(ident)] = str(version)
                    break
    return result


def room_membership(payload, room_names):
    result = {}
    for node in objects(payload):
        ident = node.get("devId", node.get("bizId"))
        room = node.get("roomName") or room_names.get(str(node.get("roomId")))
        if ident and isinstance(room, str) and room:
            result[str(ident)] = room
        name = node.get("name", node.get("roomName"))
        if not isinstance(name, str) or not name:
            continue
        for field in ("deviceIds", "devIds", "deviceList", "devices", "ids"):
            members = node.get(field)
            if isinstance(members, str):
                try:
                    members = json.loads(members)
                except ValueError:
                    continue
            if not isinstance(members, list):
                continue
            for member in members:
                if isinstance(member, dict):
                    if member.get("type", 6) not in (6, "6"):
                        continue
                    member = member.get("devId", member.get("bizId", member.get("id")))
                if isinstance(member, str) and member:
                    result[member] = name
    return result


def reported_online(device):
    def flag(value):
        if isinstance(value, bool):
            return value
        if value in (0, 1) and type(value) is int:
            return bool(value)
        return None
    modules = device.get("moduleMap")
    if isinstance(modules, dict):
        states = [flag(row.get("isOnline")) for row in modules.values() if isinstance(row, dict)]
        known = [value for value in states if value is not None]
        if known:
            return any(known)
    for field in ("isOnline", "cloudOnline"):
        state = flag(device.get(field))
        if state is not None:
            return state
    return None
