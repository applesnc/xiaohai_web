"""Loopback-only Xiaohai dashboard. Credentials and sessions stay in memory."""
from __future__ import annotations

import argparse
import asyncio
from contextlib import suppress
from dataclasses import dataclass, field
import json
import logging
from pathlib import Path
import secrets
import time
from urllib.parse import urlsplit

import aiohttp
from aiohttp import web

from devices import InputError, command_dps, demo_devices, device_view, discover, normalize
from local_store import AccountStore
from rooms import room_for
from cloudio.client import CloudFailure, HomeCloud, LoginRejected

ROOT = Path(__file__).resolve().parent
COOKIE = "xiaohai_local_session"
TTL = 12 * 60 * 60
DISCOVERY_TIMEOUT = 300


@dataclass
class Session:
    account: str
    client: object = None
    http: object = None
    demo: bool = False
    devices: dict = field(default_factory=dict)
    commands: dict = field(default_factory=dict)
    events: list = field(default_factory=list)
    tasks: set = field(default_factory=set)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    updated: float = 0
    touched: float = field(default_factory=time.time)
    warning: str = ""
    stale: bool = False
    loading: bool = False
    progress: str = ""
    store: object = None
    ready: bool = False
    room_overrides: dict = field(default_factory=dict)
    refresh_task: object = None
    refresh_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def start_refresh(self):
        if self.loading or (self.refresh_task and not self.refresh_task.done()):
            return
        async def update():
            try:
                async with asyncio.timeout(90):
                    await self.refresh()
            except LoginRejected:
                self.stale = True
                self.warning = "账号验证已失效，请退出后重新连接。"
            except Exception:
                self.stale = True
                self.warning = "状态刷新失败，当前显示上次读取结果。控制已暂停，请稍后刷新。"
        self.refresh_task = asyncio.create_task(update())
        self.tasks.add(self.refresh_task)
        self.refresh_task.add_done_callback(self.tasks.discard)

    def save_cache(self):
        if self.store and not self.demo:
            try:
                self.store.save(self.devices, self.updated)
            except (OSError, ValueError, TypeError):
                logging.getLogger("xiaohai.cloud").warning("dashboard code=CACHE_WRITE_FAILED")
                self.warning = "本地设备缓存写入失败，当前连接仍可使用。"

    def set_progress(self, message):
        self.progress = message

    def start_discovery(self):
        if self.loading:
            return
        self.loading = True
        self.ready = False
        self.progress = "账号已连接，正在读取云端最新设备列表…"
        task = asyncio.create_task(self.load_devices())
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def load_devices(self):
        try:
            if self.refresh_task and not self.refresh_task.done():
                await self.refresh_task
            async with asyncio.timeout(DISCOVERY_TIMEOUT):
                # Do not hold the request lock across metadata discovery: the
                # page must be able to poll progress and log out while it runs.
                await self.refresh()
                # Fresh lists do not inherit raw fields such as productVer
                # from disk. Only room and schema are retained for display.
                rows = list(self.devices.values())
                found, warnings = await discover(self.client, rows, self.set_progress)
                self.devices = found
                # A long discovery must not label its original DP snapshot fresh.
                if time.time() - self.updated >= 30:
                    self.set_progress("功能已读取，正在更新设备状态…")
                    await self.refresh()
                self.warning = "；".join(warnings)
                self.stale = False
                self.ready = True
                self.save_cache()
                self.record("设备与功能已读取完成", "success")
        except CloudFailure as err:
            self.stale = True
            self.warning = cloud_error_message(err)[0] + " 账号仍保持连接，可点击重新读取设备与功能。"
            self.record(self.warning, "warning")
        except TimeoutError:
            self.stale = True
            self.warning = "设备功能读取超时，账号仍保持连接。可在连接设置中重新读取。"
            logging.getLogger("xiaohai.cloud").warning("dashboard code=DISCOVERY_TIMEOUT")
            self.record(self.warning, "warning")
        except Exception:
            self.stale = True
            self.warning = "设备功能读取未完成，账号仍保持连接。可在连接设置中重新读取。"
            logging.getLogger("xiaohai.cloud").warning("dashboard code=DISCOVERY_FAILED")
            self.record(self.warning, "warning")
        finally:
            self.loading = False
            self.progress = ""

    def record(self, message, status="info"):
        self.events.insert(0, {"id": secrets.token_hex(5), "time": time.time(), "message": message, "status": status})
        self.events = self.events[:30]

    def reconcile(self):
        for dev_id, command in list(self.commands.items()):
            if command["status"] != "pending":
                continue
            device = self.devices.get(dev_id, {})
            dps = device.get("dps", {})
            if command.get("observed") and all(key in dps and type(dps[key]) is type(value) and dps[key] == value for key, value in command["dps"].items()):
                command["status"] = "confirmed"
                self.record(f"{device.get('name', '设备')} · 云端状态已确认", "success")
            elif time.time() - command["time"] > 35:
                command["status"] = "unconfirmed"
                self.record(f"{device.get('name', '设备')} · 尚未确认执行结果，请核对设备", "warning")

    async def refresh(self):
        async with self.refresh_lock:
            await self._refresh()

    async def _refresh(self):
        if self.demo:
            self.updated, self.stale = time.time(), False
            self.ready = True
            self.reconcile()
            return
        started = time.time()
        rows = await self.client.inventory()
        latest = {}
        for item in rows:
            if not isinstance(item, dict) or not item.get("devId"):
                continue
            key = str(item["devId"])
            old = self.devices.get(key, {})
            same_product = item.get("productId") == old.get("productId")
            metadata = {key: old[key] for key in ("_schema", "_room", "_channel_names") if key in old} if same_product else {}
            latest[key] = {**metadata, **normalize(item)}
        self.devices = latest
        for dev_id, command in self.commands.items():
            if dev_id in latest and command["status"] == "pending" and command["time"] < started:
                command["observed"] = True
        if not self.loading and self.ready:
            self.warning = ""
        self.updated, self.stale = time.time(), False
        self.reconcile()
        if not self.loading:
            self.save_cache()

    def view(self):
        self.reconcile()
        devices = []
        for key, device in self.devices.items():
            item = device_view(device)
            item["room"], item["room_source"] = room_for(device, self.room_overrides)
            command = self.commands.get(key)
            item["command"] = {k: v for k, v in command.items() if k not in {"dps", "observed"}} if command else None
            devices.append(item)
        return {"connected": True, "demo": self.demo, "account": self.account,
                "account_key": self.store.key if self.store else "demo",
                "loading": self.loading, "progress": self.progress, "ready": self.ready,
                "refreshing": bool(self.refresh_task and not self.refresh_task.done()),
                "devices": devices, "updated_at": self.updated, "stale": self.stale,
                "warning": self.warning, "events": self.events}

    async def close(self):
        tasks = list(self.tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if self.http:
            await self.http.close()
        self.client = None


SESSIONS = web.AppKey("sessions", dict)
PORT = web.AppKey("port", int)
CLIENT_FACTORY = web.AppKey("client_factory", object)
CLEANUP_TASK = web.AppKey("cleanup_task", object)
LOGIN_LOCK = web.AppKey("login_lock", object)
CACHE_DIR = web.AppKey("cache_dir", object)


def response(data, status=200):
    return web.json_response(data, status=status, dumps=lambda obj: json.dumps(obj, ensure_ascii=False, allow_nan=False))


def cloud_error_message(err):
    action = getattr(err, "action", "")
    code = getattr(err, "code", "UNKNOWN")
    if "token" in action:
        stage = "获取登录令牌"
    elif "login" in action:
        stage = "账号登录"
    elif action == "tuya.m.location.list":
        stage = "读取家庭列表"
    elif "device.list" in action:
        stage = "读取设备列表"
    elif "room" in action or "sort.list" in action:
        stage = "读取房间"
    elif "product" in action or "ref.info" in action or "prop.list" in action or "device.get" in action:
        stage = "读取设备功能"
    else:
        stage = "访问小海云"
    if isinstance(err, LoginRejected):
        explanation = "账号验证未通过，请检查账号、密码和国家码。"
    elif code == "REQUEST_TIMEOUT":
        explanation = "请求超时。"
    elif code == "TRANSPORT_ERROR":
        explanation = "网络连接或证书验证失败。"
    elif "SIGN" in code or "SING" in code or code == "1004":
        explanation = "云端拒绝了接口签名，需要检查客户端协议。"
    else:
        explanation = "云端返回错误。"
    return f"{stage}失败：{explanation} 错误码：{code}", {"stage":stage, "action":action, "code":code, "http_status":getattr(err,"http_status",None)}


@web.middleware
async def boundary(request, handler):
    try:
        port = request.app[PORT]
        if request.host not in {f"127.0.0.1:{port}", f"localhost:{port}"}:
            return response({"error": "仅允许从本机地址访问。"}, 403)
        if request.method not in {"GET", "HEAD"}:
            expected_origin = f"http://{request.host}"
            if request.headers.get("Origin") != expected_origin or request.headers.get("X-Local-Request") != "1":
                return response({"error": "请求来源验证失败，请从本机页面操作。"}, 403)
            if request.content_type != "application/json":
                return response({"error": "请求必须使用 JSON。"}, 415)
        result = await handler(request)
    except InputError as err:
        result = response({"error": str(err)}, 400)
    except CloudFailure as err:
        message, diagnostic = cloud_error_message(err)
        result = response({"error":message, "diagnostic":diagnostic}, 401 if isinstance(err,LoginRejected) else 502)
    except TimeoutError:
        logging.getLogger("xiaohai.cloud").warning("dashboard code=OVERALL_TIMEOUT")
        result = response({"error": "云端读取总时限已到。账号验证或设备读取尚未完成，请根据诊断记录定位。错误码：OVERALL_TIMEOUT"}, 504)
    except aiohttp.ClientError:
        result = response({"error": "连接小海云时发生网络错误。错误码：TRANSPORT_ERROR"}, 502)
    except (json.JSONDecodeError, UnicodeDecodeError):
        result = response({"error": "收到无法识别的数据，请刷新后重试。"}, 400)
    except web.HTTPException as err:
        result = response({"error": "请求地址或方法不受支持。"}, err.status)
    except Exception:
        # Never log cloud payloads, credentials, or session identifiers.
        result = response({"error": "请求未能完成，可能遇到未适配的云端数据。请重新读取设备。"}, 500)
    result.headers.update({"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer", "X-Frame-Options": "DENY",
        "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"})
    return result


async def body(request):
    data = await request.json()
    if not isinstance(data, dict):
        raise InputError("请求内容格式不正确。")
    return data


async def get_session(request):
    session = request.app[SESSIONS].get(request.cookies.get(COOKIE))
    if not session:
        return None
    if time.time() - session.touched > TTL:
        request.app[SESSIONS].pop(request.cookies.get(COOKIE), None)
        await session.close()
        return None
    session.touched = time.time()
    return session


def masked(account):
    if "@" in account:
        name, domain = account.split("@", 1)
        return name[:2] + "***@" + domain
    return account[:3] + "****" + account[-2:]


async def login(request):
    data = await body(request)
    demo = data.get("demo") is True
    if not demo:
        username, password = data.get("username"), data.get("password")
        country = data.get("country_code", "86")
        if not isinstance(username, str) or not 3 <= len(username.strip()) <= 254 or not isinstance(password, str) or not 1 <= len(password) <= 512:
            raise InputError("请填写有效的手机号或邮箱和密码。")
        if not isinstance(country, str) or not country.isdigit() or len(country) > 4:
            raise InputError("国家码格式不正确。")
    async with request.app[LOGIN_LOCK]:
        if len(request.app[SESSIONS]) >= 8 and not request.cookies.get(COOKIE) in request.app[SESSIONS]:
            return response({"error": "本机连接数量已达上限，请先退出其他窗口中的账号。"}, 429)
        session = Session("演示家庭" if demo else masked(username.strip()), demo=demo)
        try:
            if demo:
                session.devices = demo_devices()
                await session.refresh()
            else:
                # Windows automatic proxy bypass discovery cost ~4.5 seconds
                # per request on this workstation. This cloud is directly
                # reachable; keep the setting local to this client.
                session.http = aiohttp.ClientSession(trust_env=False)
                try:
                    session.client = request.app[CLIENT_FACTORY](session.http, username.strip(), password, country)
                except ValueError as err:
                    raise InputError(str(err)) from err
                async with asyncio.timeout(75):
                    await session.client.login()
                session.store = AccountStore(request.app[CACHE_DIR], username.strip(), country)
                session.devices, session.updated = session.store.load()
                session.room_overrides = session.store.load_rooms()
                session.stale = True
        except BaseException:
            await session.close()
            raise
        old = request.app[SESSIONS].pop(request.cookies.get(COOKIE), None)
        if old:
            await old.close()
        token = secrets.token_urlsafe(32)
        request.app[SESSIONS][token] = session
        session.record("已进入演示家庭，操作不会发送到真实设备" if demo else "账号验证成功，正在同步设备", "success")
        if not demo:
            session.start_discovery()
        result = response(session.view())
        result.set_cookie(COOKIE, token, httponly=True, samesite="Strict", path="/")
        return result


async def logout(request):
    session = request.app[SESSIONS].pop(request.cookies.get(COOKIE), None)
    if session:
        await session.close()
    result = response({"connected": False})
    result.del_cookie(COOKIE, path="/")
    return result


async def state(request):
    session = await get_session(request)
    if not session:
        return response({"connected": False})
    if session.loading:
        return response(session.view())
    if time.time() - session.updated >= 30:
        session.start_refresh()
    return response(session.view())


async def refresh(request):
    session = await get_session(request)
    if not session:
        return response({"error": "请先连接小海账号。"}, 401)
    data = await body(request)
    if session.loading:
        return response(session.view())
    async with session.lock:
        if session.loading:
            return response(session.view())
        if data.get("discover") is True and not session.demo:
            session.start_discovery()
            return response(session.view())
        try:
            async with asyncio.timeout(120):
                await session.refresh()
        except Exception:
            session.stale = True
            session.warning = "刷新失败，当前显示上次读取结果。"
            raise
    return response(session.view())


async def read_back(session, dev_id, command):
    try:
        for delay in (0.3, 1, 2, 4, 8, 15):
            await asyncio.sleep(delay)
            if session.commands.get(dev_id) is not command or command["status"] != "pending":
                return
            device = session.devices.get(dev_id)
            if not device:
                return
            try:
                fresh = await session.client.one_device(device["_group_id"], dev_id)
            except Exception:
                fresh = None
            async with session.lock:
                if session.commands.get(dev_id) is not command or command["status"] != "pending":
                    return
                if isinstance(fresh, dict):
                    session.devices[dev_id] = {**session.devices.get(dev_id, device), **normalize(fresh)}
                    command["observed"] = True
                session.reconcile()
    finally:
        session.reconcile()


async def control(request):
    session = await get_session(request)
    if not session:
        return response({"error": "请先连接小海账号。"}, 401)
    data = await body(request)
    dev_id = request.match_info["device"]
    async with session.lock:
        if session.loading or not session.ready:
            raise InputError("正在读取设备功能，请等待加载完成后再控制。")
        if session.stale or time.time() - session.updated > 90:
            raise InputError("设备状态已过期，请先刷新状态。")
        device = session.devices.get(dev_id)
        if not device:
            raise InputError("未找到这台设备，请重新读取设备列表。")
        previous = session.commands.get(dev_id)
        is_stop = data.get("action") == "curtain" and data.get("value") == "stop"
        if previous and previous["status"] == "pending" and not is_stop:
            raise InputError("上一条命令仍在等待状态确认，请稍候。")
        dps = command_dps(device, data.get("action"), data.get("value"))
        momentary = data.get("action") == "scene"
        command = {"status": "sent" if momentary else "pending", "time": time.time(), "dps": dps,
                   "observed": session.demo}
        if session.demo:
            device["dps"].update(dps)
        else:
            await session.client.send(device, dps)
        session.commands[dev_id] = command
        label = "场景触发请求已发送，请核对现场结果" if momentary else "命令已发送"
        session.record(f"{device.get('name', '设备')} · {label}", "info")
        if session.demo:
            session.reconcile()
        elif not momentary:
            task = asyncio.create_task(read_back(session, dev_id, command))
            session.tasks.add(task)
            task.add_done_callback(session.tasks.discard)
        return response({"accepted": True, "snapshot": session.view()})


async def assign_room(request):
    session = await get_session(request)
    if not session:
        return response({"error": "请先连接小海账号。"}, 401)
    data = await body(request)
    room = data.get("room")
    if not isinstance(room, str) or len(room.strip()) > 32 or any(ord(char) < 32 for char in room):
        raise InputError("房间名称应为不超过 32 个字符的文本。")
    dev_id = request.match_info["device"]
    async with session.lock:
        if dev_id not in session.devices:
            raise InputError("未找到这台设备。")
        overrides = dict(session.room_overrides)
        if room.strip():
            overrides[dev_id] = room.strip()
        else:
            overrides.pop(dev_id, None)
        if session.store:
            try:
                session.store.save_rooms(overrides)
            except OSError:
                return response({"error": "房间设置未能保存到本机，请稍后重试。"}, 500)
        session.room_overrides = overrides
    return response(session.view())


async def static(request):
    name = request.match_info.get("name", "index.html")
    if name not in {"index.html", "app.js", "style.css", "favicon.svg"}:
        raise web.HTTPNotFound()
    return web.FileResponse(ROOT / "static" / name)


async def health(request):
    return response({"app": "xiaohai-local-control", "version": "2.0.0"})


async def lifecycle(app):
    async def expire():
        while True:
            await asyncio.sleep(60)
            for token, session in list(app[SESSIONS].items()):
                if time.time() - session.touched > TTL:
                    app[SESSIONS].pop(token, None)
                    await session.close()
    task = asyncio.create_task(expire())
    yield
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task
    await asyncio.gather(*(session.close() for session in list(app[SESSIONS].values())))


def create_app(port=8766, client_factory=HomeCloud, cache_dir=None):
    app = web.Application(middlewares=[boundary], client_max_size=16 * 1024)
    app[SESSIONS], app[PORT], app[CLIENT_FACTORY], app[LOGIN_LOCK] = {}, port, client_factory, asyncio.Lock()
    app[CACHE_DIR] = Path(cache_dir) if cache_dir is not None else ROOT / ".local-data" / "accounts"
    app.cleanup_ctx.append(lifecycle)
    app.add_routes([web.get("/", static), web.get("/static/{name}", static),
                    web.get("/api/health", health),
                    web.get("/api/state", state), web.post("/api/login", login),
                    web.post("/api/logout", logout), web.post("/api/refresh", refresh),
                    web.post("/api/devices/{device}/control", control),
                    web.post("/api/devices/{device}/room", assign_room)])
    return app


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Xiaohai local control dashboard")
    parser.add_argument("--port", type=int, default=8766)
    args = parser.parse_args()
    print(f"Xiaohai control: http://127.0.0.1:{args.port}", flush=True)
    web.run_app(create_app(args.port), host="127.0.0.1", port=args.port, access_log=None, print=None)
