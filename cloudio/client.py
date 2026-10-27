"""Authenticated cloud RPC session and read-only device discovery resources."""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time

import aiohttp

from channel_metadata import dp_names
from device_schema import product_catalogs, room_membership, version_values
from payload_inspection import inspect as inspect_payload
from .wire import ClientProfile, envelope, password_proof

LOG = logging.getLogger("xiaohai.cloud")
SESSION_ERRORS = {"USER_SESSION_INVALID", "USER_SESSION_LOSS"}
ACCOUNT_ERRORS = {"USER_PASSWD_WRONG", "USER_NOT_EXISTS", "USER_NAME_NOT_EXIST", "NOT_EXISTS"}


class CloudFailure(Exception):
    def __init__(self, *, action="", code="UNKNOWN", http_status=None):
        self.action = action
        self.code = code if isinstance(code, str) and re.fullmatch(r"[A-Z0-9_]{1,64}", code) else "UNKNOWN"
        self.http_status = http_status
        super().__init__(f"Cloud request failed: {self.code}")


class LoginRejected(CloudFailure):
    pass


def rows(payload, *containers):
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        for key in containers:
            if isinstance(payload.get(key), list):
                return rows(payload[key])
    return []


class HomeCloud:
    def __init__(self, http, username, password, country, profile=None):
        self.http = http
        self.profile = profile or ClientProfile.load()
        self.account = username.strip()
        self.password = password
        self.country = country
        self.session_id = None
        self.auth_lock = asyncio.Lock()
        self.last_version_note = ""
        self.last_schema_note = ""

    async def exchange(self, action, payload=None, *, version=None, group=None, authenticated=True):
        if authenticated and not self.session_id:
            await self.login()
        for attempt in range(2):
            observed_session = self.session_id
            request = envelope(self.profile, action, payload, version=version, group=group,
                               session_id=observed_session if authenticated else None)
            started = time.monotonic()
            status = None
            try:
                async with self.http.post(self.profile.endpoint, data=request,
                                          headers={"User-Agent":self.profile.user_agent},
                                          timeout=aiohttp.ClientTimeout(total=30)) as response:
                    status = response.status
                    reply = await response.json(content_type=None)
            except (aiohttp.ClientError, TimeoutError) as error:
                code = "REQUEST_TIMEOUT" if isinstance(error, TimeoutError) else "TRANSPORT_ERROR"
                LOG.warning("cloud action=%s code=%s http=%s elapsed=%.3fs", action, code, status, time.monotonic()-started)
                raise CloudFailure(action=action, code=code, http_status=status) from None
            except (ValueError, UnicodeDecodeError):
                raise CloudFailure(action=action, code="NON_JSON_RESPONSE", http_status=status) from None
            if not isinstance(reply, dict):
                raise CloudFailure(action=action, code="INVALID_RESPONSE", http_status=status)
            code = "OK" if reply.get("success") is True else reply.get("errorCode", "UNKNOWN")
            safe_code = code if isinstance(code, str) and re.fullmatch(r"[A-Z0-9_]{1,64}", code) else "UNKNOWN"
            LOG.warning("cloud action=%s code=%s http=%s elapsed=%.3fs", action, safe_code, status, time.monotonic()-started)
            if reply.get("success") is True:
                result = reply.get("result")
                inspect_payload(action, result)
                return result
            if authenticated and attempt == 0 and code in SESSION_ERRORS:
                await self.login(expired=observed_session)
                continue
            error_type = LoginRejected if code in ACCOUNT_ERRORS else CloudFailure
            raise error_type(action=action, code=safe_code, http_status=status)

    async def login(self, *, expired=None):
        async with self.auth_lock:
            if expired is not None and self.session_id and self.session_id != expired:
                return
            email = "@" in self.account
            account_field = "email" if email else "mobile"
            token_method = "tuya.m.user.email.token.create" if email else "tuya.m.user.mobile.token.get"
            login_method = "tuya.m.user.email.password.login" if email else "tuya.m.user.mobile.passwd.login"
            token = await self.exchange(token_method, {account_field:self.account, "countryCode":self.country}, authenticated=False)
            if not isinstance(token, dict) or not token.get("token"):
                raise LoginRejected(action=token_method, code="INVALID_AUTH_RESPONSE")
            reply = await self.exchange(login_method, {account_field:self.account, "countryCode":self.country,
                "passwd":password_proof(self.password), "options":json.dumps({"group":1}),
                "token":token["token"], "ifencrypt":0}, authenticated=False)
            if not isinstance(reply, dict) or not isinstance(reply.get("sid"), str) or not reply["sid"]:
                raise LoginRejected(action=login_method, code="INVALID_AUTH_RESPONSE")
            self.session_id = reply["sid"]

    async def inventory(self):
        homes = rows(await self.exchange("tuya.m.location.list"), "list", "groups")
        devices = {}
        for home in homes:
            group = next((home[key] for key in ("groupId", "gid", "ownerId") if home.get(key) is not None), None)
            if group is None:
                continue
            listed = await self.exchange("tuya.m.my.group.device.list", group=group)
            for device in rows(listed, "devices", "list"):
                if device.get("devId"):
                    device = {**device, "_group_id":group}
                    info = device.get("dataPointInfo")
                    if isinstance(info, dict) and "dps" not in device:
                        device["dps"] = info.get("dps", {})
                    devices.setdefault(str(device["devId"]), device)
        return list(devices.values())

    async def one_device(self, group, ident):
        payload = await self.exchange("tuya.m.my.group.device.list", group=group)
        return next((row for row in rows(payload, "devices", "list") if row.get("devId") == ident), None)

    async def rooms(self, group):
        payload = await self.exchange("tuya.m.room.list", {"gid":int(group)}, group=group)
        rooms = rows(payload, "rooms", "list")
        names = {str(room.get("id", room.get("roomId"))):room.get("name", room.get("roomName")) for room in rooms}
        found = room_membership(payload, names)
        if not found:
            payload = await self.exchange("tuya.m.my.group.device.sort.list", {"gid":str(group)}, version="1.0", group=group)
            found = room_membership(payload, names)
        LOG.warning("cloud rooms=%d matched_devices=%d", len(names), len(found))
        return found

    async def product_definitions(self, group):
        try:
            return product_catalogs(await self.exchange("tuya.m.product.standard.config.list", group=group))
        except CloudFailure:
            return {}

    async def hydrate(self, devices, progress=None):
        versions = {}
        for group in dict.fromkeys(row.get("_group_id") for row in devices if row.get("_group_id") is not None):
            try:
                versions.update(version_values(await self.exchange("tuya.m.device.ext.prop.list", group=group, version="1.0")))
            except CloudFailure:
                pass
        missing = []
        for device in devices:
            version = versions.get(str(device["devId"])) or device.get("productVer") or device.get("productVersion")
            if version:
                device["productVer"] = str(version)
            else:
                missing.append(device)
        completed = len(devices) - len(missing)
        failures = 0
        semaphore = asyncio.Semaphore(4)

        async def read(device):
            nonlocal completed, failures
            async with semaphore:
                try:
                    detail = await self.exchange("tuya.m.device.get", {"devId":device["devId"]}, version="4.1", group=device.get("_group_id"))
                    choices = version_values(detail)
                    if isinstance(detail, dict):
                        version = choices.get(str(device["devId"])) or detail.get("productVer") or detail.get("productVersion")
                        if version:
                            device["productVer"] = str(version)
                        names = dp_names(detail)
                        if names:
                            device["_channel_names"] = names
                        if isinstance(detail.get("roomName"), str) and detail["roomName"].strip():
                            device["_room"] = detail["roomName"].strip()
                    if not device.get("productVer"):
                        failures += 1
                except CloudFailure:
                    failures += 1
                finally:
                    completed += 1
                    if progress:
                        progress(completed, len(devices))
        if progress:
            progress(completed, len(devices))
        async with asyncio.TaskGroup() as workers:
            for device in missing:
                workers.create_task(read(device))
        self.last_version_note = "Some device metadata is unavailable" if failures else ""

    async def panel_definitions(self, pairs):
        if not pairs:
            return {}
        data = {"productIds":json.dumps([{"pid":pid, "ver":version} for pid, version in pairs], separators=(",", ":")), "zigbeeGroup":True}
        try:
            payload = await self.exchange("tuya.m.device.ref.info.list", data, version="6.1")
        except CloudFailure:
            self.last_schema_note = "Product definitions unavailable"
            return {}
        self.last_schema_note = ""
        result = {}
        for item in rows(payload, "list", "products"):
            pid = item.get("id", item.get("productId"))
            version = item.get("productVer", item.get("ver"))
            if pid and version:
                result[f"{pid}|{version}"] = item
        return result

    async def send(self, device, values):
        ident = device["devId"]
        gateway = device.get("meshId") or device.get("parentId") or ident
        return await self.exchange("tuya.m.device.dp.publish", {"devId":ident, "gwId":gateway,
            "dps":json.dumps(values, separators=(",", ":"))})
