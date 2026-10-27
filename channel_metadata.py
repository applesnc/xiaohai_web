"""Extract custom DP labels without retaining complete cloud device beans."""
import json
import re


def dp_names(payload):
    if not isinstance(payload, dict):
        return {}
    found = {}
    for field in ("dpName", "dpNames", "dpNameMap", "_channel_names"):
        names = payload.get(field)
        if isinstance(names, str):
            try:
                names = json.loads(names)
            except ValueError:
                continue
        if not isinstance(names, dict):
            continue
        for key, value in names.items():
            if (re.fullmatch(r"[A-Za-z0-9_]{1,64}", str(key)) and isinstance(value, str)
                    and 0 < len(value.strip()) <= 120 and not any(ord(c) < 32 for c in value)):
                found[str(key)] = value.strip()
    for field in ("device", "deviceBean", "result", "dataPointInfo"):
        child = payload.get(field)
        if isinstance(child, dict):
            found.update(dp_names(child))
    return found
