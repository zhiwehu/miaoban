import {createMcpUi} from './mcp-ui.js';

const $ = (id) => document.getElementById(id);
const state = {
  view: "overview",
  config: null,
  logSession: null,
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
  recording: "录音中",
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
  "recording.started": "开始录音",
  "recording.confirming": "等待停止确认",
  "recording.confirm_expired": "停止确认超时",
  "recording.stopped": "录音已保存",
  "recording.transcript": "实时转写",
  "recording.transcription_unavailable": "实时转写不可用",
  "recording.speaker_unavailable": "发言人区分不可用",
  "recording.diarized": "发言人重分析完成",
  "recording.minutes_started": "正在自动生成会议纪要",
  "recording.input_ignored": "录音中忽略文字",
  "tool.started": "工具查询中",
  "tool.completed": "工具查询完成",
  "tool.failed": "工具查询失败",
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
const mcp = createMcpUi({api,node,notice,onConfig:()=>{}});
function view(name) {
  if(name === 'skills') name = 'mcp';
  if (!["overview", "conversations", "connections", "models", "mcp", "logs", "recordings", "lab"].includes(name))
    name = "overview";
  state.view = name;
  document.body.dataset.view = name;
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
    mcp: "MCP 工具",
    connections: "客户端接入",
    logs: "事件日志",
    recordings: "录音",
    lab: "网页聊天",
  }[name];
  if (location.hash !== "#" + name) history.replaceState(null, "", "#" + name);
  window.dispatchEvent(new CustomEvent("voicehub:view", {detail:name}));
}
document
  .querySelectorAll("[data-view]")
  .forEach((el) => (el.onclick = () => view(el.dataset.view)));
$("go-lab").onclick = () => view("lab");
$("go-logs").onclick = () => view("logs");
$("mcp-settings").onclick = () => {
  $('mcp-common-settings').scrollIntoView({behavior:'smooth',block:'start'});
};
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
      cfg.llm.provider === "builtin" ? "内置服务" : "HTTP API";
    $("engine-model").textContent = cfg.llm.model;
  }
  const devices = $("devices");
  devices.replaceChildren();
  if (!s.sessions.length)
    empty(
      devices,
      "暂时没有在线设备",
      "前往网页聊天，验证你的第一个客户端。",
    );
  for (const device of s.sessions) {
    const row = node("div", "device-row");
    const text = node("div");
    text.append(
      node("strong", "", device.device_id),
      node("small", "", device.id.slice(0, 12) + " · " + (device.protocol || "未知协议") + " · " + device.wake_mode),
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
  const rendered = new Set();
  const appendCalls = tid => {
    if (!tid || rendered.has(tid)) return;
    target.append(...mcp.renderCalls(sid,tid));
    rendered.add(tid);
  };
  for (const m of messages) {
    if (m.role === 'assistant') appendCalls(m.turn_id);
    target.append(bubble(m));
    if (m.role === 'user') appendCalls(m.turn_id);
  }
  for (const [key, value] of state.live) {
    if (
      includeLive &&
      value.sid === sid &&
      !messages.some((m) => m.turn_id === key && m.role === "assistant")
    ) {
      appendCalls(key);
      target.append(
        bubble({
          role: "assistant",
          content: value.text,
          status: "streaming",
          created: value.created,
        }),
      );
    }
  }
  if (!target.children.length)
    empty(target, "对话已经准备好", "发送一句话，或开启麦克风。");
  if (scrollMode === "prepend") target.scrollTop = previousTop + target.scrollHeight - previousHeight;
  else if (scrollMode === "end" || follow) target.scrollTop = target.scrollHeight;
  else target.scrollTop = previousTop;
}
async function selectSession(sid, preserveScroll = false) {
  state.selected = sid;
  const messages = await api("sessions/" + sid + "/messages");
  await mcp.loadCalls(sid,messages);
  if (state.selected !== sid) return;
  state.messages = messages;
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
    await mcp.loadCalls(state.selected,older);
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
      .map(([k, v]) => k + ": " + (typeof v === 'object' ? JSON.stringify(v) : String(v)))
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
    const older = await api("logs?before=" + last.id + (state.logSession ? "&session_id=" + encodeURIComponent(state.logSession) : ""));
    state.logs.push(...older);
    renderLogs();
    $("more-logs").hidden = older.length < 100;
  } catch (e) {
    notice(e.message, true);
  }
};
window.addEventListener("voicehub:open-session", async (event) => {
  try {
    const {sid, target} = event.detail;
    if (target === "logs") {
      state.logSession = sid;
      $("log-session-scope").textContent = "当前会话：" + sid;
      $("log-session-scope").hidden = $("clear-log-session").hidden = false;
      $("log-filter").value = "all";
      view("logs");
      await refresh();
    } else {
      view("conversations");
      await selectSession(sid);
    }
  } catch (e) { notice(e.message, true); }
});
$("clear-log-session").onclick = () => {
  state.logSession = null;
  $("log-session-scope").hidden = $("clear-log-session").hidden = true;
  refresh().catch(e => notice(e.message, true));
};
let refreshTimer;
async function refresh() {
  const [status, sessions, logs] = await Promise.all([
    api("status"),
    api("sessions"),
    api("logs" + (state.logSession ? "?session_id=" + encodeURIComponent(state.logSession) : "")),
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
    loadRecordings().catch(() => {});
  };
  monitor.onmessage = (event) => {
    const e = JSON.parse(event.data);
    mcp.track(e);
    if (e.type.startsWith('tool.')) {
      if (state.selected === e.session_id) renderMessages($('messages'),state.messages,state.selected);
      if (identity?.session_id === e.session_id) renderLab();
    }
    if (e.type.startsWith("recording.")) {
      if (e.type === "recording.transcript") applyTranscriptEvent(e);
      else if (e.type === "recording.transcription_unavailable")
        applyTranscriptionUnavailable(e);
      else if (e.type === "recording.speaker_unavailable")
        notice(e.message || "发言人区分暂时不可用。", true);
      else if (e.type === "recording.diarized" &&
               displayedRecording?.id === e.recording_id) {
        loadRecordings().catch(() => {});
        loadDisplayedTranscript(displayedRecording).catch(() => {});
        notice("发言人重分析完成，逐字稿已更新。");
      } else if (e.type === "recording.minutes_started") {
        notice("逐字稿已更新，正在自动生成默认会议纪要。");
      }
      else if (e.type === "recording.confirming" &&
               displayedRecording?.id === e.recording?.id) {
        displayedConfirming = true;
        renderRecordingDetail();
        renderTranscript();
      } else if (e.type === "recording.confirm_expired" &&
                 displayedRecording?.id === e.recording?.id) {
        displayedConfirming = false;
        renderRecordingDetail();
        renderTranscript();
      } else if (e.type === "recording.started") {
        handleRecordingStarted(e.recording).catch(() => {});
        loadRecordings().catch(() => {});
      } else if (e.type === "recording.stopped") {
        handleRecordingStopped(e.recording).catch(() => {});
        loadRecordings().catch(() => {});
      } else if (e.type === "recording.deleted") {
        handleRecordingDeleted(e.recording_id);
        loadRecordings().catch(() => {});
      }
      return;
    }
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
      if (e.type === "session.disconnected")
        setTimeout(() => loadRecordings().catch(() => {}), 300);
    }
    scheduleRefresh();
  };
  monitor.onclose = () => {
    mcp.unconfirm();
    if (state.selected) renderMessages($('messages'),state.messages,state.selected);
    renderLab();
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
    delete cfg.tools;
    const result = await api("config/models", {
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
let activeWakeMode = "manual", activeWakePhrase = "";
let recordings = [], displayedRecording = null;
let displayedTranscript = [], displayedTranscriptState = "idle";
let minutesTemplates = null, minutesJobs = [], minutesJob = null;
let minutesPollTimer = null, minutesModelId = "", minutesRecordingId = null;
let speechMap = null;
let allowRecordingPlayback = false;
let displayedConfirming = false, transcriptWarning = "", transcriptFetchToken = 0;
let recordingsRefreshTimer = null;
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
  syncRecordingControls();
}
function formatClock(milliseconds) {
  const total = Math.max(0, Math.floor((milliseconds || 0) / 1000));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const seconds = total % 60;
  const clock = hours
    ? hours + ":" + String(minutes).padStart(2, "0")
    : String(minutes);
  return clock + ":" + String(seconds).padStart(2, "0");
}
function transcriptStamp(milliseconds) {
  const total = Math.max(0, Math.floor((milliseconds || 0) / 1000));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const seconds = total % 60;
  return hours
    ? hours + ":" + String(minutes).padStart(2, "0") + ":" +
      String(seconds).padStart(2, "0")
    : String(minutes).padStart(2, "0") + ":" + String(seconds).padStart(2, "0");
}
function formatBytes(bytes) {
  if (!bytes) return "0 KB";
  if (bytes < 1024 * 1024) return Math.max(1, Math.round(bytes / 1024)) + " KB";
  return (bytes / 1024 / 1024).toFixed(1) + " MB";
}
function recordingReason(reason) {
  return {
    web: "网页停止",
    voice: "语音确认停止",
    client: "客户端按键",
    disconnect: "连接断开",
    limit: "达到时长上限",
    storage_error: "写入失败",
    server_shutdown: "服务关闭",
    server_restart: "异常重启恢复",
  }[reason] || reason || "未知";
}
let currentLineIndex = -1;
let seekDragging = false;

function activeRecordings() {
  return recordings.filter((item) => item.status === "active");
}
function pageRecording() {
  return activeRecordings().find(
    (item) => item.session_id === identity?.session_id,
  ) || null;
}
function recordingOrigin(recording) {
  if (!recording) return "unknown";
  if (recording.session_id === identity?.session_id) return "web";
  return recording.device_id?.startsWith("web-") ? "web" : "device";
}
function recordingSource(recording) {
  if (!recording) return "—";
  if (recording.session_id === identity?.session_id) return "网页麦克风";
  if (recording.device_id?.startsWith("web-")) return "网页麦克风";
  return recording.device_id ? "喵伴 " + recording.device_id : "未知设备";
}
function detailDate(seconds) {
  if (!seconds) return "—";
  return new Date(seconds * 1000).toLocaleString("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}
function syncRecordingControls() {
  const recording = !!pageRecording();
  $("send-text").disabled = !clientReady || recording;
  $("client-text").disabled = recording;
  $("activate-client").disabled = !clientReady || recording;
  $("stop-client").disabled = !clientReady || recording;
  $("disconnect-client").disabled = !socket;
  $("lab-recording-hint").hidden = !recording;
  renderRecordingDetail();
}
function resetAudio() {
  const audio = $("recording-audio");
  audio.pause();
  audio.removeAttribute("src");
  audio.load();
  $("recording-seek").max = 0;
  $("recording-seek").value = 0;
  $("recording-current").textContent = "00:00";
  $("recording-total").textContent = "00:00";
  $("recording-play").textContent = "▶";
  currentLineIndex = -1;
}
function recordingAudioUrl(recording) {
  if (!recording || recording.status === "active") return "";
  return recording.ready_audio_url || recording.download_url || "";
}
function fullToReady(ms) {
  const segments = speechMap?.segments || [];
  if (!segments.length || !Number.isFinite(ms)) return ms;
  for (const segment of segments) {
    if (ms < segment.full_start_ms) return segment.ready_start_ms;
    if (ms <= segment.full_end_ms)
      return segment.ready_start_ms + (ms-segment.full_start_ms);
  }
  return speechMap.ready_duration_ms || ms;
}
function readyToFull(ms) {
  const segments = speechMap?.segments || [];
  if (!segments.length || !Number.isFinite(ms)) return ms;
  for (const segment of segments) {
    if (ms < segment.ready_start_ms) return segment.full_start_ms;
    if (ms <= segment.ready_end_ms)
      return segment.full_start_ms + (ms-segment.ready_start_ms);
  }
  return speechMap.full_duration_ms || ms;
}
function syncAudio(recording) {
  const audio = $("recording-audio");
  const active = recording?.status === "active";
  const url = recordingAudioUrl(recording);
  const key = recording
    ? recording.id + ":" + recording.status + ":" +
      (recording.ready_audio_url ? "ready" : "raw")
    : "";
  if (audio.dataset.key !== key) {
    audio.dataset.key = key;
    resetAudio();
    if (url) {
      audio.src = url;
      audio.load();
    }
  }
  const player = $("recording-player");
  const placeholder = $("recording-player-placeholder");
  if (!recording) {
    player.hidden = true;
    placeholder.hidden = true;
    return;
  }
  if (active) {
    player.hidden = true;
    placeholder.hidden = false;
    placeholder.textContent = "录音仍在进行，停止后可以播放、下载和跳转文字。";
  } else if (url) {
    player.hidden = false;
    placeholder.hidden = true;
  } else {
    player.hidden = true;
    placeholder.hidden = false;
    placeholder.textContent = "音频文件暂不可用。";
  }
}
function renderRecordingDetail() {
  const recording = displayedRecording;
  const empty = $("recording-empty");
  const detail = $("recording-detail");
  if (!recording) {
    empty.hidden = false;
    detail.hidden = true;
    resetMinutes();
    syncAudio(null);
    return;
  }
  empty.hidden = true;
  detail.hidden = false;
  const active = recording.status === "active";
  const isWeb = recordingOrigin(recording) === "web";
  const lines = displayedTranscript.length || recording.transcript_lines || 0;
  $("recording-detail-date").textContent = detailDate(recording.started_at);
  $("recording-detail-title").textContent = active
    ? isWeb ? "网页录音" : "喵伴录音"
    : "录音详情";
  const meta = [
    recordingSource(recording),
    formatClock(recording.duration_ms),
    lines + " 行",
    recording.speaker_count ? recording.speaker_count + " 位发言人" : "",
    formatBytes(recording.bytes || 0),
    recording.reason ? recordingReason(recording.reason) : "",
  ].filter(Boolean);
  $("recording-detail-meta").textContent = meta.join(" · ");
  const state = $("recording-detail-state");
  state.textContent = active
    ? displayedConfirming
      ? "等待停止确认"
      : isWeb ? "网页录音中" : "喵伴录音中"
    : "已保存";
  state.classList.toggle("active", active);
  $("recording-stop").hidden = !active;
  $("recording-stop").disabled = !active;
  const hasText = displayedTranscript.length > 0;
  $("recording-export").disabled = !hasText;
  $("recording-minutes-button").disabled = active;
  const download = $("recording-download");
  const downloadable = !active && !!recording.download_url;
  download.hidden = !downloadable;
  const when = new Date(recording.started_at * 1000);
  const stamp =
    when.getFullYear() +
    String(when.getMonth() + 1).padStart(2, "0") +
    String(when.getDate()).padStart(2, "0") + "-" +
    String(when.getHours()).padStart(2, "0") +
    String(when.getMinutes()).padStart(2, "0");
  if (downloadable) {
    download.href = recording.download_url;
    download.download =
      "voicehub-" + stamp + "-" + recording.id.slice(0, 8) + "-原始.wav";
  }
  const ready = !active && !!recording.ready_audio_url;
  const readyLink = $("recording-ready-download");
  readyLink.hidden = !ready;
  if (ready) {
    readyLink.href = recording.ready_audio_url;
    readyLink.download =
      "voicehub-" + stamp + "-" + recording.id.slice(0, 8) + "-精简.wav";
  }
  $("recording-action-hint").textContent = active
    ? "录音结束后可以播放、下载和点击文字跳转；当前文字可以先行导出。"
    : "播放和“下载精简音频”默认使用已去掉空白、降噪的版本；“下载原始音频”保留完整原始文件。会议纪要由本机离线生成。";
  syncAudio(recording);
}
function lineSeconds(line) {
  if (Number.isFinite(line?.elapsed_ms)) return line.elapsed_ms / 1000;
  if (line?.stamp) {
    const parts = line.stamp.split(":").map(Number);
    if (parts.length === 2 && parts.every(Number.isFinite))
      return parts[0] * 60 + parts[1];
    if (parts.length === 3 && parts.every(Number.isFinite))
      return parts[0] * 3600 + parts[1] * 60 + parts[2];
  }
  return null;
}
function highlightAt(seconds, {scroll = false, force = false} = {}) {
  const target = $("transcript-lines");
  const rows = [...target.querySelectorAll(".transcript-line")];
  let index = -1;
  displayedTranscript.forEach((line, position) => {
    const start = lineSeconds(line);
    if (start !== null && start <= seconds + 0.08) index = position;
  });
  if (index === currentLineIndex && !force) return;
  currentLineIndex = index;
  for (const row of rows) row.classList.remove("current");
  if (index < 0) return;
  const row = rows[index];
  if (!row) return;
  row.classList.add("current");
  if (scroll) {
    const box = target.getBoundingClientRect();
    const rect = row.getBoundingClientRect();
    if (rect.top < box.top || rect.bottom > box.bottom)
      row.scrollIntoView({block: index === 0 ? "start" : "center",
                          behavior: "smooth"});
  }
}
function transcriptLine(value) {
  if (typeof value === "string")
    return {stamp: "", text: value, elapsed_ms: null};
  const stamp = String(value?.stamp || "");
  let elapsed = Number.isFinite(value?.elapsed_ms)
    ? value.elapsed_ms
    : null;
  if (elapsed === null && stamp) {
    const seconds = lineSeconds({stamp});
    if (seconds !== null) elapsed = seconds * 1000;
  }
  const speaker = value?.speaker ? String(value.speaker) : null;
  const speakerId = Number.isFinite(value?.speaker_id) ? value.speaker_id : null;
  return {stamp, text: String(value?.text || ""), elapsed_ms: elapsed,
          speaker, speaker_id: speakerId};
}
function renderTranscript() {
  const target = $("transcript-lines");
  const stick =
    target.scrollHeight - target.scrollTop - target.clientHeight < 80;
  const previousTop = target.scrollTop;
  target.replaceChildren();
  currentLineIndex = -1;
  let status = "等待录音";
  let hint = "时间戳是录音内时间；停顿后自动换行。转写只使用本机 ASR。";
  if (!displayedRecording) {
    empty(target, "还没有文字", "选择一条录音或开始录音后，这里会显示文字。");
  } else if (displayedTranscript.length) {
    displayedTranscript.forEach((value, index) => {
      const line = transcriptLine(value);
      const row = node("div", "transcript-line");
      row.dataset.index = index;
      if (line.stamp) row.append(node("time", "", line.stamp));
      if (line.speaker) {
        const speaker = node("span", "speaker", line.speaker);
        if (line.speaker_id) speaker.dataset.speaker = line.speaker_id;
        row.append(speaker);
      }
      row.append(node("span", "", line.text));
      if (displayedRecording.status === "active" || lineSeconds(line) === null) {
        row.classList.add("unseekable");
      } else {
        row.onclick = () => {
          const seconds = lineSeconds(line);
          const audio = $("recording-audio");
          if (seconds === null || !audio.getAttribute("src")) return;
          const readySeconds = fullToReady(seconds*1000)/1000;
          audio.currentTime = readySeconds;
          $("recording-seek").value = readySeconds;
          $("recording-current").textContent = formatClock(readySeconds * 1000);
          highlightAt(seconds, {scroll: true, force: true});
          playRecordingAudio();
        };
      }
      target.append(row);
    });
    target.scrollTop = stick ? target.scrollHeight : previousTop;
    if (displayedRecording.status === "active") {
      status = "录音中 · 实时粗分";
      hint = "录音中的说话人标签是实时粗分；停止后会自动按整段录音重新分析，最终结果以停止后为准。";
    } else {
      status = "已保存";
      hint = "点击一行跳到对应录音位置；拖动进度条会同步高亮当前段落。";
    }
  } else if (transcriptWarning) {
    empty(target, "没有转写", transcriptWarning);
    status = "无转写";
    hint = transcriptWarning;
  } else if (displayedTranscriptState === "loading") {
    empty(target, "正在读取转写…", "稍等，正在从本机读取文字。");
    status = "读取中";
  } else if (displayedRecording.status === "active") {
    empty(target, "正在听…", "停顿后会实时出现一行文字。");
    status = "录音中 · 等待第一段";
  } else {
    empty(target, "没有转写", "这条录音没有可用文字（旧录音或 ASR 不可用）。");
    status = "无转写";
  }
  if (displayedConfirming)
    hint = "等待确认停止：请说“确认停止”，或点停止按钮。";
  if (displayedTranscript.length && (displayedRecording?.speaker_count || 0) > 0)
    hint += " 说话人由本机声纹自动分组，可能受短句或重叠说话影响。";
  $("transcript-status").textContent = status;
  $("transcript-hint").textContent = hint;
  const audio = $("recording-audio");
  if (displayedRecording?.status !== "active") {
    const atStart = !audio.getAttribute("src") || audio.currentTime < .5;
    if (atStart) target.scrollTop = 0;
    if (audio.getAttribute("src"))
      highlightAt(readyToFull(audio.currentTime*1000)/1000,
                  {scroll: !atStart, force: true});
  }
}
async function loadDisplayedTranscript(recording) {
  const token = ++transcriptFetchToken;
  try {
    const data = await api("recordings/" + recording.id + "/transcript");
    if (token !== transcriptFetchToken) return;
    if (displayedRecording?.id !== recording.id) return;
    speechMap = data.speech_map || null;
    const lines = Array.isArray(data.lines)
      ? data.lines.map(transcriptLine)
      : (data.text || "").split("\n").filter(Boolean).map(transcriptLine);
    if (data.available && lines.length >= displayedTranscript.length)
      displayedTranscript = lines;
    if (data.available && lines.length) {
      displayedTranscriptState = data.status === "active" ? "active" : "stopped";
      transcriptWarning = "";
      displayedRecording.transcription_error = null;
    } else if (!data.available && data.status !== "active" &&
               !displayedTranscript.length) {
      displayedTranscriptState = "unavailable";
      transcriptWarning = "这条录音没有可用的转写（旧录音或 ASR 不可用）。";
    } else if (!displayedTranscript.length) {
      displayedTranscriptState = data.status === "active" ? "active" : "stopped";
      if (recording.transcription_error) {
        transcriptWarning = recording.transcription_error;
        displayedTranscriptState = "unavailable";
      }
    }
    renderTranscript();
    renderRecordingDetail();
  } catch (error) {
    if (token !== transcriptFetchToken) return;
    if (!displayedTranscript.length) {
      displayedTranscriptState = "unavailable";
      transcriptWarning = "读取转写失败：" + error.message;
      renderTranscript();
    }
  }
}
async function selectDisplayedRecording(recording, {load = true} = {}) {
  if (!recording) return;
  if (displayedRecording?.id === recording.id) {
    displayedRecording = {...displayedRecording, ...recording};
    renderRecordingDetail();
    renderRecordings();
    if (load && !displayedTranscript.length)
      await loadDisplayedTranscript(recording);
    return;
  }
  displayedRecording = recording;
  displayedTranscript = [];
  displayedTranscriptState = recording.status === "active" ? "active" : "stopped";
  displayedConfirming = false;
  transcriptWarning = recording.transcription_error || "";
  transcriptFetchToken++;
  currentLineIndex = -1;
  speechMap = null;
  resetMinutes();
  renderRecordings();
  renderRecordingDetail();
  renderTranscript();
  if (load) await loadDisplayedTranscript(recording);
}
function applyTranscriptEvent(event) {
  if (!displayedRecording || displayedRecording.id !== event.recording_id)
    return;
  if (event.merged) {
    const index = (event.line || 0) - 1;
    if (index < 0 || index >= displayedTranscript.length) {
      loadDisplayedTranscript(displayedRecording).catch(() => {});
      return;
    }
    displayedTranscript[index] = {
      stamp: event.stamp || displayedTranscript[index].stamp,
      text: event.text,
      elapsed_ms: displayedTranscript[index].elapsed_ms,
      speaker: displayedTranscript[index].speaker,
      speaker_id: displayedTranscript[index].speaker_id,
    };
    displayedTranscriptState = "active";
    transcriptWarning = "";
    displayedRecording.transcription_error = null;
    renderTranscript();
    renderRecordingDetail();
    return;
  }
  if (event.line <= displayedTranscript.length) return;
  if (event.line > displayedTranscript.length + 1) {
    loadDisplayedTranscript(displayedRecording).catch(() => {});
    return;
  }
  displayedTranscript.push({
    stamp: event.stamp || "",
    text: event.text,
    elapsed_ms: typeof event.elapsed_ms === "number" ? event.elapsed_ms : null,
    speaker: event.speaker || null,
    speaker_id: Number.isFinite(event.speaker_id) ? event.speaker_id : null,
  });
  if (displayedRecording && Number.isFinite(event.speaker_id))
    displayedRecording.speaker_count = Math.max(
      displayedRecording.speaker_count || 0, event.speaker_id);
  displayedTranscriptState = "active";
  transcriptWarning = "";
  displayedRecording.transcription_error = null;
  renderTranscript();
  renderRecordingDetail();
}
function applyTranscriptionUnavailable(event) {
  if (!displayedRecording || displayedRecording.id !== event.recording_id)
    return;
  transcriptWarning = event.message || "实时转写暂时不可用。";
  displayedRecording.transcription_error = transcriptWarning;
  if (!displayedTranscript.length) displayedTranscriptState = "unavailable";
  renderTranscript();
}
async function handleRecordingStarted(recording) {
  if (!displayedRecording || displayedRecording.status !== "active") {
    await selectDisplayedRecording(recording);
    return;
  }
  if (recording.session_id === identity?.session_id &&
      displayedRecording.session_id !== identity?.session_id) {
    await selectDisplayedRecording(recording);
    return;
  }
  if (displayedRecording.id === recording.id) {
    displayedRecording = {...displayedRecording, ...recording};
    renderRecordingDetail();
    renderRecordings();
  }
}
async function handleRecordingStopped(recording) {
  if (!displayedRecording || displayedRecording.id !== recording.id) return;
  if (displayedRecording.status === "stopped" &&
      displayedTranscriptState === "stopped")
    return;
  displayedRecording = {...displayedRecording, ...recording};
  displayedConfirming = false;
  renderRecordingDetail();
  renderRecordings();
  await loadDisplayedTranscript(displayedRecording);
  notice(
    "录音已保存，共 " +
      formatClock(recording.duration_ms) +
      "。音频和文字已可查看。",
  );
}
function handleRecordingDeleted(recordingId) {
  if (displayedRecording?.id !== recordingId) return;
  displayedRecording = null;
  displayedTranscript = [];
  displayedTranscriptState = "idle";
  displayedConfirming = false;
  transcriptWarning = "";
  transcriptFetchToken++;
  currentLineIndex = -1;
  $("recording-audio").dataset.key = "";
  resetAudio();
  renderRecordingDetail();
  renderTranscript();
  renderRecordings();
}
function scheduleRecordingsRefresh() {
  clearTimeout(recordingsRefreshTimer);
  recordingsRefreshTimer = activeRecordings().length
    ? setTimeout(() => loadRecordings().catch(() => {}), 5000)
    : null;
}
function renderRecordings() {
  const target = $("recordings-list");
  if (!target) return;
  $("recordings-count").textContent = recordings.length + " 个";
  target.replaceChildren();
  if (!recordings.length) {
    empty(
      target,
      "还没有录音",
      "在网页客户端或喵伴说“开始录音模式”。",
    );
    scheduleRecordingsRefresh();
    return;
  }
  for (const recording of recordings.slice(0, 60)) {
    const active = recording.status === "active";
    const item = node(
      "button",
      "history-item" +
        (displayedRecording?.id === recording.id ? " selected" : ""),
    );
    item.type = "button";
    const top = node("span", "history-top");
    top.append(node("strong", "", dateTime(recording.started_at)));
    top.append(node("span", "history-badge" + (active ? " active" : ""),
                    active ? "录音中" : "已保存"));
    const sub = node(
      "span",
      "history-sub",
      [
        recordingOrigin(recording) === "web" ? "网页" : "喵伴",
        formatClock(recording.duration_ms),
        (recording.transcript_lines || 0) + " 行",
        recording.speaker_count ? recording.speaker_count + " 人" : "",
      ].filter(Boolean).join(" · "),
    );
    item.append(top, sub);
    item.onclick = () =>
      selectDisplayedRecording(recording).catch((error) =>
        notice("读取录音失败：" + error.message, true));
    target.append(item);
  }
  scheduleRecordingsRefresh();
}
async function loadRecordings() {
  const data = await api("recordings");
  recordings = data.recordings || [];
  if (displayedRecording) {
    const fresh = recordings.find((item) => item.id === displayedRecording.id);
    if (fresh) {
      displayedRecording = fresh;
      if (fresh.status !== "active" && displayedTranscriptState === "active")
        await loadDisplayedTranscript(fresh);
    } else {
      handleRecordingDeleted(displayedRecording.id);
    }
  }
  if (!displayedRecording) {
    const preferred =
      pageRecording() || activeRecordings()[0] || recordings[0] || null;
    if (preferred) await selectDisplayedRecording(preferred);
  }
  renderRecordings();
  renderRecordingDetail();
  renderTranscript();
  syncRecordingControls();
  scheduleRecordingsRefresh();
}
async function displayStoppedRecording(recording) {
  if (!displayedRecording || displayedRecording.id !== recording.id) {
    await selectDisplayedRecording(recording);
    return;
  }
  if (displayedRecording.status === "stopped" &&
      displayedTranscriptState === "stopped")
    return;
  displayedRecording = {...displayedRecording, ...recording};
  displayedConfirming = false;
  renderRecordingDetail();
  renderRecordings();
  await loadDisplayedTranscript(displayedRecording);
}
async function stopDisplayedRecording() {
  const recording = displayedRecording;
  if (!recording || recording.status !== "active") return;
  $("recording-stop").disabled = true;
  try {
    const data = await api("recordings/" + recording.id + "/stop", {
      method: "POST",
    });
    await displayStoppedRecording(data.recording);
    await loadRecordings();
  } catch (error) {
    notice("停止录音失败：" + error.message, true);
    renderRecordingDetail();
  }
}
async function stopRecordingFromList(recording) {
  if (displayedRecording?.id !== recording.id)
    await selectDisplayedRecording(recording, {load: false});
  await stopDisplayedRecording();
}
async function exportTranscript() {
  if (!displayedRecording) return;
  try {
    const data = await api("recordings/" + displayedRecording.id + "/transcript");
    if (!data.available || !data.text) {
      notice("还没有可导出的文字。", true);
      return;
    }
    const blob = new Blob([data.text + "\n"], {type: "text/plain;charset=utf-8"});
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    const when = new Date(displayedRecording.started_at * 1000);
    const stamp =
      when.getFullYear() +
      String(when.getMonth() + 1).padStart(2, "0") +
      String(when.getDate()).padStart(2, "0") + "-" +
      String(when.getHours()).padStart(2, "0") +
      String(when.getMinutes()).padStart(2, "0");
    link.href = url;
    link.download =
      "voicehub-" + stamp + "-" + displayedRecording.id.slice(0, 8) + ".txt";
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  } catch (error) {
    notice("导出文字失败：" + error.message, true);
  }
}
function minutesStatusLabel(status) {
  return {
    queued: "排队中",
    running: "生成中",
    completed: "已完成",
    failed: "生成失败",
    cancelled: "已取消",
    interrupted: "已中断",
  }[status] || status || "未知";
}
function formatMinutesTime(seconds) {
  if (!seconds) return "—";
  return new Date(seconds * 1000).toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}
function stopMinutesPolling() {
  if (minutesPollTimer) clearTimeout(minutesPollTimer);
  minutesPollTimer = null;
}
function resetMinutes() {
  stopMinutesPolling();
  minutesJobs = [];
  minutesJob = null;
  minutesRecordingId = null;
  $("minutes-panel").hidden = true;
  $("minutes-result").hidden = true;
  $("minutes-error").hidden = true;
  $("minutes-status").hidden = true;
  $("minutes-editor-wrap").hidden = true;
  $("minutes-actions").hidden = true;
  $("minutes-cancel").hidden = true;
  $("minutes-generate").disabled = false;
}
function minutesBusy(job = minutesJob) {
  return !!job && (job.status === "queued" || job.status === "running");
}
function minutesRecordingReady() {
  return !!displayedRecording && displayedRecording.status !== "active";
}
async function loadMinutesTemplates() {
  if (minutesTemplates) return minutesTemplates;
  const data = await api("meeting-templates");
  minutesTemplates = data.templates || [];
  minutesModelId = data.model_id || "";
  $("minutes-model-tag").textContent = "本机离线生成";
  $("minutes-model-tag").title = minutesModelId || "";
  const select = $("minutes-template");
  select.replaceChildren();
  for (const template of minutesTemplates) {
    const option = document.createElement("option");
    option.value = template.template_id;
    option.textContent =
      template.name + (template.builtin ? "" : "（自定义）");
    option.title = template.description || template.instructions || "";
    select.append(option);
  }
  if (!minutesTemplates.length) {
    const option = document.createElement("option");
    option.value = "";
    option.textContent = "没有可用模板";
    select.append(option);
  }
  return minutesTemplates;
}
function minutesEvidenceButton(evidence) {
  if (!evidence) return null;
  const button = node(
    "button",
    "minutes-evidence",
    (evidence.stamp ? "[" + evidence.stamp + "] " : "") +
      (evidence.speaker || (evidence.line ? "第 " + evidence.line + " 行" : "证据")),
  );
  button.type = "button";
  button.title = evidence.quote || "";
  if (Number.isFinite(evidence.elapsed_ms)) {
    button.onclick = () => {
      const audio = $("recording-audio");
      if (!audio.getAttribute("src")) return;
      audio.currentTime = fullToReady(evidence.elapsed_ms) / 1000;
      playRecordingAudio();
    };
  } else {
    button.disabled = true;
  }
  return button;
}
function minutesEvidence(target, evidence) {
  for (const item of evidence || []) {
    const button = minutesEvidenceButton(item);
    if (!button) continue;
    target.append(button);
    if (item.quote)
      target.append(node("span", "minutes-quote", "「" + item.quote + "」"));
  }
}
function minutesTextItem(text, meta, evidence) {
  const wrap = node("div", "minutes-item");
  wrap.append(node("p", "", text));
  if (meta) {
    const metaNode = node("div", "minutes-item-meta");
    for (const [key, value] of Object.entries(meta)) {
      if (value)
        metaNode.append(
          node("span", key === "warn" ? "minutes-badge warn" : "minutes-badge", value),
        );
    }
    if (metaNode.childElementCount) wrap.append(metaNode);
  }
  const evidenceNode = node("div", "");
  minutesEvidence(evidenceNode, evidence);
  if (evidenceNode.childElementCount) wrap.append(evidenceNode);
  return wrap;
}
function renderMinutesResult(result) {
  const target = $("minutes-result");
  target.replaceChildren();
  target.hidden = false;
  const head = node("div", "minutes-result-head");
  head.append(node("strong", "", result.template_name || "会议纪要"));
  head.append(node("span", "", "本机生成"));
  head.append(node("span", "", formatMinutesTime(result.generated_at)));
  target.append(head);
  const overview = node("div", "minutes-overview", result.overview || "");
  target.append(overview);
  for (const section of result.sections || []) {
    const block = node("section", "minutes-section");
    block.append(node("h3", "", section.title));
    if (section.kind === "text") {
      block.append(node("p", "minutes-empty", section.text || ""));
    } else if (section.kind === "topics") {
      if (!(section.items || []).length && !(section.key_points || []).length) {
        block.append(node("p", "minutes-empty", "未提取到明确议题。"));
      }
      for (const topic of section.items || []) {
        const item = node("div", "minutes-item");
        item.append(node("h4", "", topic.title || "未命名议题"));
        if (topic.summary) item.append(node("p", "", topic.summary));
        const evidence = node("div", "");
        minutesEvidence(evidence, topic.evidence);
        if (evidence.childElementCount) item.append(evidence);
        block.append(item);
      }
      if ((section.key_points || []).length) {
        block.append(node("h4", "", "关键点"));
        for (const point of section.key_points) {
          block.append(minutesTextItem(point.text, null, point.evidence));
        }
      }
    } else if (section.kind === "decisions") {
      if (!(section.items || []).length)
        block.append(node("p", "minutes-empty", "本段没有已确认决策。"));
      for (const item of section.items || []) {
        block.append(minutesTextItem(item.text, {badge: "已确认"}, item.evidence));
      }
    } else if (section.kind === "proposals") {
      if (!(section.items || []).length && !(section.questions || []).length)
        block.append(node("p", "minutes-empty", "没有提议、否决或未决事项。"));
      for (const item of section.items || []) {
        const label =
          {rejected: "已否决", open: "未决", proposed: "提议"}[item.status] ||
          "未决";
        block.append(minutesTextItem(item.text, {warn: label}, item.evidence));
      }
      for (const question of section.questions || []) {
        block.append(minutesTextItem(question.text, {warn: "待确认"}, question.evidence));
      }
    } else if (section.kind === "actions") {
      if (!(section.items || []).length)
        block.append(node("p", "minutes-empty", "没有提取到行动项。"));
      for (const item of section.items || []) {
        const meta = {
          badge: item.status === "agreed" ? "已确认" : "提议",
          warn: item.needs_confirmation ? "待确认" : "",
          owner: "负责人：" + (item.owner || "未明确"),
          due:
            "截止：" +
            (item.due_date || "未明确") +
            (item.due_date_relative ? "（相对日期，待确认）" : ""),
        };
        block.append(minutesTextItem(item.task, meta, item.evidence));
      }
    } else if (section.kind === "risks") {
      if (!(section.items || []).length && !(section.questions || []).length)
        block.append(node("p", "minutes-empty", "没有提取到风险或待确认事项。"));
      for (const item of section.items || [])
        block.append(minutesTextItem(item.text, {warn: "风险/阻塞"}, item.evidence));
      for (const question of section.questions || [])
        block.append(minutesTextItem(question.text, {warn: "待确认"}, question.evidence));
    }
    target.append(block);
  }
  if (result.coverage?.chunks_failed) {
    target.append(
      node(
        "p",
        "minutes-note",
        "本次有 " + result.coverage.chunks_failed + " 个转写片段生成失败，纪要可能不完整。",
      ),
    );
  }
  const actions = $("minutes-actions");
  actions.hidden = false;
  $("minutes-edit-button").disabled = !minutesJob?.result;
  const base =
    "recordings/" + encodeURIComponent(minutesJob.recording_id) +
    "/minutes/" + encodeURIComponent(minutesJob.id) + "/export?format=";
  for (const [id, format] of [
    ["minutes-export-md", "md"],
    ["minutes-export-txt", "txt"],
  ]) {
    const link = $(id);
    link.href = "/api/" + base + format;
    link.download =
      "meeting-minutes-" +
      minutesJob.recording_id.slice(0, 8) +
      "-" +
      minutesJob.id.slice(0, 8) +
      "." +
      format;
    link.hidden = !minutesJob?.result;
  }
}
function renderMinutesHistory() {
  const target = $("minutes-history");
  target.replaceChildren();
  if (!minutesJobs.length) {
    target.append(
      node("p", "minutes-empty", "还没有生成过纪要。选择模板后点“生成纪要”。"),
    );
    return;
  }
  for (const job of minutesJobs) {
    const item = node("button", "minutes-history-item" +
      (minutesJob?.id === job.id ? " selected" : ""));
    item.type = "button";
    const left = node("span", "");
    left.append(node("strong", "", job.template_name || "会议纪要"));
    left.append(
      node("small", "", " · " + formatMinutesTime(job.created_at)),
    );
    item.append(left);
    item.append(node("small", "", minutesStatusLabel(job.status)));
    item.onclick = () => openMinutesJob(job.id).catch((error) =>
      notice("读取纪要失败：" + error.message, true));
    target.append(item);
  }
}
async function loadMinutesJobs({select = false} = {}) {
  if (!displayedRecording) return;
  const data = await api(
    "recordings/" + encodeURIComponent(displayedRecording.id) + "/minutes",
  );
  minutesJobs = data.jobs || [];
  renderMinutesHistory();
  if (select && minutesJobs.length && !minutesJob) {
    await openMinutesJob(minutesJobs[0].id);
  }
}
async function openMinutesJob(jobId) {
  if (!displayedRecording) return;
  const data = await api(
    "recordings/" +
      encodeURIComponent(displayedRecording.id) +
      "/minutes/" +
      encodeURIComponent(jobId),
  );
  minutesJob = data.job;
  renderMinutesJob();
  renderMinutesHistory();
}
function renderMinutesJob() {
  const job = minutesJob;
  const panel = $("minutes-panel");
  const status = $("minutes-status");
  const error = $("minutes-error");
  const result = $("minutes-result");
  const actions = $("minutes-actions");
  const editor = $("minutes-editor-wrap");
  const generate = $("minutes-generate");
  const cancel = $("minutes-cancel");
  panel.hidden = false;
  if (!job) {
    status.hidden = true;
    error.hidden = true;
    result.hidden = true;
    editor.hidden = true;
    actions.hidden = true;
    cancel.hidden = true;
    generate.disabled = !minutesRecordingReady() || !displayedTranscript.length;
    return;
  }
  const busy = minutesBusy(job);
  const select = $("minutes-template");
  if ([...select.options].some((option) => option.value === job.template_id))
    select.value = job.template_id;
  generate.disabled = busy || !minutesRecordingReady() || !displayedTranscript.length;
  cancel.hidden = !busy;
  status.hidden = !busy;
  if (busy) {
    $("minutes-progress-bar").style.width = Math.max(0, Math.min(100, job.progress || 0)) + "%";
    $("minutes-status-text").textContent =
      (job.message || "正在生成") + " · " + (job.progress || 0) + "%";
  }
  if (job.status === "failed" || job.status === "cancelled" ||
      job.status === "interrupted") {
    error.hidden = false;
    error.textContent =
      (job.error?.message || job.message || "纪要生成失败。") +
      (job.status === "interrupted" ? " 可以重新生成。" : "");
  } else {
    error.hidden = true;
  }
  if (job.status === "completed" && job.result) {
    renderMinutesResult(job.result);
    if (job.edited_markdown) {
      $("minutes-result").append(
        node("p", "minutes-note",
          "这份纪要已手动编辑；上面的结构化内容仍是模型原始版本，导出会使用编辑后的 Markdown。"),
      );
    }
    actions.hidden = false;
  } else {
    result.hidden = true;
    actions.hidden = job.status !== "completed" || !job.result;
    for (const id of ["minutes-export-md", "minutes-export-txt"])
      $(id).hidden = true;
    $("minutes-edit-button").disabled = true;
  }
  editor.hidden = true;
  generate.textContent = job.status === "completed" ? "重新生成" : "生成纪要";
}
async function openMinutesPanel() {
  if (!displayedRecording) return;
  const panel = $("minutes-panel");
  panel.hidden = false;
  panel.scrollIntoView({behavior: "smooth", block: "start"});
  if (displayedRecording.status === "active") {
    $("minutes-error").hidden = false;
    $("minutes-error").textContent = "录音还没有结束，停止并保存后再生成会议纪要。";
    $("minutes-generate").disabled = true;
    return;
  }
  if (minutesRecordingId !== displayedRecording.id) {
    resetMinutes();
    minutesRecordingId = displayedRecording.id;
    minutesJob = null;
    panel.hidden = false;
  }
  try {
    await loadMinutesTemplates();
    await loadMinutesJobs();
    if (minutesJob) {
      renderMinutesJob();
      if (minutesBusy()) pollMinutesJob();
    } else if (minutesJobs.length) {
      await openMinutesJob(minutesJobs[0].id);
      if (minutesBusy()) pollMinutesJob();
    } else {
      renderMinutesJob();
    }
    if (!displayedTranscript.length) {
      $("minutes-error").hidden = false;
      $("minutes-error").textContent =
        "这条录音没有可用的转写，暂时无法生成会议纪要。";
      $("minutes-generate").disabled = true;
    }
  } catch (error) {
    notice("读取纪要信息失败：" + error.message, true);
    $("minutes-error").hidden = false;
    $("minutes-error").textContent = error.message;
  }
}
async function createMinutesTemplate() {
  const name = $("minutes-template-name").value.trim();
  const instructions = $("minutes-template-instructions").value.trim();
  const sections = [...document.querySelectorAll(
    "#minutes-template-sections input:checked")].map((item) => item.value);
  if (!name || !instructions) {
    notice("请填写模板名称和关注点。", true);
    return;
  }
  if (!sections.length) {
    notice("至少选择一个纪要栏目。", true);
    return;
  }
  try {
    const data = await api("meeting-templates", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({name, instructions, sections}),
    });
    minutesTemplates = null;
    await loadMinutesTemplates();
    $("minutes-template").value = data.template.template_id;
    $("minutes-template-name").value = "";
    $("minutes-template-instructions").value = "";
    $("minutes-template-create").closest("details").open = false;
    notice("自定义模板已保存，可以直接生成。");
  } catch (error) {
    notice("保存模板失败：" + error.message, true);
  }
}

async function startMinutesJob() {
  if (!displayedRecording) return;
  const templateId = $("minutes-template").value;
  if (!templateId) return;
  $("minutes-generate").disabled = true;
  $("minutes-error").hidden = true;
  try {
    const data = await api(
      "recordings/" + encodeURIComponent(displayedRecording.id) + "/minutes",
      {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({template_id: templateId}),
      },
    );
    minutesJob = data.job;
    renderMinutesJob();
    await loadMinutesJobs();
    pollMinutesJob();
  } catch (error) {
    $("minutes-error").hidden = false;
    $("minutes-error").textContent = error.message;
    $("minutes-generate").disabled = false;
    notice("生成会议纪要失败：" + error.message, true);
  }
}
function pollMinutesJob() {
  stopMinutesPolling();
  if (!minutesJob || !minutesBusy()) return;
  const jobId = minutesJob.id;
  const recordingId = minutesJob.recording_id;
  minutesPollTimer = setTimeout(async () => {
    if (!displayedRecording || displayedRecording.id !== recordingId ||
        minutesJob?.id !== jobId) return;
    try {
      await openMinutesJob(jobId);
      if (minutesBusy()) pollMinutesJob();
      else await loadMinutesJobs();
    } catch (error) {
      $("minutes-error").hidden = false;
      $("minutes-error").textContent = error.message;
    }
  }, 1200);
}
async function cancelMinutesJob() {
  if (!minutesJob) return;
  try {
    await api(
      "recordings/" +
        encodeURIComponent(minutesJob.recording_id) +
        "/minutes/" +
        encodeURIComponent(minutesJob.id),
      {method: "DELETE"},
    );
    minutesJob = {...minutesJob, status: "cancelled", stage: "cancelled",
      message: "生成任务已取消。"};
    stopMinutesPolling();
    renderMinutesJob();
    await loadMinutesJobs();
  } catch (error) {
    notice("取消生成失败：" + error.message, true);
  }
}
async function saveMinutesEdit() {
  if (!minutesJob) return;
  const markdown = $("minutes-editor").value;
  try {
    const data = await api(
      "recordings/" +
        encodeURIComponent(minutesJob.recording_id) +
        "/minutes/" +
        encodeURIComponent(minutesJob.id),
      {
        method: "PATCH",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({markdown}),
      },
    );
    minutesJob = data.job;
    $("minutes-editor-wrap").hidden = true;
    $("minutes-result").hidden = false;
    renderMinutesJob();
    notice("会议纪要编辑已保存。");
  } catch (error) {
    notice("保存纪要失败：" + error.message, true);
  }
}


const recordingAudio = $("recording-audio");
recordingAudio.autoplay = false;
recordingAudio.pause();
function playRecordingAudio() {
  allowRecordingPlayback = true;
  return recordingAudio.play().catch(() => {
    allowRecordingPlayback = false;
    notice("无法播放录音。", true);
  });
}
const recordingSeek = $("recording-seek");
recordingAudio.addEventListener("loadedmetadata", () => {
  const duration = Number.isFinite(recordingAudio.duration)
    ? recordingAudio.duration
    : 0;
  recordingSeek.max = duration;
  $("recording-total").textContent = formatClock(duration * 1000);
});
recordingAudio.addEventListener("timeupdate", () => {
  $("recording-current").textContent = formatClock(
    recordingAudio.currentTime * 1000,
  );
  if (!seekDragging) recordingSeek.value = recordingAudio.currentTime;
  if (displayedRecording?.status !== "active")
    highlightAt(readyToFull(recordingAudio.currentTime*1000)/1000,
                {scroll: !recordingAudio.paused});
});
recordingAudio.addEventListener("play", () => {
  if (!allowRecordingPlayback) {
    recordingAudio.pause();
    return;
  }
  $("recording-play").textContent = "⏸";
});
recordingAudio.addEventListener("pause", () => {
  allowRecordingPlayback = false;
  $("recording-play").textContent = "▶";
});
recordingAudio.addEventListener("ended", () => {
  allowRecordingPlayback = false;
  $("recording-play").textContent = "▶";
});
window.addEventListener("pagehide", () => {
  recordingAudio.pause();
});
recordingAudio.addEventListener("error", () => {
  if (!recordingAudio.getAttribute("src")) return;
  notice("录音音频加载失败，请稍后重试。", true);
});
recordingSeek.addEventListener("pointerdown", () => {
  seekDragging = true;
});
recordingSeek.addEventListener("pointerup", () => {
  seekDragging = false;
});
recordingSeek.addEventListener("input", () => {
  if (!recordingAudio.getAttribute("src")) return;
  seekDragging = true;
  recordingAudio.currentTime = Number(recordingSeek.value);
  $("recording-current").textContent = formatClock(
    recordingAudio.currentTime * 1000,
  );
  highlightAt(readyToFull(recordingAudio.currentTime*1000)/1000,
              {scroll: true, force: true});
});
recordingSeek.addEventListener("change", () => {
  seekDragging = false;
});
$("recording-play").onclick = () => {
  if (!recordingAudio.getAttribute("src")) return;
  if (recordingAudio.paused) playRecordingAudio();
  else recordingAudio.pause();
};
$("recording-export").onclick = () => exportTranscript();
$("recording-minutes-button").onclick = () => openMinutesPanel();
$("minutes-generate").onclick = () => startMinutesJob();
$("minutes-refresh").onclick = () =>
  loadMinutesJobs().catch((error) => notice("刷新纪要失败：" + error.message, true));
$("minutes-cancel").onclick = () => cancelMinutesJob();
$("minutes-template-create").onclick = () => createMinutesTemplate();
$("minutes-edit-button").onclick = () => {
  if (!minutesJob?.result) return;
  $("minutes-editor").value =
    minutesJob.edited_markdown || minutesJob.result.markdown || "";
  $("minutes-editor-wrap").hidden = false;
  $("minutes-result").hidden = true;
  $("minutes-editor").focus();
};
$("minutes-cancel-edit").onclick = () => {
  $("minutes-editor-wrap").hidden = true;
  $("minutes-result").hidden = false;
};
$("minutes-save-edit").onclick = () => saveMinutesEdit();
window.addEventListener("voicehub:view", (event) => {
  if (event.detail === "recordings")
    loadRecordings().catch((error) =>
      notice("读取录音失败：" + error.message, true));
  else
    recordingAudio.pause();
});

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
    // Let Chrome negotiate the AirPods hardware rate; capture.js converts to 16 kHz.
    context = new AudioContext();
    await context.audioWorklet.addModule("/static/capture.js");
  }
  await context.resume();
  return context;
}
let microphonePackets = 0, microphoneFrames = 0, microphonePeak = 0;
let microphoneLastFrame = 0, microphoneStarted = 0;
function renderMicrophone() {
  const track = stream?.getAudioTracks()[0];
  const target = $("microphone-status");
  if (!track) { target.textContent = "麦克风未开启"; $("microphone-level").value = 0; return; }
  const age = performance.now() - (microphoneLastFrame || microphoneStarted);
  const state = track.readyState !== "live" ? "输入已断开，请重新开启麦克风" :
    track.muted ? "输入设备暂无音频" : context?.state !== "running" ? "音频已暂停，请点击开启麦克风恢复" :
    age > 3000 ? "没有采集到音频帧，请重新开启麦克风或切换输入设备" :
    microphonePeak < .001 ? "输入接近静音，请检查麦克风选择和耳机佩戴" : "正在采集";
  target.textContent = [track.label || "默认输入", state,
    (context?.sampleRate || "—") + " Hz → 16000 Hz",
    "采集 " + microphoneFrames + " 帧 / 已上传 " + microphonePackets + " 帧"].join(" · ");
  $("microphone-level").value = Math.min(1, microphonePeak * 5);
}
async function refreshMicrophones() {
  if (!navigator.mediaDevices?.enumerateDevices) return;
  const select = $("microphone-device"), selected = select.value;
  const devices = await navigator.mediaDevices.enumerateDevices();
  select.replaceChildren();
  const automatic = node("option", "", "系统默认麦克风"); automatic.value = "";
  select.append(automatic);
  let index = 0;
  for (const device of devices.filter(d => d.kind === "audioinput")) {
    const option = node("option", "", device.label || "麦克风 " + (++index));
    option.value = device.deviceId; select.append(option);
  }
  select.value = [...select.options].some(o => o.value === selected) ? selected : "";
}
async function microphone() {
  if (stream) return;
  try {
    const device = $("microphone-device").value;
    // Opening Bluetooth input may change the hardware route and its sample rate.
    stream = await navigator.mediaDevices.getUserMedia({
      audio: {echoCancellation: true, noiseSuppression: true, autoGainControl: true,
        channelCount: 1, ...(device ? {deviceId: {exact: device}} : {})}, video: false,
    });
    // Discard the old output route before constructing the input graph.
    if (context && context.state !== "closed") await context.close();
    context = null;
    await audioContext();
    micSource = context.createMediaStreamSource(stream);
    capture = new AudioWorkletNode(context, "voicehub-capture");
    mute = context.createGain(); mute.gain.value = 0;
    micSource.connect(capture).connect(mute).connect(context.destination);
    microphonePackets = microphoneFrames = 0;
    microphonePeak = 0;
    microphoneLastFrame = 0; microphoneStarted = performance.now();
    capture.port.onmessage = (event) => {
      if (event.data?.type === "level") {
        microphonePeak = event.data.peak;
        microphoneLastFrame = performance.now();
        renderMicrophone();
        return;
      }
      microphoneFrames++;
      microphoneLastFrame = performance.now();
      if (socket?.readyState === WebSocket.OPEN && clientReady) {
        if (socket.bufferedAmount > 64000) {
          notice("上传音频积压，已断开以避免延迟累积。", true); disconnect(); return;
        }
        socket.send(event.data);
        microphonePackets++;
      }
    };
    const track = stream.getAudioTracks()[0];
    track.onmute = track.onunmute = renderMicrophone;
    track.onended = () => { renderMicrophone(); notice("麦克风已断开，请选择输入设备后重新开启。", true); };
    capture.onprocessorerror = () => notice("音频采集已停止，请重新开启麦克风。", true);
    renderMicrophone();
    await refreshMicrophones().catch(() => {});
    syncRecordingControls();
  } catch (e) {
    stopMicrophone();
    throw e;
  }
}
function stopMicrophone() {
  if (stream) for (const track of stream.getTracks()) {
    track.onmute = track.onunmute = track.onended = null;
    track.stop();
  }
  stream = null;
  if (capture) { capture.port.onmessage = null; capture.onprocessorerror = null; }
  micSource?.disconnect(); capture?.disconnect(); mute?.disconnect();
  capture = null; micSource = null; mute = null;
  renderMicrophone();
  syncRecordingControls();
}
setInterval(() => { if (stream) renderMicrophone(); }, 1000);
navigator.mediaDevices?.addEventListener("devicechange", () => refreshMicrophones().catch(() => {}));
$("microphone-device").onchange = () => {
  if (stream) connect(true);
};
refreshMicrophones().catch(() => {});

function phase(name, detail) {
  $("client-phase").textContent = labels[name] || name;
  $("client-detail").textContent =
    detail || "我在听。说完一句话后稍等，就会收到回复。";
  $("mic-orb").className = "mic-orb " + name;
}
async function loadLabMessages() {
  if (!identity?.session_id) return;
  const sid = identity.session_id;
  const messages = await api("sessions/" + sid + "/messages");
  await mcp.loadCalls(sid,messages);
  if (identity?.session_id !== sid) return;
  labMessages = messages;
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
      setTimeout(() => loadRecordings().catch(() => {}), 500);
      await new Promise((resolve) => setTimeout(resolve, 250));
    }
    clearPlayback();
    stopMicrophone();
    if (useMic) await microphone();
    else await audioContext();
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
      wake_mode: wantsMic ? $("client-wake").value : "manual",
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
      mcp.track(e);
      if (e.type.startsWith('tool.')) renderLab();
      if (e.type === "hello") {
        $("notice").hidden = true;
        retries = 0;
        identity = {
          device: identity?.device || crypto.randomUUID().slice(0, 8),
          session_id: e.session_id,
          resume_token: e.resume_token,
        };
        sessionStorage.setItem("voicehub-client", JSON.stringify(identity));
        activeWakeMode = e.wake_mode;
        activeWakePhrase = e.wake_phrase || "";
        $("wake-feedback").hidden = activeWakeMode !== "backend";
        $("wake-feedback").textContent = '待机时请说“' + activeWakePhrase + '”唤醒，我会回应“我在，请说。”。待机后仍可再次唤醒。';
        setClientReady(true);
        $("connect-text").disabled = $("connect-mic").disabled = false;
        $("client-status").textContent =
          "已连接" + (wantsMic ? " · 麦克风开启" : "");
        $("lab-session").textContent = e.session_id.slice(0, 12);
        await loadLabMessages();
        await loadRecordings().catch(() => {});
        if (e.wake_mode !== "backend") send({ type: "session.activate" });
        else phase("standby", "请说“" + activeWakePhrase + "”唤醒；无需点击激活聆听。");
        if (wantsMic && !e.capabilities.barge_in)
          notice("客户端未声明有效回声消除，自然插话未开启；仍可点击打断。");
        return;
      }
      if (e.type === "wake.check") {
        $("wake-feedback").hidden = false;
        $("wake-feedback").textContent = e.matched
          ? '已匹配“' + e.phrase + '”，进入聆听。'
          : '未匹配“' + e.phrase + '”。本次识别：' + (e.heard || '（未识别到文字）') + '。请完整说出唤醒词。';
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
      if (e.type === "session.state") {
        let detail;
        if (e.state === "recording") {
          detail = "录音中，只保存声音，不回答。实时转写在「录音」页。";
          if (!displayedRecording || displayedRecording.status !== "active")
            loadRecordings().catch(() => {});
        } else if (e.state === "standby") {
          detail = activeWakeMode === "backend"
            ? "请说“" + activeWakePhrase + "”唤醒。"
            : "等待唤醒或手动激活。";
        }
        phase(e.state, detail);
      }
      if (e.type === "recording.started") {
        handleRecordingStarted(e.recording).catch(() => {});
        loadRecordings().catch(() => {});
        return;
      }
      if (e.type === "recording.confirming") {
        if (displayedRecording?.id === e.recording?.id) {
          displayedConfirming = true;
          renderRecordingDetail();
          renderTranscript();
        }
        return;
      }
      if (e.type === "recording.confirm_expired") {
        if (displayedRecording?.id === e.recording?.id) {
          displayedConfirming = false;
          renderRecordingDetail();
          renderTranscript();
        }
        return;
      }
      if (e.type === "recording.stopped") {
        handleRecordingStopped(e.recording).catch(() => {});
        loadRecordings().catch(() => {});
        return;
      }
      if (e.type === "recording.transcript") {
        applyTranscriptEvent(e);
        return;
      }
      if (e.type === "recording.transcription_unavailable") {
        applyTranscriptionUnavailable(e);
        return;
      }
      if (e.type === "recording.speaker_unavailable") {
        notice(e.message || "发言人区分暂时不可用。", true);
        return;
      }
      if (e.type === "recording.diarized") {
        if (displayedRecording?.id === e.recording_id) {
          loadRecordings().catch(() => {});
          loadDisplayedTranscript(displayedRecording).catch(() => {});
          notice("发言人重分析完成，逐字稿已更新。");
        }
        return;
      }
      if (e.type === "recording.minutes_started") {
        notice("逐字稿已更新，正在自动生成默认会议纪要。");
        return;
      }
      if (e.type === "recording.deleted") {
        handleRecordingDeleted(e.recording_id);
        loadRecordings().catch(() => {});
        return;
      }
      if (e.type === "recording.input_ignored") {
        notice(e.message, true);
        return;
      }
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
    setTimeout(() => loadRecordings().catch(() => {}), 500);
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
  setTimeout(() => loadRecordings().catch(() => {}), 500);
}
$("connect-text").onclick = () => connect(false);
$("connect-mic").onclick = () => connect(true);
$("disconnect-client").onclick = disconnect;
$("recording-stop").onclick = () => stopDisplayedRecording();
$("recordings-refresh").onclick = () =>
  loadRecordings().catch((error) => notice("刷新录音失败：" + error.message, true));
$("lab-open-recordings").onclick = () => view("recordings");
$("activate-client").onclick = () => {
  context?.resume().catch(() => {});
  send({ type: "session.activate" });
};
$("client-wake").onchange = () => {
  $("wake-feedback").hidden = $("client-wake").value !== "backend";
  $("wake-feedback").textContent = '待机时请说“' + (state.config?.wake.phrase || "小宝同学") + '”唤醒，无需点击激活聆听。';
  if (socket) connect(wantsMic);
};
$("stop-client").onclick = () => {
  const recording = pageRecording();
  if (recording) {
    stopRecordingFromList(recording);
    return;
  }
  clearPlayback();
  send({ type: "interrupt" });
};
$("text-form").onsubmit = (event) => {
  event.preventDefault();
  if (pageRecording()) {
    notice("录音模式中不处理文字对话；请先停止录音。", true);
    return;
  }
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
    populateConfig((await api("config/models")).config);
    await refresh();
    await refreshServices();
    await loadRecordings().catch(() => {});
    if (state.view === 'mcp') await mcp.refreshCatalogue();
    observe();
  } catch (e) {
    notice(e.message, true);
  }
}
await boot();
