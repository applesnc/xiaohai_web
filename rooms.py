"""Cloud room names, explicit local overrides, then labeled name inference."""

ROOM_NAMES = ("南次卧", "北次卧", "东次卧", "西次卧", "主卧", "次卧", "客厅", "餐厅", "厨房",
              "公卫", "主卫", "卫生间", "玄关", "书房", "儿童房", "阳台", "储藏室", "走廊")


def room_for(device, overrides):
    key = str(device["devId"])
    if key in overrides:
        return overrides[key], "local"
    if device.get("_room"):
        return str(device["_room"]), "cloud"
    name = str(device.get("name") or "")
    for room in ROOM_NAMES:
        if room in name:
            return room, "name"
    return "全屋设备", "group"
