const $ = (id) => document.getElementById(id);
const state = {
  view: "overview",
  config: null,
  services: [],
  sessions: [],
  status: null,
  logs: [],
  selected: null,
  messages: [],
  live: new Map(),
};
const labels = {
  standby: "待机",
  listening: "聆听中",
  thinking: "思考中",
  speaking: "播报中",
  disconnected: "已断开",
};
const eventLabels = {
  "session.connected": "客户端已连接",
  "session.disconnected": "客户端已断开",
  "session.state": "会话状态变化",
  "session.activated": "会话已激活",
  "message.user": "收到用户消息",
  "speech.started": "检测到用户起声",
  "audio.start": "开始发送语音",
  "audio.end": "音频发送结束",
  "turn.completed": "本轮完成",
  "playback.clear": "清除待播音频",
  "playback.unconfirmed": "播放尚未确认",
  "config.saved": "配置已保存",
  "transcript.final": "识别完成",
  "reply.final": "回复生成完成",
  error: "处理错误",
};
const time = (value) =>
  new Date((value || Date.now() / 1000) * 1000).toLocaleTimeString("zh-CN", {
    hour12: false,
  });
const dateTime = (value) =>
  new Date(value * 1000).toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
function node(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;
  return n;
}
function empty(target, title, detail) {
  target.replaceChildren();
  const n = node("div", "empty");
  n.append(node("span", "", "◎"), node("h3", "", title), node("p", "", detail));
  target.append(n);
}
function notice(message, error = false) {
  $("notice").hidden = false;
  $("notice").textContent = message;
  $("notice").classList.toggle("error", error);
}
async function api(path, options = {}) {
  const response = await fetch("/api/" + path, options);
  if (response.status === 401) {
    $("login").hidden = false;
    throw new Error("请使用管理员令牌登录");
  }
  if (!response.ok) {
    let message = "请求失败（" + response.status + "）";
    try {
      const data = await response.json();
      message = data.error?.message || message;
    } catch {}
    throw new Error(message);
  }
  return response.json();
}
function view(name) {
  if (!["overview", "conversations", "models", "logs", "lab"].includes(name))
    name = "overview";
  state.view = name;
  document
    .querySelectorAll("[data-panel]")
    .forEach((el) => (el.hidden = el.dataset.panel !== name));
  document
    .querySelectorAll("[data-view]")
    .forEach((el) => el.classList.toggle("active", el.dataset.view === name));
  $("page-title").textContent = {
    overview: "运行总览",
    conversations: "对话记录",
    models: "模型与语音",
    logs: "事件日志",
    lab: "连接实验室",
  }[name];
  if (location.hash !== "#" + name) history.replaceState(null, "", "#" + name);
}
document
  .querySelectorAll("[data-view]")
  .forEach((el) => (el.onclick = () => view(el.dataset.view)));
$("go-lab").onclick = () => view("lab");
$("go-logs").onclick = () => view("logs");
window.onhashchange = () => view(location.hash.slice(1));
view(location.hash.slice(1));
setInterval(() => ($("clock").textContent = time()), 1000);

function renderOverview() {
  const s = state.status;
  if (!s) return;
  $("online-count").textContent = s.online;
  $("recent-count").textContent = state.sessions.length;
  $("session-count").textContent = state.sessions.length;
  $("vad-status").textContent = s.vad_available ? "可用" : "待配置";
  const cfg = state.config;
  if (cfg) {
    $("engine-status").textContent =
      cfg.llm.provider === "mlx" ? "本地 MLX" : "HTTP API";
    $("engine-model").textContent = cfg.llm.model;
  }
  const devices = $("devices");
  devices.replaceChildren();
  if (!s.sessions.length)
    empty(
      devices,
      "暂时没有在线设备",
      "前往连接实验室，验证你的第一个客户端。",
    );
  for (const device of s.sessions) {
    const row = node("div", "device-row");
    const text = node("div");
    text.append(
      node("strong", "", device.device_id),
      node("small", "", device.id.slice(0, 12) + " · " + device.wake_mode),
    );
    row.append(
      node("span", "device-icon", "◉"),
      text,
      node("span", "tag", labels[device.state] || device.state),
    );
    devices.append(row);
  }
  const activity = $("activity");
  activity.replaceChildren();
  for (const item of state.logs.slice(0, 5)) {
    const row = node("div", "activity-row");
    row.append(
      node("time", "", time(item.created)),
      node("span", "event-label", eventLabels[item.type] || item.type),
      node("span", "tag", item.type === "error" ? "ERROR" : "EVENT"),
    );
    activity.append(row);
  }
  if (!state.logs.length)
    empty(
      activity,
      "等待第一条事件",
      "连接、语音处理与模型异常会自动出现在这里。",
    );
}
function renderSessions() {
  const target = $("sessions");
  const scrollTop = target.scrollTop;
  target.replaceChildren();
  if (!state.sessions.length)
    empty(target, "还没有会话", "记录会在开始对话后出现。");
  for (const s of state.sessions) {
    const b = node(
      "button",
      "session-item" + (s.id === state.selected ? " selected" : ""),
    );
    b.append(
      node("strong", "", s.device_id),
      node("small", "", dateTime(s.updated) + " · " + s.id.slice(0, 8)),
    );
    b.onclick = () => selectSession(s.id);
    b.setAttribute("aria-current", String(s.id === state.selected));
    target.append(b);
  }
  target.scrollTop = scrollTop;
}
const deliveryLabels = {
  confirmed: "已识别",
  generated: "已生成 · 播放未确认",
  played: "客户端确认播完",
  interrupted: "回复已中断",
  failed: "处理失败",
  streaming: "正在生成",
};
function bubble(message) {
  const wrapper = node("div", "message " + message.role);
  const meta = node("div", "meta");
  meta.append(
    node("span", "", message.role === "user" ? "你" : "小宝"),
    node("time", "", time(message.created)),
  );
  wrapper.append(
    meta,
    node("div", "bubble", message.content),
    node("div", "delivery", deliveryLabels[message.status] || message.status),
  );
  return wrapper;
}
function renderMessages(target, messages, sid, includeLive = true, scrollMode = "follow") {
  const previousTop = target.scrollTop;
  const previousHeight = target.scrollHeight;
  const follow = previousHeight - previousTop - target.clientHeight < 80;
  target.replaceChildren();
  for (const m of messages) target.append(bubble(m));
  for (const [key, value] of state.live) {
    if (
      includeLive &&
      value.sid === sid &&
      !messages.some((m) => m.turn_id === key && m.role === "assistant")
    )
      target.append(
        bubble({
          role: "assistant",
          content: value.text,
          status: "streaming",
          created: value.created,
        }),
      );
  }
  if (!target.children.length)
    empty(target, "对话已经准备好", "发送一句话，或开启麦克风。");
  if (scrollMode === "prepend") target.scrollTop = previousTop + target.scrollHeight - previousHeight;
  else if (scrollMode === "end" || follow) target.scrollTop = target.scrollHeight;
  else target.scrollTop = previousTop;
}
async function selectSession(sid, preserveScroll = false) {
  state.selected = sid;
  state.messages = await api("sessions/" + sid + "/messages");
  $("conversation-title").textContent =
    state.sessions.find((s) => s.id === sid)?.device_id || sid.slice(0, 12);
  $("interrupt-session").disabled = !state.status?.sessions.some(
    (s) => s.id === sid,
  );
  $("more-messages").hidden = state.messages.length < 100;
  renderSessions();
  renderMessages($("messages"), state.messages, sid, true, preserveScroll ? "follow" : "end");
}
$("interrupt-session").onclick = () =>
  api("sessions/" + state.selected + "/interrupt", { method: "POST" }).catch(
    (e) => notice(e.message, true),
  );
$("more-messages").onclick = async () => {
  try {
    if (!state.messages.length) return;
    const older = await api(
      "sessions/" + state.selected + "/messages?before=" + state.messages[0].id,
    );
    state.messages = [...older, ...state.messages];
    renderMessages($("messages"), state.messages, state.selected, true, "prepend");
    $("more-messages").hidden = older.length < 100;
  } catch (e) {
    notice(e.message, true);
  }
};
$("more-sessions").onclick = async () => {
  try {
    const last = state.sessions.at(-1);
    if (!last) return;
    const older = await api("sessions?before=" + last.updated);
    state.sessions.push(...older);
    renderSessions();
    $("more-sessions").hidden = older.length < 100;
  } catch (e) {
    notice(e.message, true);
  }
};
function renderLogs() {
  const target = $("logs");
  target.replaceChildren();
  const filter = $("log-filter").value;
  for (const item of state.logs) {
    if (
      filter !== "all" &&
      !item.type.startsWith(filter) &&
      !(filter === "turn" && item.type.startsWith("playback"))
    )
      continue;
    const row = node(
      "div",
      "log-row" + (item.type === "error" ? " error" : ""),
    );
    const detail = node("div", "log-detail");
    const payload = item.payload || {};
    const values = Object.entries(payload)
      .filter(([k]) => !["seq", "timestamp", "text"].includes(k))
      .map(([k, v]) => k + ": " + String(v))
      .join(" · ");
    detail.append(
      node("div", "mono", item.session_id?.slice(0, 12) || "system"),
      node("div", "", values || "—"),
    );
    row.append(
      node("time", "", time(item.created)),
      node("span", "log-type", item.type),
      detail,
    );
    target.append(row);
  }
  if (!target.children.length)
    empty(target, "没有匹配的日志", "切换筛选条件，或开始一段对话。");
}
$("log-filter").onchange = renderLogs;
$("refresh-logs").onclick = () =>
  refresh().catch((e) => notice(e.message, true));
$("more-logs").onclick = async () => {
  try {
    const last = state.logs.at(-1);
    if (!last) return;
    const older = await api("logs?before=" + last.id);
    state.logs.push(...older);
    renderLogs();
    $("more-logs").hidden = older.length < 100;
  } catch (e) {
    notice(e.message, true);
  }
};
let refreshTimer;
async function refresh() {
  const [status, sessions, logs] = await Promise.all([
    api("status"),
    api("sessions"),
    api("logs"),
  ]);
  state.status = status;
  state.sessions = sessions;
  state.logs = logs;
  renderOverview();
  renderSessions();
  renderLogs();
  if (state.selected) await selectSession(state.selected, true);
}
function scheduleRefresh() {
  clearTimeout(refreshTimer);
  refreshTimer = setTimeout(
    () => refresh().catch((e) => notice(e.message, true)),
    150,
  );
}
let monitor, monitorRetry;
function observe() {
  monitor = new WebSocket(
    (location.protocol === "https:" ? "wss://" : "ws://") +
      location.host +
      "/api/events",
  );
  monitor.onopen = () => {
    $("server-dot").classList.remove("off");
    $("connection-label").textContent = "事件流已连接";
    refresh().catch((e) => notice(e.message, true));
  };
  monitor.onmessage = (event) => {
    const e = JSON.parse(event.data);
    if (e.type === "reply.delta") {
      const item = state.live.get(e.turn_id) || {
        sid: e.session_id,
        text: "",
        created: e.timestamp,
      };
      item.text += e.text;
      state.live.set(e.turn_id, item);
      if (state.selected === e.session_id)
        renderMessages($("messages"), state.messages, state.selected);
      return;
    }
    if (
      [
        "turn.completed",
        "playback.clear",
        "session.disconnected",
        "error",
      ].includes(e.type)
    ) {
      if (e.turn_id) state.live.delete(e.turn_id);
      if (e.interrupted_turn_id) state.live.delete(e.interrupted_turn_id);
      if (e.type === "session.disconnected")
        for (const [key, value] of state.live)
          if (value.sid === e.session_id) state.live.delete(key);
    }
    scheduleRefresh();
  };
  monitor.onclose = () => {
    $("server-dot").classList.add("off");
    $("connection-label").textContent = "事件流重连中";
    clearTimeout(monitorRetry);
    monitorRetry = setTimeout(observe, 2500);
  };
  monitor.onerror = () => monitor.close();
}

const serviceBusy = new Set();
const serviceLabels = {running: "运行中", starting: "加载中", stopped: "已停止", failed: "失败",
  blocked: "端口被占用", external: "外部服务", embedded: "随后台运行", system: "系统语音"};
function renderServices() {
  for (const item of state.services) {
    const container = $(item.kind + "-service");
    if (!container) continue;
    const status = $(item.kind + "-service-status");
    const detail = [serviceLabels[item.state] || item.state];
    if (item.pid) detail.push("PID " + item.pid);
    if (item.active) detail.push(item.active + " 个请求处理中");
    if (item.needs_restart) detail.push("模型已修改，请重启生效");
    if (item.error) detail.push(item.error);
    if (!item.managed) detail.push("选择内置服务并保存后可操作");
    status.textContent = detail.join(" · ");
    status.classList.toggle("error", ["failed", "blocked"].includes(item.state));
    for (const button of container.querySelectorAll("button")) {
      button.disabled = !item.managed || serviceBusy.has(item.kind) ||
        (button.dataset.action === "start" && ["running", "starting"].includes(item.state)) ||
        (button.dataset.action !== "start" && !item.pid && item.state === "stopped");
    }
  }
}
async function refreshServices() {
  state.services = await api("services");
  renderServices();
}
async function controlService(kind, action) {
  serviceBusy.add(kind);
  renderServices();
  try {
    await api("services/" + kind + "/" + action, {method: "POST"});
    await refreshServices();
    notice(action === "stop" ? "服务已停止，模型进程已释放。" : "操作已提交，模型加载状态会自动更新。");
  } catch (e) {
    notice(e.message, true);
    await refreshServices().catch(() => {});
  } finally {
    serviceBusy.delete(kind);
    renderServices();
  }
}
let servicePolling = false;
setInterval(async () => {
  if (state.view !== "models" || document.hidden || servicePolling) return;
  servicePolling = true;
  try { await refreshServices(); } catch (e) { notice(e.message, true); }
  finally { servicePolling = false; }
}, 2000);

function field(label, id, value, type = "text") {
  const l = node("label", "", label);
  const input = node("input");
  input.id = id;
  input.type = type;
  input.value = value;
  input.required = true;
  l.append(input);
  return l;
}
function populateConfig(cfg) {
  state.config = cfg;
  const target = $("provider-fields");
  target.replaceChildren();
  const names = {
    asr: ["语音识别", "SPEECH TO TEXT"],
    llm: ["对话模型", "LANGUAGE MODEL"],
    tts: ["语音合成", "TEXT TO SPEECH"],
  };
  for (const [i, kind] of ["asr", "llm", "tts"].entries()) {
    const c = cfg[kind],
      panel = node("article", "panel"),
      heading = node("div", "provider-heading"),
      title = node("div");
    title.append(
      node("h2", "", names[kind][0]),
      node("small", "", names[kind][1]),
    );
    heading.append(node("span", "provider-number", "0" + (i + 1)), title);
    panel.append(heading);
    const label = node("label", "", "提供商");
    const select = node("select");
    select.id = kind + "-provider";
    for (const [value, text] of kind === "tts"
      ? [
          ["builtin", "内置服务（可启停）"],
          ["macos", "macOS 原生语音"],
          ["http", "兼容 HTTP API"],
        ]
      : [
          ["builtin", "内置服务（可启停）"],
          ["mlx", "Mac 本地 MLX（随后台运行）"],
          ["http", "兼容 HTTP API"],
        ]) {
      const option = node("option", "", text);
      option.value = value;
      select.append(option);
    }
    select.value = c.provider;
    label.append(select);
    panel.append(
      label,
      field("模型 ID", kind + "-model", c.model),
      field("API Base URL", kind + "-url", c.url, "url"),
      field("密钥环境变量", kind + "-key_env", c.key_env),
    );
    if (kind === "asr") {
      const label = node("label", "", "识别语言");
      const select = node("select");
      select.id = "asr-language";
      for (const [value, text] of [
        ["Chinese", "中文"],
        ["English", "英文"],
        ["auto", "自动"],
      ]) {
        const o = node("option", "", text);
        o.value = value;
        select.append(o);
      }
      select.value = c.language;
      label.append(select);
      panel.append(label);
    }
    if (kind === "llm") {
      const f = field(
        "最大回复 Token",
        "llm-max_tokens",
        c.max_tokens,
        "number",
      );
      f.querySelector("input").min = 16;
      f.querySelector("input").max = 2048;
      panel.append(f);
    }
    if (kind === "tts") {
      panel.append(
        field("音色 / Voice ID", "tts-voice", c.voice),
        field("HTTP PCM 采样率", "tts-sample_rate", c.sample_rate, "number"),
      );
    }
    select.onchange = () => {
      if (kind === "tts" && select.value === "builtin" && $("tts-model").value === "tts-1") {
        $("tts-model").value = "mlx-community/Qwen3-TTS-12Hz-0.6B-CustomVoice-8bit";
        $("tts-voice").value = "Serena";
        $("tts-sample_rate").value = 24000;
      }
      for (const key of ["url", "key_env"])
        $(kind + "-" + key).disabled = select.value !== "http";
      if (kind === "tts")
        $("tts-sample_rate").disabled = select.value === "macos";
    };
    const service = node("div", "service-controls");
    service.id = kind + "-service";
    const status = node("p", "service-state", "正在读取服务状态…");
    status.id = kind + "-service-status";
    status.setAttribute("role", "status");
    const actions = node("div", "button-row");
    for (const [action, text] of [["start", "启动"], ["stop", "停止"], ["restart", "重启"]]) {
      const button = node("button", "secondary", text);
      button.type = "button";
      button.dataset.action = action;
      button.setAttribute("aria-label", names[kind][0] + text);
      button.onclick = () => controlService(kind, action);
      actions.append(button);
    }
    service.append(status, actions);
    panel.append(service);
    target.append(panel);
    select.onchange();
  }
  renderServices();
  const mapping = {
    "wake-mode": cfg.wake.mode,
    "wake-phrase": cfg.wake.phrase,
    "idle-seconds": cfg.wake.idle_seconds,
    "vad-model": cfg.audio.vad_model,
    "onset-ms": cfg.audio.onset_ms,
    "silence-ms": cfg.audio.silence_ms,
    "speech-threshold": cfg.audio.speech_threshold,
    "retention-days": cfg.retention_days,
    "system-prompt": cfg.system_prompt,
  };
  for (const [id, value] of Object.entries(mapping)) $(id).value = value;
  $("barge-in").checked = cfg.audio.barge_in;
  renderOverview();
}
$("settings-form").onsubmit = async (event) => {
  event.preventDefault();
  const button = event.submitter;
  button.disabled = true;
  try {
    const cfg = structuredClone(state.config);
    for (const kind of ["asr", "llm", "tts"])
      for (const key of Object.keys(cfg[kind])) {
        const input = $(kind + "-" + key);
        if (input)
          cfg[kind][key] =
            input.type === "number" ? Number(input.value) : input.value;
      }
    cfg.wake = {
      mode: $("wake-mode").value,
      phrase: $("wake-phrase").value,
      idle_seconds: Number($("idle-seconds").value),
    };
    Object.assign(cfg.audio, {
      barge_in: $("barge-in").checked,
      vad_model: $("vad-model").value,
      onset_ms: Number($("onset-ms").value),
      silence_ms: Number($("silence-ms").value),
      speech_threshold: Number($("speech-threshold").value),
    });
    cfg.system_prompt = $("system-prompt").value;
    cfg.retention_days = Number($("retention-days").value);
    const result = await api("config", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(cfg),
    });
    populateConfig(result.config);
    await refreshServices();
    notice("配置已保存。请让客户端重新连接，以使用新配置。");
  } catch (e) {
    notice(e.message, true);
  } finally {
    button.disabled = false;
  }
};
$("login-form").onsubmit = async (event) => {
  event.preventDefault();
  try {
    await api("login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token: $("admin-token").value }),
    });
    $("admin-token").value = "";
    $("login").hidden = true;
    await boot();
  } catch (e) {
    notice(e.message, true);
  }
};

// Reference client: continuous PCM input and an explicitly clearable output queue.
let socket = null,
  context = null,
  stream = null,
  capture = null,
  micSource = null,
  mute = null;
let currentTurn = null,
  nextAudioTime = 0,
  sources = new Set(),
  endTimer,
  pingTimer,
  reconnectTimer;
let manualDisconnect = false,
  retries = 0,
  wantsMic = false,
  labMessages = [],
  partial = "",
  clientReady = false;
let identity;
try {
  identity = JSON.parse(sessionStorage.getItem("voicehub-client") || "null");
} catch {
  identity = null;
}
function send(value) {
  if (socket?.readyState === WebSocket.OPEN && clientReady)
    socket.send(JSON.stringify(value));
}
function setClientReady(ready) {
  clientReady = ready;
  $("send-text").disabled = !ready;
  $("activate-client").disabled = !ready;
  $("stop-client").disabled = !ready;
  $("disconnect-client").disabled = !socket;
}
function clearPlayback() {
  clearTimeout(endTimer);
  for (const source of sources) {
    try {
      source.stop();
    } catch {}
  }
  sources.clear();
  nextAudioTime = context?.currentTime || 0;
  currentTurn = null;
}
async function audioContext() {
  if (!context || context.state === "closed") {
    context = new AudioContext({ sampleRate: 16000 });
    await context.audioWorklet.addModule("/static/capture.js");
  }
  await context.resume();
  return context;
}
async function microphone() {
  await audioContext();
  if (stream) return;
  stream = await navigator.mediaDevices.getUserMedia({
    audio: {
      echoCancellation: true,
      noiseSuppression: true,
      autoGainControl: true,
      channelCount: 1,
    },
    video: false,
  });
  micSource = context.createMediaStreamSource(stream);
  capture = new AudioWorkletNode(context, "voicehub-capture");
  mute = context.createGain();
  mute.gain.value = 0;
  micSource.connect(capture).connect(mute).connect(context.destination);
  capture.port.onmessage = (event) => {
    if (socket?.readyState === WebSocket.OPEN && clientReady) {
      if (socket.bufferedAmount > 64000) {
        notice("上传音频积压，已断开以避免延迟累积。", true);
        disconnect();
        return;
      }
      socket.send(event.data);
    }
  };
}
function stopMicrophone() {
  if (stream) for (const track of stream.getTracks()) track.stop();
  stream = null;
  micSource?.disconnect();
  capture?.disconnect();
  mute?.disconnect();
  capture = null;
  micSource = null;
  mute = null;
}
function phase(name, detail) {
  $("client-phase").textContent = labels[name] || name;
  $("client-detail").textContent =
    detail || "你的声音正在通过统一 WebSocket 协议传输。";
  $("mic-orb").className = "mic-orb " + name;
}
async function loadLabMessages() {
  if (!identity?.session_id) return;
  labMessages = await api("sessions/" + identity.session_id + "/messages");
  renderLab();
}
function renderLab() {
  renderMessages($("lab-messages"), labMessages, identity?.session_id, false);
  if (partial) {
    const n = bubble({
      role: "assistant",
      content: partial,
      status: "streaming",
      created: Date.now() / 1000,
    });
    $("lab-messages").append(n);
    $("lab-messages").scrollTop = $("lab-messages").scrollHeight;
  }
}
async function connect(useMic = false) {
  manualDisconnect = false;
  wantsMic = useMic;
  $("connect-text").disabled = $("connect-mic").disabled = true;
  try {
    if (socket) {
      const old = socket;
      socket = null;
      setClientReady(false);
      old.close();
      await new Promise((resolve) => setTimeout(resolve, 250));
    }
    clearPlayback();
    stopMicrophone();
    await audioContext();
    if (useMic) await microphone();
    openClient();
  } catch (e) {
    stopMicrophone();
    $("connect-text").disabled = $("connect-mic").disabled = false;
    notice("无法开启客户端：" + e.message, true);
  }
}
function openClient() {
  clearTimeout(reconnectTimer);
  const ws = (socket = new WebSocket(
    (location.protocol === "https:" ? "wss://" : "ws://") +
      location.host +
      "/ws",
  ));
  setClientReady(false);
  $("disconnect-client").disabled = false;
  $("client-status").textContent = retries ? "正在重连" : "正在连接";
  ws.onopen = () => {
    if (socket !== ws) return;
    const settings = stream?.getAudioTracks()[0]?.getSettings() || {};
    const hello = {
      type: "hello",
      version: 1,
      device_id: "web-" + (identity?.device || crypto.randomUUID().slice(0, 8)),
      wake_mode: $("client-wake").value,
      audio: { format: "pcm_s16le", sample_rate: 16000 },
      capabilities: {
        echo_cancellation: !!settings.echoCancellation,
        continuous_audio: wantsMic,
      },
    };
    if (identity?.session_id) {
      hello.session_id = identity.session_id;
      hello.resume_token = identity.resume_token;
    }
    ws.send(JSON.stringify(hello));
    clearInterval(pingTimer);
    pingTimer = setInterval(() => send({ type: "ping" }), 15000);
  };
  ws.onmessage = (event) => {
    void (async () => {
      if (socket !== ws) return;
      const e = JSON.parse(event.data);
      if (e.type === "hello") {
        $("notice").hidden = true;
        retries = 0;
        identity = {
          device: identity?.device || crypto.randomUUID().slice(0, 8),
          session_id: e.session_id,
          resume_token: e.resume_token,
        };
        sessionStorage.setItem("voicehub-client", JSON.stringify(identity));
        setClientReady(true);
        $("connect-text").disabled = $("connect-mic").disabled = false;
        $("client-status").textContent =
          "已连接" + (wantsMic ? " · 麦克风开启" : "");
        $("lab-session").textContent = e.session_id.slice(0, 12);
        await loadLabMessages();
        if (e.wake_mode !== "backend") send({ type: "session.activate" });
        else phase("standby", "持续上传已开启，等待唤醒词。");
        if (wantsMic && !e.capabilities.barge_in)
          notice("客户端未声明有效回声消除，自然插话未开启；仍可点击打断。");
        return;
      }
      if (e.type === "error") {
        notice(
          (e.code || "error") + "：" + (e.message || "请查看事件日志"),
          true,
        );
        if (e.code === "invalid_resume") {
          identity = null;
          sessionStorage.removeItem("voicehub-client");
        }
        return;
      }
      if (e.type === "session.state")
        phase(
          e.state,
          e.state === "standby" ? "等待唤醒或手动激活。" : undefined,
        );
      if (e.type === "reply.delta") {
        partial += e.text;
        renderLab();
      }
      if (e.type === "message.user") {
        partial = "";
        await loadLabMessages();
      }
      if (e.type === "audio.start") {
        clearPlayback();
        currentTurn = e.turn_id;
      }
      if (e.type === "audio.chunk" && e.turn_id === currentTurn) {
        const binary = atob(e.data);
        const array = new Uint8Array(binary.length);
        for (let i = 0; i < binary.length; i++) array[i] = binary.charCodeAt(i);
        const pcm = new DataView(array.buffer),
          buffer = context.createBuffer(1, array.length / 2, 16000),
          samples = buffer.getChannelData(0);
        for (let i = 0; i < samples.length; i++)
          samples[i] = pcm.getInt16(i * 2, true) / 32768;
        if (nextAudioTime - context.currentTime > 0.5) {
          notice("播放队列超过 500ms，已请求停止。", true);
          clearPlayback();
          send({ type: "interrupt" });
          return;
        }
        const source = context.createBufferSource();
        source.buffer = buffer;
        source.connect(context.destination);
        nextAudioTime = Math.max(nextAudioTime, context.currentTime + 0.015);
        source.start(nextAudioTime);
        nextAudioTime += buffer.duration;
        sources.add(source);
        source.onended = () => sources.delete(source);
      }
      if (e.type === "audio.end" && e.turn_id === currentTurn) {
        const tid = e.turn_id;
        clearTimeout(endTimer);
        endTimer = setTimeout(
          () => {
            if (tid === currentTurn)
              send({ type: "playback.done", turn_id: tid });
          },
          Math.max(0, (nextAudioTime - context.currentTime) * 1000) + 60,
        );
      }
      if (e.type === "playback.clear") {
        clearPlayback();
        partial = "";
        await loadLabMessages();
      }
      if (e.type === "turn.completed") {
        partial = "";
        await loadLabMessages();
      }
    })().catch((e) => notice("客户端同步失败：" + e.message, true));
  };
  ws.onclose = () => {
    if (socket !== ws) return;
    socket = null;
    setClientReady(false);
    clearInterval(pingTimer);
    clearPlayback();
    partial = "";
    $("client-status").textContent = "已断开";
    $("connect-text").disabled = $("connect-mic").disabled = false;
    if (!manualDisconnect && retries < 8) {
      retries++;
      phase("重新连接中", "网络中断，正在恢复会话。旧音频不会重播。");
      reconnectTimer = setTimeout(
        openClient,
        Math.min(10000, 500 * 2 ** retries),
      );
    } else {
      stopMicrophone();
      phase("连接已结束", "历史记录已保留。");
    }
  };
  ws.onerror = () => ws.close();
}
function disconnect() {
  manualDisconnect = true;
  clearTimeout(reconnectTimer);
  clearInterval(pingTimer);
  clearPlayback();
  stopMicrophone();
  socket?.close();
  setClientReady(false);
}
$("connect-text").onclick = () => connect(false);
$("connect-mic").onclick = () => connect(true);
$("disconnect-client").onclick = disconnect;
$("activate-client").onclick = () => send({ type: "session.activate" });
$("stop-client").onclick = () => {
  clearPlayback();
  send({ type: "interrupt" });
};
$("text-form").onsubmit = (event) => {
  event.preventDefault();
  const text = $("client-text").value.trim();
  if (text && clientReady) {
    send({ type: "input.text", text });
    $("client-text").value = "";
  }
};
window.addEventListener("pagehide", () => {
  manualDisconnect = true;
  disconnect();
  monitor?.close();
  clearTimeout(monitorRetry);
});
async function boot() {
  try {
    populateConfig((await api("config")).config);
    await refresh();
    await refreshServices();
    observe();
  } catch (e) {
    notice(e.message, true);
  }
}
await boot();
