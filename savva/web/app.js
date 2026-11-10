const state = {
  sessionId: crypto.randomUUID(),
  busy: false,
  settings: null,
};

const elements = {
  messages: document.querySelector("#messages"),
  composer: document.querySelector("#composer"),
  input: document.querySelector("#message-input"),
  send: document.querySelector("#send-button"),
  connection: document.querySelector("#connection-pill"),
  toolList: document.querySelector("#tool-list"),
  toolCount: document.querySelector("#tool-count"),
  settingsDialog: document.querySelector("#settings-dialog"),
  settingsForm: document.querySelector("#settings-form"),
  settingsError: document.querySelector("#settings-error"),
  keyStatus: document.querySelector("#key-status"),
};

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const document = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(document.error?.message || `HTTP ${response.status}`);
    error.code = document.error?.code || "HTTP_ERROR";
    throw error;
  }
  return document;
}

async function initialize() {
  try {
    const [health, tools, settings] = await Promise.all([
      api("/api/health"),
      api("/api/tools"),
      api("/api/settings"),
    ]);
    renderHealth(health);
    renderTools(tools.tools);
    applySettings(settings);
  } catch (error) {
    elements.connection.textContent = "Offline";
    elements.connection.className = "pill error";
  }
}

function renderHealth(health) {
  const ready = health.api_key_configured || health.provider === "ollama";
  elements.connection.textContent = ready ? "Agent ready" : "API key missing";
  elements.connection.className = `pill ${ready ? "ready" : "error"}`;
  elements.connection.title = `${health.provider} · ${health.model}`;
}

function renderTools(tools) {
  elements.toolCount.textContent = String(tools.length);
  elements.toolList.replaceChildren();
  for (const tool of tools) {
    const item = document.createElement("article");
    item.className = "registry-item";
    const name = document.createElement("code");
    name.textContent = tool.name;
    const description = document.createElement("p");
    description.textContent = tool.description;
    const meta = document.createElement("div");
    meta.className = "registry-meta";
    const risk = document.createElement("span");
    risk.textContent = tool.risk_level;
    const timeout = document.createElement("span");
    timeout.textContent = `${tool.timeout_seconds}s`;
    meta.append(risk, timeout);
    item.append(name, description, meta);
    elements.toolList.append(item);
  }
}

function addUserMessage(text) {
  const welcome = elements.messages.querySelector(".welcome-card");
  if (welcome) welcome.remove();
  const article = document.createElement("article");
  article.className = "message message-user";
  article.textContent = text;
  elements.messages.append(article);
  scrollToBottom();
}

function addAgentShell() {
  const article = document.createElement("article");
  article.className = "message message-agent";
  const orb = document.createElement("span");
  orb.className = "agent-orb";
  orb.textContent = "S";
  const body = document.createElement("div");
  body.className = "agent-body";
  const thinking = document.createElement("div");
  thinking.className = "thinking";
  thinking.textContent = "SAVVA выбирает инструменты и проверяет результаты…";
  body.append(thinking);
  article.append(orb, body);
  elements.messages.append(article);
  scrollToBottom();
  return { article, body, thinking };
}

function renderAgentResult(shell, result) {
  shell.thinking.remove();
  const timeline = document.createElement("div");
  timeline.className = "tool-timeline";
  const calls = new Map();

  for (const event of result.events || []) {
    if (event.type === "tool_call") {
      const card = createToolCard(event);
      calls.set(event.call_id, card);
      timeline.append(card.element);
    } else if (event.type === "tool_result" || (event.type === "tool_call" && event.result)) {
      const card = calls.get(event.call_id);
      if (card && event.result) card.setResult(event.result);
    } else if (event.type === "tool_call" && event.result) {
      // Handled by the call renderer above for providers that combine both fields.
    }
    if (event.type === "model_usage") {
      const usage = document.createElement("div");
      usage.className = "usage-row";
      const cost = Number(event.cost_usd || 0);
      usage.textContent =
        `tokens ${event.total_tokens || 0} · cost $${cost.toFixed(6)} · ` +
        `step ${event.step + 1}`;
      timeline.append(usage);
    }
    if (event.type === "confirmation_required" || event.confirmation_required) {
      timeline.append(createConfirmation(event));
    }
  }

  const answer = document.createElement("div");
  answer.className = "answer";
  renderFormattedAnswer(
    answer,
    result.answer || "Модель не вернула текстовый ответ.",
  );
  shell.body.append(timeline, answer);
  scrollToBottom();
}

function renderFormattedAnswer(container, text) {
  const lines = String(text).replace(/\r/g, "").split("\n");
  let paragraph = [];
  let list = null;
  let listType = null;
  let code = null;

  const flushParagraph = () => {
    if (!paragraph.length) return;
    const element = document.createElement("p");
    appendInline(element, paragraph.join(" "));
    container.append(element);
    paragraph = [];
  };
  const flushList = () => {
    list = null;
    listType = null;
  };

  for (const line of lines) {
    if (line.trim().startsWith("```")) {
      flushParagraph();
      flushList();
      if (code) {
        container.append(code);
        code = null;
      } else {
        code = document.createElement("pre");
        const codeText = document.createElement("code");
        code.append(codeText);
        code.dataset.content = "";
      }
      continue;
    }
    if (code) {
      code.dataset.content += `${line}\n`;
      code.firstChild.textContent = code.dataset.content;
      continue;
    }
    if (!line.trim()) {
      flushParagraph();
      flushList();
      continue;
    }
    const heading = line.match(/^(#{1,6})\s+(.+)$/);
    if (heading) {
      flushParagraph();
      flushList();
      const element = document.createElement(heading[1].length <= 2 ? "h2" : "h3");
      appendInline(element, heading[2]);
      container.append(element);
      continue;
    }
    const bullet = line.match(/^\s*[-*]\s+(.+)$/);
    const numbered = line.match(/^\s*\d+\.\s+(.+)$/);
    if (bullet || numbered) {
      flushParagraph();
      const type = bullet ? "ul" : "ol";
      if (!list || listType !== type) {
        list = document.createElement(type);
        listType = type;
        container.append(list);
      }
      const item = document.createElement("li");
      appendInline(item, (bullet || numbered)[1]);
      list.append(item);
      continue;
    }
    paragraph.push(line.trim());
  }
  if (code) container.append(code);
  flushParagraph();
}

function appendInline(parent, text) {
  const parts = String(text).split(/(\*\*[^*]+\*\*|`[^`]+`)/g);
  for (const part of parts) {
    if (!part) continue;
    if (part.startsWith("**") && part.endsWith("**")) {
      const strong = document.createElement("strong");
      strong.textContent = part.slice(2, -2);
      parent.append(strong);
    } else if (part.startsWith("`") && part.endsWith("`")) {
      const code = document.createElement("code");
      code.textContent = part.slice(1, -1);
      parent.append(code);
    } else {
      parent.append(document.createTextNode(part));
    }
  }
}

function createToolCard(event) {
  const details = document.createElement("details");
  details.className = "tool-card";
  const summary = document.createElement("summary");
  const dot = document.createElement("span");
  dot.className = "tool-dot";
  const name = document.createElement("span");
  name.className = "tool-name";
  name.textContent = event.name || event.result?.tool || "unknown.tool";
  const status = document.createElement("span");
  status.className = "tool-state";
  status.textContent = "requested";
  summary.append(dot, name, status);

  const body = document.createElement("div");
  body.className = "tool-body";
  body.append(jsonBlock("Arguments", event.arguments || {}));
  const resultSlot = document.createElement("div");
  body.append(resultSlot);
  details.append(summary, body);

  if (event.result) {
    const result = event.result;
    status.textContent = result.status || "unknown";
    if (result.status !== "success") dot.classList.add("error");
    resultSlot.append(
      jsonBlock("Result", result),
      usageFooter(result.duration_ms),
    );
  }

  return {
    element: details,
    setResult(result) {
      status.textContent = result.status || "unknown";
      if (result.status !== "success") dot.classList.add("error");
      resultSlot.replaceChildren(
        jsonBlock("Result", result),
        usageFooter(result.duration_ms),
      );
    },
  };
}

function jsonBlock(titleText, value) {
  const section = document.createElement("section");
  const title = document.createElement("h4");
  title.textContent = titleText;
  const pre = document.createElement("pre");
  pre.className = "json-block";
  pre.textContent = JSON.stringify(value, null, 2);
  section.append(title, pre);
  return section;
}

function usageFooter(duration) {
  const footer = document.createElement("span");
  footer.className = "tool-state";
  footer.textContent = `${duration || 0} ms`;
  return footer;
}

function createConfirmation(event) {
  const card = document.createElement("div");
  card.className = "confirmation-card";
  const text = document.createElement("p");
  text.textContent = "Это действие потребует явного подтверждения. Write-инструменты пока отключены.";
  const button = document.createElement("button");
  button.type = "button";
  button.className = "button ghost";
  button.textContent = "Confirmation not available";
  button.disabled = true;
  card.append(text, button);
  return card;
}

function scrollToBottom() {
  requestAnimationFrame(() => {
    elements.messages.scrollTop = elements.messages.scrollHeight;
  });
}

async function sendMessage(text) {
  if (state.busy || !text.trim()) return;
  state.busy = true;
  elements.send.disabled = true;
  elements.input.value = "";
  addUserMessage(text);
  const shell = addAgentShell();
  try {
    const result = await api("/api/chat", {
      method: "POST",
      body: JSON.stringify({ message: text, session_id: state.sessionId }),
    });
    renderAgentResult(shell, result);
  } catch (error) {
    shell.thinking.remove();
    const answer = document.createElement("div");
    answer.className = "answer";
    answer.textContent = `${error.code}: ${error.message}`;
    shell.body.append(answer);
  } finally {
    state.busy = false;
    elements.send.disabled = false;
    elements.input.focus();
  }
}

function applySettings(settings) {
  state.settings = settings;
  const form = elements.settingsForm.elements;
  for (const [name, value] of Object.entries(settings)) {
    if (form[name]) form[name].value = value ?? "";
  }
  elements.keyStatus.textContent =
    settings.provider === "openrouter"
      ? (settings.api_key_configured ? "Key is configured in backend." : "Key is missing in backend.")
      : "Ollama does not use an API key.";
}

async function saveSettings() {
  elements.settingsError.textContent = "";
  const form = new FormData(elements.settingsForm);
  const payload = {
    provider: form.get("provider"),
    model: form.get("model"),
    base_url: form.get("base_url"),
    max_tokens: Number(form.get("max_tokens")),
    max_tool_steps: Number(form.get("max_tool_steps")),
    max_requests_per_session: Number(form.get("max_requests_per_session")),
    max_request_cost_usd: Number(form.get("max_request_cost_usd")),
    max_session_cost_usd: Number(form.get("max_session_cost_usd")),
    max_duration_seconds: Number(form.get("max_duration_seconds")),
    workspace_root: form.get("workspace_root"),
  };
  try {
    const result = await api("/api/settings", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    applySettings(result.settings);
    const health = await api("/api/health");
    renderHealth(health);
    elements.settingsDialog.close();
  } catch (error) {
    elements.settingsError.textContent = `${error.code}: ${error.message}`;
  }
}

elements.composer.addEventListener("submit", (event) => {
  event.preventDefault();
  sendMessage(elements.input.value);
});

elements.input.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    elements.composer.requestSubmit();
  }
});

document.querySelectorAll(".suggestion").forEach((button) => {
  button.addEventListener("click", () => sendMessage(button.dataset.prompt || ""));
});

document.querySelector("#open-settings").addEventListener("click", async () => {
  const settings = await api("/api/settings");
  applySettings(settings);
  elements.settingsDialog.showModal();
});

document.querySelector("#new-chat").addEventListener("click", async () => {
  await api("/api/reset", {
    method: "POST",
    body: JSON.stringify({ session_id: state.sessionId }),
  }).catch(() => {});
  state.sessionId = crypto.randomUUID();
  elements.messages.replaceChildren();
  location.reload();
});

elements.settingsForm.addEventListener("submit", (event) => {
  if (event.submitter?.value === "default") {
    event.preventDefault();
    saveSettings();
  }
});

elements.settingsForm.elements.provider.addEventListener("change", (event) => {
  const isOpenRouter = event.target.value === "openrouter";
  const model = elements.settingsForm.elements.model;
  const baseUrl = elements.settingsForm.elements.base_url;
  if (
    !model.value ||
    model.value === "nvidia/nemotron-3.5-lightning:free" ||
    model.value === "thinkingmachines/inkling-2-small:free" ||
    model.value === "qwen/qwen3.5-9b"
  ) {
    model.value = isOpenRouter
      ? "nvidia/nemotron-3.5-lightning:free"
      : "qwen/qwen3.5-9b";
  }
  if (!baseUrl.value || baseUrl.value.includes("openrouter.ai") || baseUrl.value.includes("127.0.0.1")) {
    baseUrl.value = isOpenRouter
      ? "https://openrouter.ai/api/v1"
      : "http://127.0.0.1:11434/v1";
  }
});

initialize();
