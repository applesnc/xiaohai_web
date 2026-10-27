"""Per-account device cache. Authentication material is never serialized."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import uuid

from device_schema import reported_online


DEVICE_FIELDS = {
    "devId", "name", "productId", "productVer", "productVersion",
    "parentId", "meshId", "isOnline", "dps", "_group_id", "_room", "_schema", "_channel_names",
}


class AccountStore:
    def __init__(self, root: Path, username: str, country: str):
        identity = country + "\0" + username.strip().casefold()
        self.key = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        self.path = root / (self.key + ".json")

    def load(self):
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if data.get("format") != 1 or not isinstance(data.get("devices"), list):
                return {}, 0
            devices = {}
            for row in data["devices"]:
                if isinstance(row, dict) and row.get("devId"):
                    devices[str(row["devId"])] = {key: value for key, value in row.items() if key in DEVICE_FIELDS}
            return devices, float(data.get("updated_at") or 0)
        except (OSError, ValueError, TypeError, AttributeError):
            return {}, 0

    def save(self, devices: dict, updated: float):
        # Deliberate allowlist: cloud beans can contain localKey and other secrets.
        rows = [{key: value for key, value in row.items() if key in DEVICE_FIELDS}
                for row in devices.values()]
        for cached, original in zip(rows, devices.values()):
            online = reported_online(original)
            if online is not None:
                cached["isOnline"] = online
        content = json.dumps({"format": 1, "updated_at": updated, "devices": rows},
                             ensure_ascii=False, allow_nan=False)
        self.write(self.path, content)

    def load_rooms(self):
        try:
            data = json.loads(self.path.with_suffix(".rooms.json").read_text(encoding="utf-8"))
            return {str(key): value for key, value in data.items() if isinstance(value, str) and 0 < len(value) <= 32}
        except (OSError, ValueError, TypeError, AttributeError):
            return {}

    def save_rooms(self, overrides):
        self.write(self.path.with_suffix(".rooms.json"), json.dumps(overrides, ensure_ascii=False))

    @staticmethod
    def write(path, content):
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix("." + uuid.uuid4().hex + ".tmp")
        try:
            temporary.write_text(content, encoding="utf-8")
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
