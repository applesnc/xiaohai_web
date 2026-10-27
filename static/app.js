"use strict";

const $ = (selector) => document.querySelector(selector);
const icon = (name, extra = "") => `<svg class="icon ${extra}" aria-hidden="true"><use href="#i-${name}"/></svg>`;
const escapeHTML = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[char]));
const numberText = (value) => typeof value === "number" && Number.isFinite(value) ? Number(value.toFixed(1)).toString() : "—";
let snapshot = {connected:false, devices:[], events:[]};
let view = "all", room = "", type = "all", onlineOnly = false, query = "";
let polling = false, loginBusy = false;
let lastPoll = 0;
const busy = new Set();
let favorites = new Set();
let favoriteKey = "xiaohai-favorites";

function loadFavorites() {
  favoriteKey = `xiaohai-favorites:${snapshot.demo ? "demo" : snapshot.account_key || "guest"}`;
  try { favorites = new Set(JSON.parse(localStorage.getItem(favoriteKey) || "[]")); }
  catch { favorites = new Set(); }
}

async function api(path, data) {
  const options = {method:data === undefined ? "GET" : "POST", cache:"no-store", credentials:"same-origin", signal:AbortSignal.timeout(150000)};
  if (data !== undefined) {
    options.headers = {"Content-Type":"application/json", "X-Local-Request":"1"};
    options.body = JSON.stringify(data);
  }
  let response;
  try { response = await fetch(path, options); }
  catch (error) { throw new Error(error.name === "TimeoutError" ? "连接超时，请稍后刷新确认结果。" : "无法连接本机服务，请检查服务是否正在运行。"); }
  let result;
  try { result = await response.json(); }
  catch { throw new Error("服务返回了无法识别的内容。"); }
  if (!response.ok) throw new Error(result.error || "操作未完成，请稍后重试。");
  return result;
}

function toast(message, error = false) {
  const element = document.createElement("div");
  element.className = `toast${error ? " error" : ""}`;
  element.textContent = message;
  $("#toasts").append(element);
  setTimeout(() => element.remove(), error ? 6500 : 4200);
}

function accept(data, showEvents = false) {
  const previous = new Set((snapshot.events || []).map(event => event.id));
  const changedAccount = snapshot.account_key !== data.account_key || snapshot.demo !== data.demo;
  snapshot = {devices:[], events:[], ...data};
  if (changedAccount) loadFavorites();
  if (showEvents) {
    snapshot.events.filter(event => !previous.has(event.id) && event.status !== "info").slice(0, 2)
      .forEach(event => toast(event.message, event.status === "warning"));
  }
  if (!snapshot.connected) { room = ""; view = "all"; }
  render();
}

function roomNames() { return [...new Set(snapshot.devices.map(device => device.room))].sort((a,b) => a.localeCompare(b, "zh-CN")); }

function render() {
  const connected = snapshot.connected;
  const devices = snapshot.devices;
  const rooms = roomNames();
  if (room && !rooms.includes(room)) room = "";
  const label = room || (view === "favorites" ? "我的常用" : "全部设备");
  $("#home-title").textContent = snapshot.demo ? "演示家庭" : "我的家庭";
  $("#home-subtitle").textContent = connected ? snapshot.account : "还未连接账号";
  $("#breadcrumb-view").textContent = label;
  $("#connection-label").textContent = connected ? (snapshot.demo ? "演示模式" : snapshot.loading ? "已连接 · 正在加载" : snapshot.stale ? "连接需检查" : "小海云已连接") : "连接小海云";
  $("#connection-open .status-dot").classList.toggle("disconnected", !connected || (snapshot.stale && !snapshot.loading));
  $("#demo-banner").hidden = !snapshot.demo;
  $("#warning-banner").hidden = !snapshot.warning && !snapshot.loading;
  $("#warning-banner").textContent = snapshot.loading ? snapshot.progress + " 加载完成后即可控制。" : snapshot.warning || "";
  $("#hero-description").textContent = connected ? (snapshot.demo ? "正在体验模拟家庭，连接账号后即可读取你的真实设备。" : `${rooms.length} 个分组，${devices.length} 台设备。灯光、窗帘与冷暖，一处掌握。`) : "灯光、窗帘、开关与冷暖，在一个页面里掌握。";
  $("#hero-status").textContent = connected ? (snapshot.demo ? "演示数据 · 无真实控制" : "直连小海云，无需家庭端口映射") : "等待与你的家庭连接";
  $("#stat-all").textContent = connected ? devices.length : "—";
  $("#stat-on").textContent = connected ? devices.filter(d => d.power === true && d.online !== false).length : "—";
  $("#stat-rooms").textContent = connected ? rooms.filter(name => !["未分配房间", "全屋设备"].includes(name)).length : "—";
  $("#stat-online").textContent = connected ? devices.filter(d => d.online === true).length : "—";
  $("#nav-count").textContent = devices.length;
  $("#room-count").textContent = rooms.length;
  document.querySelectorAll("[data-view]").forEach(button => button.classList.toggle("active", !room && button.dataset.view === view));
  $("#room-nav").innerHTML = rooms.length ? rooms.map(name => `<button class="nav-item room-button${room === name ? " active" : ""}" data-room="${escapeHTML(name)}"><span class="room-dot"></span>${escapeHTML(name)}<span class="room-num">${devices.filter(d => d.room === name).length}</span></button>`).join("") : '<p class="room-placeholder">连接后自动读取房间</p>';
  $("#room-select").innerHTML = '<option value="">所有房间</option>' + rooms.map(name => `<option value="${escapeHTML(name)}"${room === name ? " selected" : ""}>${escapeHTML(name)}</option>`).join("");
  $("#room-select").disabled = !connected;
  $("#refresh").disabled = !connected || polling || snapshot.loading;
  $("#rediscover").disabled = polling || snapshot.loading;
  $("#refresh").classList.toggle("loading", polling);
  $("#sync-label").textContent = connected ? (snapshot.loading ? snapshot.progress : snapshot.updated_at ? `上次读取 ${new Date(snapshot.updated_at * 1000).toLocaleTimeString("zh-CN", {hour12:false})}${snapshot.stale ? " · 数据已过期" : " · 每 30 秒更新"}` : "正在读取设备") : "连接账号后，自动同步设备与房间";
  $("#empty-state").hidden = connected;
  $("#device-grid").hidden = !connected;
  const matching = devices.filter(device => {
    const typeMatch = type === "all" || (type === "other" ? !["climate","heating","fan","light","curtain","switch","gateway"].includes(device.kind) : device.kind === type);
    return (!room || device.room === room) && (view !== "favorites" || favorites.has(device.id)) && typeMatch && (!onlineOnly || device.online === true) && `${device.name} ${device.room}`.toLocaleLowerCase().includes(query);
  });
  $("#section-title").innerHTML = `${escapeHTML(label)} <span id="section-count">${matching.length}</span>`;
  if (connected) {
    $("#device-grid").innerHTML = matching.length ? matching.map(card).join("") : `<div class="empty-filter">${icon("search")}<p>${devices.length ? "没有符合筛选条件的设备" : snapshot.loading ? "账号已连接，正在加载你的设备…" : "账号已连接，暂未读取到设备"}</p><button class="text-button" id="reset-filters"${snapshot.loading && !devices.length ? " disabled" : ""}>${devices.length ? "清除筛选条件" : "重新读取设备"}</button></div>`;
  }
  renderAccount();
  renderActivity();
}

function card(device) {
  const offline = device.online === false;
  const pending = busy.has(device.id) || device.command?.status === "pending";
  const disabled = offline || pending || snapshot.stale || snapshot.loading || !snapshot.ready;
  const disabledAttr = disabled ? " disabled" : "";
  const names = {climate:"空调",heating:"地暖",fan:"新风",light:"灯光",curtain:"窗帘",switch:"开关面板",gateway:"网关",other:"设备"};
  const icons = {climate:"ac",heating:"heat",fan:"fan",light:"light",curtain:"curtain",switch:"switch",gateway:"cloud",other:"device"};
  const onlineText = offline ? "离线" : device.online === true ? "在线" : "在线状态未知";
  const selected = favorites.has(device.id);
  let status = device.power === true ? "运行中" : device.power === false ? "已关闭" : "仅状态展示";
  let stateClass = device.power ? "running" : "";
  if (device.channels?.length && device.power === null) status = "场景面板";
  if (device.kind === "curtain") status = "窗帘控制";
  if (device.kind === "gateway") status = device.gateway_state || "网关状态";
  if (device.command?.status === "sent") status = "场景请求已发送";
  if (device.command?.status === "unconfirmed") { status = "结果未确认，请核对设备"; stateClass = "pending"; }
  if (snapshot.stale) { status = "状态过期 · 控制已暂停"; stateClass = "pending"; }
  if (snapshot.loading) { status = "正在加载功能 · 暂不可控制"; stateClass = "pending"; }
  if (offline) { status = "设备离线"; stateClass = ""; }
  if (pending) { status = "已发送 · 等待云端确认"; stateClass = "pending"; }
  let content = "";
  if (["climate","heating"].includes(device.kind)) {
    const value = device.target;
    content = `<div class="temperature-row"><div><div class="temperature-display">${numberText(value)}<small>°C</small></div><p class="temperature-caption">设定温度</p></div><div class="temp-actions">${[[-1,"minus","降低"],[1,"plus","升高"]].map(([direction,name,label]) => `<button data-action="temperature" data-direction="${direction}" aria-label="${escapeHTML(device.name)}${label}温度"${disabled || !device.can_temperature || typeof value !== "number" || (direction < 0 ? value <= device.min_temp : value >= device.max_temp) ? " disabled" : ""}>${icon(name)}</button>`).join("")}</div></div><div class="current-temp">${icon("temp")}室内温度 <span>${numberText(device.temperature)} °C</span></div>`;
  } else if (device.kind === "fan") {
    const current = device.fan_speeds.find(item => item.value === device.fan_speed);
    content = `<div class="fan-visual">${icon("fan")}<div><strong>${escapeHTML(current?.label || "—")}</strong><p>当前风速</p></div></div>`;
  } else if (device.channels?.length) {
    content = `<p class="channel-summary">${device.channels.length} 个通道${device.channels.length > 3 ? " · 上下滚动查看全部" : ""}</p><div class="channel-list">${device.channels.map(channel => `<div class="channel-row"><div class="channel-head"><span>${escapeHTML(channel.label)}</span>${channel.kind === "scene" ? `<button class="scene-button" data-action="scene" data-value="${escapeHTML(channel.scene_code)}"${disabled || !channel.can_scene ? " disabled" : ""}>触发场景</button>` : `<button class="power-toggle" role="switch" data-action="channel_power" data-channel="${escapeHTML(channel.key)}" aria-label="${escapeHTML(device.name + channel.label)}" aria-checked="${channel.on === true}"${disabled || !channel.can_power ? " disabled" : ""}></button>`}</div>${channel.can_brightness ? `<label class="percent-control"><span>亮度 <output>${numberText(channel.brightness)}%</output></span><input type="range" min="0" max="100" step="1" value="${channel.brightness ?? 0}" data-action="brightness" data-channel="${escapeHTML(channel.key)}" aria-label="${escapeHTML(device.name + channel.label)}亮度"${disabledAttr}></label>` : ""}</div>`).join("")}</div>`;
  } else if (device.kind === "curtain") {
    const stopDisabled = offline || snapshot.stale || snapshot.loading || !snapshot.ready || busy.has(device.id);
    content = `<div class="curtain-buttons">${[["open","打开"],["stop","停止"],["close","关闭"]].filter(([value]) => device.curtain_actions.includes(value)).map(([value,label]) => `<button data-action="curtain" data-value="${value}"${(value === "stop" ? stopDisabled : disabled) ? " disabled" : ""}>${label}</button>`).join("")}</div>${device.can_position ? `<label class="percent-control curtain-position"><span>目标位置 <output>${numberText(device.position)}%</output></span><input type="range" min="0" max="100" step="${device.position_step}" value="${device.position ?? 0}" data-action="position" aria-label="${escapeHTML(device.name)}目标位置"${disabledAttr}><small>每次调节 ${numberText(device.position_step)}%</small></label>` : ""}`;
  } else {
    content = `<div class="gateway-state">${icon(device.kind === "switch" ? "power" : "cloud")}<span>${device.kind === "gateway" ? escapeHTML(device.gateway_state) : device.kind === "switch" ? (device.power ? "已开启" : "已关闭") : "设备连接状态"}</span></div>`;
  }
  const select = (action,label,values,current) => `<label class="control-select"><span>${label}</span><select data-action="${action}" aria-label="${escapeHTML(device.name)}${label}"${disabledAttr}>${!values.some(item => item.value === current) ? '<option value="" disabled selected>未知</option>' : ""}${values.map(item => `<option value="${escapeHTML(item.value)}"${item.value === current ? " selected" : ""}>${escapeHTML(item.label)}</option>`).join("")}</select></label>`;
  const controls = (device.can_mode ? select("mode","模式",device.modes,device.mode) : "") + (device.can_fan ? select("fan","风速",device.fan_speeds,device.fan_speed) : "");
  return `<article class="device-card kind-${escapeHTML(device.kind)}${device.power && !offline ? " running" : ""}${offline ? " offline-card" : ""}" data-id="${escapeHTML(device.id)}" aria-label="${escapeHTML(device.name)}"><div class="card-top"><span class="device-icon">${icon(icons[device.kind] || "device")}</span><div class="card-name"><h3>${escapeHTML(device.name)}</h3><div class="card-meta"><button class="room-tag" data-room-device="${escapeHTML(device.id)}" title="${({cloud:"云端房间",local:"本地分配",name:"根据名称归类",group:"全屋分组"})[device.room_source] || "房间"}·点击调整">${escapeHTML(device.room)} ✎</button><span>·</span><span class="status-dot${offline ? " offline-dot" : device.online === null ? " unknown-dot" : ""}"></span>${onlineText}</div></div><button class="favorite-button${selected ? " selected" : ""}" data-favorite="${escapeHTML(device.id)}" aria-label="${selected ? "取消常用" : "添加常用"}：${escapeHTML(device.name)}" aria-pressed="${selected}">${icon("star")}</button></div><div class="card-body">${content}${controls ? `<div class="device-controls">${controls}</div>` : ""}${device.note ? `<p class="device-note">${escapeHTML(device.note)}</p>` : ""}</div><div class="card-footer"><span class="device-status ${stateClass}">${icon(pending ? "refresh" : device.power ? "power" : "check")}${status}</span>${device.can_power ? `<button class="power-toggle" role="switch" aria-checked="${device.power === true}" aria-label="${escapeHTML(device.name)}电源" data-action="power"${disabledAttr}></button>` : `<span class="temperature-caption">${names[device.kind] || "设备"}</span>`}</div></article>`;
}

function renderAccount() {
  $("#connected-account").hidden = !snapshot.connected;
  $("#login-form").hidden = snapshot.connected;
  $("#dialog-demo").hidden = snapshot.connected;
  $("#dialog-title").textContent = snapshot.connected ? "家庭连接" : "连接你的家庭";
  $("#account-name").textContent = snapshot.account || "";
  $("#account-kind").textContent = snapshot.demo ? "演示模式" : "小海云";
}

function renderActivity() {
  $("#activity-list").innerHTML = snapshot.events.length ? snapshot.events.map(event => `<div class="event ${escapeHTML(event.status)}"><span class="status-dot"></span><div><p>${escapeHTML(event.message)}</p><small>${new Date(event.time * 1000).toLocaleTimeString("zh-CN",{hour12:false})}</small></div></div>`).join("") : '<div class="empty-filter">还没有操作记录</div>';
}

function openConnection() {
  $("#login-error").hidden = true;
  renderAccount();
  if (!$("#connection-dialog").open) $("#connection-dialog").showModal();
}

function setLoginBusy(value) {
  loginBusy = value;
  document.querySelectorAll("#login-form input, #login-form button, #dialog-demo").forEach(element => element.disabled = value);
  $("#login-submit").textContent = value ? "正在验证账号…" : "连接并读取设备 →";
}

async function connect(data) {
  if (loginBusy) return;
  setLoginBusy(true);
  $("#login-error").hidden = true;
  try {
    const result = await api("/api/login", data);
    $("#password").value = "";
    $("#password").type = "password";
    $("#toggle-password").setAttribute("aria-label", "显示密码");
    accept(result);
    $("#connection-dialog").close();
    toast(data.demo ? "已进入演示家庭，操作不会发送到真实设备" : "账号连接成功，正在后台同步设备与功能");
  } catch (error) {
    if (!$("#connection-dialog").open) $("#connection-dialog").showModal();
    $("#login-error").textContent = error.message;
    $("#login-error").hidden = false;
  } finally { data.password = ""; setLoginBusy(false); }
}

async function disconnect(openLogin = false) {
  try {
    await api("/api/logout", {});
    accept({connected:false});
    $("#password").value = "";
    if (openLogin) openConnection(); else $("#connection-dialog").close();
    toast("已退出，账号凭据已从服务内存清除");
  } catch (error) { toast(error.message, true); }
}

async function refresh(discover = false) {
  if (polling || !snapshot.connected || snapshot.loading) return;
  polling = true;
  render();
  $("#rediscover").disabled = true;
  try {
    accept(await api("/api/refresh", {discover}), true);
    toast(discover && !snapshot.demo ? "正在后台重新读取设备与功能" : "已读取最新云端状态");
  } catch (error) {
    snapshot.stale = true;
    snapshot.warning = error.message;
    toast(error.message, true);
  } finally { polling = false; $("#rediscover").disabled = false; render(); }
}

async function control(id, action, value) {
  if (busy.has(id)) return;
  busy.add(id);
  render();
  try {
    const result = await api(`/api/devices/${encodeURIComponent(id)}/control`, {action, value});
    accept(result.snapshot);
    toast(snapshot.demo ? "演示设备状态已更新" : action === "scene" ? "场景触发请求已发送，请核对现场结果" : "命令已发送，正在读取云端状态");
  } catch (error) { toast(error.message, true); }
  finally { busy.delete(id); render(); lastPoll = 0; setTimeout(poll, 500); }
}

document.querySelectorAll("#settings-open, #connection-open, #empty-connect").forEach(button => button.addEventListener("click", openConnection));
document.querySelectorAll("[data-close]").forEach(button => button.addEventListener("click", () => $(`#${button.dataset.close}`).close()));
let roomDevice = "";
$("#room-form").addEventListener("submit", async event => {
  event.preventDefault();
  const button = $("#room-save");
  button.disabled = true;
  try {
    accept(await api(`/api/devices/${encodeURIComponent(roomDevice)}/room`, {room:$("#room-name").value.trim()}));
    $("#room-dialog").close();
    toast("房间分配已保存到当前账号");
  } catch (error) { toast(error.message, true); }
  finally { button.disabled = false; }
});
$("#room-reset").addEventListener("click", async () => {
  try {
    accept(await api(`/api/devices/${encodeURIComponent(roomDevice)}/room`, {room:""}));
    $("#room-dialog").close();
    toast("已恢复自动归类");
  } catch (error) { toast(error.message, true); }
});
$("#activity-open").addEventListener("click", () => $("#activity-dialog").showModal());
$("#mobile-activity").addEventListener("click", () => $("#activity-dialog").showModal());
document.querySelectorAll("dialog").forEach(dialog => {
  dialog.addEventListener("click", event => { if (event.target === dialog) { const r = dialog.getBoundingClientRect(); if (event.clientX < r.left || event.clientX > r.right || event.clientY < r.top || event.clientY > r.bottom) dialog.close(); } });
});
$("#login-form").addEventListener("submit", event => {
  event.preventDefault();
  connect({username:$("#username").value.trim(), password:$("#password").value, country_code:$("#country").value});
});
$("#toggle-password").addEventListener("click", () => {
  const show = $("#password").type === "password";
  $("#password").type = show ? "text" : "password";
  $("#toggle-password").setAttribute("aria-label", show ? "隐藏密码" : "显示密码");
});
$("#empty-demo").addEventListener("click", () => connect({demo:true}));
$("#dialog-demo").addEventListener("click", () => connect({demo:true}));
$("#demo-connect").addEventListener("click", () => disconnect(true));
$("#disconnect").addEventListener("click", () => disconnect());
$("#refresh").addEventListener("click", () => refresh());
$("#rediscover").addEventListener("click", () => refresh(true));
$("#search").addEventListener("input", event => { query = event.target.value.trim().toLocaleLowerCase(); render(); });
$("#online-filter").addEventListener("click", () => {
  onlineOnly = !onlineOnly;
  $("#online-filter").setAttribute("aria-pressed", String(onlineOnly));
  render();
});
$("#type-filters").addEventListener("click", event => {
  const button = event.target.closest("[data-type]");
  if (!button) return;
  type = button.dataset.type;
  document.querySelectorAll("[data-type]").forEach(item => item.classList.toggle("active", item === button));
  render();
});
document.querySelectorAll("[data-view]").forEach(button => button.addEventListener("click", () => { view = button.dataset.view; room = ""; render(); }));
$("#room-nav").addEventListener("click", event => {
  const button = event.target.closest("[data-room]");
  if (button) { room = button.dataset.room; view = "all"; render(); }
});
$("#room-select").addEventListener("change", event => { room = event.target.value; view = "all"; render(); });
$("#device-grid").addEventListener("click", event => {
  const roomButton = event.target.closest("[data-room-device]");
  if (roomButton) {
    roomDevice = roomButton.dataset.roomDevice;
    const device = snapshot.devices.find(item => item.id === roomDevice);
    $("#room-device-name").textContent = device.name;
    $("#room-name").value = device.room;
    $("#room-options").innerHTML = roomNames().map(name => `<option value="${escapeHTML(name)}"></option>`).join("");
    $("#room-source").textContent = ({cloud:"当前来自小海云房间。",local:"当前使用你在本机分配的房间。",name:"当前按设备名称自动归类，可以修改。",group:"名称未标明具体房间，暂放在全屋设备分组。"})[device.room_source] || "可在本机调整房间。";
    $("#room-dialog").showModal();
    return;
  }
  const favorite = event.target.closest("[data-favorite]");
  if (favorite) {
    const id = favorite.dataset.favorite;
    if (favorites.has(id)) favorites.delete(id); else favorites.add(id);
    try { localStorage.setItem(favoriteKey, JSON.stringify([...favorites])); } catch {}
    render(); return;
  }
  if (event.target.closest("#reset-filters")) {
    if (!snapshot.devices.length) { refresh(true); return; }
    type = "all"; view = "all"; room = ""; query = ""; onlineOnly = false;
    $("#search").value = "";
    $("#online-filter").setAttribute("aria-pressed", "false");
    document.querySelectorAll("[data-type]").forEach(item => item.classList.toggle("active", item.dataset.type === "all"));
    render(); return;
  }
  const button = event.target.closest("button[data-action]");
  const article = button?.closest("[data-id]");
  if (!article || button.disabled) return;
  const device = snapshot.devices.find(item => item.id === article.dataset.id);
  if (!device) return;
  if (button.dataset.action === "power") control(device.id, "power", device.power !== true);
  else if (button.dataset.action === "channel_power") {
    const channel = device.channels.find(item => item.key === button.dataset.channel);
    control(device.id, "channel_power", {channel:channel.key, on:channel.on !== true});
  }
  else if (["curtain", "scene"].includes(button.dataset.action)) control(device.id, button.dataset.action, button.dataset.value);
  else if (button.dataset.action === "temperature") control(device.id, "temperature", Number((device.target + Number(button.dataset.direction) * device.temp_step).toFixed(4)));
});
$("#device-grid").addEventListener("change", event => {
  const range = event.target.closest('input[type="range"][data-action]');
  if (range) {
    const value = Number(range.value);
    control(range.closest("[data-id]").dataset.id, range.dataset.action, range.dataset.action === "brightness" ? {channel:range.dataset.channel, percent:value} : value);
    return;
  }
  const select = event.target.closest("select[data-action]");
  if (select) control(select.closest("[data-id]").dataset.id, select.dataset.action, select.value);
});
$("#device-grid").addEventListener("input", event => {
  const range = event.target.closest('input[type="range"]');
  if (range) range.closest("label").querySelector("output").textContent = `${numberText(Number(range.value))}%`;
});

async function poll() {
  if (polling || loginBusy || busy.size || document.hidden) return;
  const interval = snapshot.loading || snapshot.refreshing || snapshot.devices.some(device => device.command?.status === "pending") ? 750 : 3500;
  if (Date.now() - lastPoll < interval) return;
  if (!snapshot.loading && document.activeElement?.closest(".device-card")) return;
  lastPoll = Date.now();
  polling = true;
  try { accept(await api("/api/state"), true); }
  catch (error) {
    if (snapshot.connected) { snapshot.stale = true; snapshot.warning = error.message; render(); }
  } finally { polling = false; $("#refresh").disabled = !snapshot.connected || snapshot.loading; $("#rediscover").disabled = snapshot.loading; $("#refresh").classList.remove("loading"); }
}

$("#today").textContent = new Date().toLocaleDateString("zh-CN", {month:"long",day:"numeric",weekday:"long"});
loadFavorites(); render(); poll();
setInterval(poll, 1000);
document.addEventListener("visibilitychange", () => { if (!document.hidden) poll(); });
