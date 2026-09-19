const chat = document.getElementById("chat");
const form = document.getElementById("composer");
const input = document.getElementById("input");
const sendBtn = document.getElementById("send");
const statusEl = document.getElementById("status");
const deepToggle = document.getElementById("deep");
const clearBtn = document.getElementById("clear");

const STORE_KEY = "pom-pom-chat-v1";
const WELCOME =
  "欢迎登上星穹列车，开拓者！帕姆是列车长帕姆，有什么想聊的尽管说哦！";
const { createConversation, statusText, resolveToken, MAX_MESSAGES } = window.PomPomCore;

// All conversation state lives in chat-core.js so it can be unit tested.
const conv = createConversation(loadTranscript());
let inFlight = null; // AbortController of the request currently on screen

const TOKEN_KEY = "pom-pom-token-v1";

function readStoredToken() {
  try {
    return localStorage.getItem(TOKEN_KEY) || "";
  } catch (err) {
    return "";
  }
}

// 通行证来自分享链接（#token=…），记下来下次直接可用
const accessToken = resolveToken(window.location.hash, readStoredToken());
if (accessToken) {
  try {
    localStorage.setItem(TOKEN_KEY, accessToken);
  } catch (err) {
    /* private mode: keep it only for this page */
  }
}

function authHeaders() {
  return accessToken ? { "X-Access-Token": accessToken } : {};
}

const DEEP_ICONS = {
  search_knowledge_base: "🔍",
  search_web: "🌐",
  fetch_page: "📖",
};

const SOURCE_NAMES = {
  duckduckgo: "DuckDuckGo",
  so360: "360",
  sogou: "搜狗",
};

function loadTranscript() {
  try {
    const raw = localStorage.getItem(STORE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed
      .filter(
        (item) =>
          item &&
          (item.role === "user" || item.role === "pom") &&
          typeof item.text === "string" &&
          item.text.trim()
      )
      .slice(-MAX_MESSAGES);
  } catch (err) {
    return []; // private mode / corrupted store: just start fresh
  }
}

function persistTranscript() {
  try {
    localStorage.setItem(STORE_KEY, JSON.stringify(conv.transcript));
  } catch (err) {
    /* quota or privacy mode: chatting still works, it just won't survive reload */
  }
}

function recordMessage(role, text) {
  conv.add(role, text);
  persistTranscript();
}

function renderMessage(role, text) {
  const div = document.createElement("div");
  div.className = `msg ${role === "user" ? "user" : "pom"}`;
  const avatar = role === "user"
    ? '<div class="mini-avatar"><div class="dot" style="background:#ffd479"></div></div>'
    : '<div class="mini-avatar"><div class="dot"></div></div>';
  const bubble = document.createElement("div");
  bubble.className = "bubble";
  bubble.textContent = text;
  div.innerHTML = avatar;
  div.appendChild(bubble);
  chat.appendChild(div);
  chat.scrollTop = chat.scrollHeight;
}

function addMessage(role, text) {
  renderMessage(role, text);
  recordMessage(role, text);
}

function clearConversation() {
  if (conv.transcript.length > 1 && !window.confirm("要清空和帕姆的全部对话吗？")) {
    return;
  }
  // Cancel the in-flight request and mark it stale: a late reply must not land
  // in the freshly cleared conversation.
  if (inFlight) {
    inFlight.abort();
    inFlight = null;
  }
  conv.clear();
  try {
    localStorage.removeItem(STORE_KEY);
  } catch (err) {
    /* ignore */
  }
  chat.innerHTML = "";
  addMessage("pom", WELCOME);
  input.focus();
}

function restoreConversation() {
  if (!conv.transcript.length) {
    return false;
  }
  conv.transcript.forEach((item) => renderMessage(item.role, item.text));
  return true;
}

function setTyping(on) {
  let el = document.getElementById("typing");
  if (on && !el) {
    el = document.createElement("div");
    el.id = "typing";
    el.className = "typing";
    el.textContent = "帕姆正在翻列车智库……";
    chat.appendChild(el);
    chat.scrollTop = chat.scrollHeight;
  } else if (!on && el) {
    el.remove();
  }
  sendBtn.disabled = on;
}

async function sendNormal(message, token, signal) {
  const resp = await fetch("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify({ message, history: conv.outgoing(message) }),
    signal
  });
  if (resp.status === 401) throw new Error("unauthorized");
  if (!resp.ok) throw new Error("bad status " + resp.status);
  const data = await resp.json();
  if (conv.isStale(token)) return data.reply; // cleared while we waited
  addMessage("pom", data.reply);
  return data.reply;
}

function addThinkingPanel() {
  const wrap = document.createElement("div");
  wrap.className = "msg pom";
  wrap.innerHTML = '<div class="mini-avatar"><div class="dot"></div></div>';
  const panel = document.createElement("details");
  panel.className = "think";
  panel.open = true;
  const summary = document.createElement("summary");
  summary.textContent = "帕姆正在深度思考……";
  const list = document.createElement("ul");
  list.className = "think-steps";
  panel.appendChild(summary);
  panel.appendChild(list);
  wrap.appendChild(panel);
  chat.appendChild(wrap);
  chat.scrollTop = chat.scrollHeight;
  return panel;
}

function addThinkStep(panel, step) {
  const item = document.createElement("li");
  item.className = "think-step";
  const icon = DEEP_ICONS[step.action] || "🔎";
  const hits = typeof step.hits === "number" ? step.hits : 0;
  const label = step.action === "fetch_page" ? "翻正文" : "查询";
  const from = SOURCE_NAMES[step.source] ? `（${SOURCE_NAMES[step.source]}）` : "";
  item.textContent =
    `${icon} ${label}「${step.input || ""}」` +
    (hits > 0 ? ` · 命中 ${hits} 条${from}` : " · 没有查到");
  panel.querySelector(".think-steps").appendChild(item);
  chat.scrollTop = chat.scrollHeight;
}

async function sendDeep(message, token, signal) {
  const panel = addThinkingPanel();
  const resp = await fetch("/api/chat/stream", {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify({
      message,
      history: conv.outgoing(message),
      mode: "deep",
    }),
    signal
  });
  if (resp.status === 401) throw new Error("unauthorized");
  if (!resp.ok || !resp.body) throw new Error("bad status " + resp.status);

  const reader = resp.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";
  let reply = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let split;
    while ((split = buffer.indexOf("\n\n")) >= 0) {
      const block = buffer.slice(0, split);
      buffer = buffer.slice(split + 2);
      const line = block.split("\n").find((row) => row.startsWith("data: "));
      if (!line) continue;
      let event;
      try {
        event = JSON.parse(line.slice(6));
      } catch (err) {
        continue;
      }
      if (event.type === "step") {
        addThinkStep(panel, event);
      } else if (event.type === "reply") {
        reply = event.reply || "";
      }
    }
  }

  const steps = panel.querySelectorAll(".think-step").length;
  panel.querySelector("summary").textContent = steps
    ? `帕姆的思考过程（查了 ${steps} 次）`
    : "帕姆的思考过程";
  panel.open = false;
  if (conv.isStale(token)) {
    return reply; // conversation was cleared; drop this answer
  }
  addMessage("pom", reply || "呜……列车广播好像出故障了，开拓者稍等一下再试试哦！");
  return reply;
}

async function send() {
  const message = input.value.trim();
  if (!message) return;
  const deep = deepToggle.checked;
  input.value = "";
  addMessage("user", message);
  if (!deep) setTyping(true);
  sendBtn.disabled = true;
  const token = conv.generation;
  const controller = new AbortController();
  inFlight = controller;
  try {
    // sendDeep/sendNormal already append the reply via addMessage, which keeps
    // the in-browser transcript (and the history sent to the backend) in sync.
    await (deep ? sendDeep(message, token, controller.signal) : sendNormal(message, token, controller.signal));
  } catch (err) {
    if (!controller.signal.aborted && !conv.isStale(token)) {
      addMessage(
        "pom",
        err && err.message === "unauthorized"
          ? "这趟列车需要通行证帕。请用带 #token=… 的链接打开，或者找帕姆要一张。"
          : "呜……列车广播好像出故障了，开拓者稍等一下再试试哦！"
      );
    }
  } finally {
    if (inFlight === controller) inFlight = null;
    if (!deep) setTyping(false);
    sendBtn.disabled = false;
  }
}

form.addEventListener("submit", (e) => { e.preventDefault(); send(); });
clearBtn.addEventListener("click", clearConversation);

async function init() {
  try {
    const resp = await fetch("/api/health");
    const data = await resp.json();
    const status = statusText(data);
    statusEl.textContent = status.text;
    statusEl.classList.toggle("ok", status.ok);
  } catch (e) {
    statusEl.textContent = "无法连接后端服务";
  }
}

// 刷新后恢复上次对话；没有记录再发欢迎语
if (!restoreConversation()) {
  addMessage("pom", WELCOME);
}
init();
