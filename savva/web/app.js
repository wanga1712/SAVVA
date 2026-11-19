const CHAT_STORAGE_KEY = "savva.chat-history.v1";

const state = {
  sessionId: "",
  chats: [],
  busySessions: new Set(),
  monitoredSessions: new Set(),
  lastNewChatAt: 0,
  storageWarningShown: false,
  corruptStorageWarning: false,
  welcomeCard: null,
  settings: null,
  currentProposal: null,
};

const elements = {
  messages: document.querySelector("#messages"),
  composer: document.querySelector("#composer"),
  input: document.querySelector("#message-input"),
  send: document.querySelector("#send-button"),
  connection: document.querySelector("#connection-pill"),
  network: document.querySelector("#network-pill"),
  toolList: document.querySelector("#tool-list"),
  toolCount: document.querySelector("#tool-count"),
  settingsDialog: document.querySelector("#settings-dialog"),
  settingsForm: document.querySelector("#settings-form"),
  settingsError: document.querySelector("#settings-error"),
  keyStatus: document.querySelector("#key-status"),
  toolsView: document.querySelector("#tools-view"),
  projectsView: document.querySelector("#projects-view"),
  tabTools: document.querySelector("#tab-tools"),
  tabProjects: document.querySelector("#tab-projects"),
  projectTree: document.querySelector("#project-tree"),
  actionJournal: document.querySelector("#action-journal"),
  fileDialog: document.querySelector("#file-dialog"),
  fileDialogTitle: document.querySelector("#file-dialog-title"),
  fileContent: document.querySelector("#file-content"),
  proposalDialog: document.querySelector("#proposal-dialog"),
  proposalTitle: document.querySelector("#proposal-title"),
  proposalSummary: document.querySelector("#proposal-summary"),
  proposalChanges: document.querySelector("#proposal-changes"),
  proposalDiff: document.querySelector("#proposal-diff"),
  proposalError: document.querySelector("#proposal-error"),
  approveProposal: document.querySelector("#approve-proposal"),
  rejectProposal: document.querySelector("#reject-proposal"),
  historyDialog: document.querySelector("#history-dialog"),
  historyList: document.querySelector("#chat-history-list"),
};

function createChatRecord() {
  const now = Date.now();
  return { id: crypto.randomUUID(), createdAt: now, updatedAt: now, messages: [] };
}

function activeChat() {
  return state.chats.find((chat) => chat.id === state.sessionId) || null;
}

function showStorageWarning(code, message) {
  if (state.storageWarningShown) return;
  state.storageWarningShown = true;
  appendSystemNotice(`${code}: ${message}`);
}

function persistChatState() {
  if (state.storageDisabled) return;
  try {
    localStorage.setItem(CHAT_STORAGE_KEY, JSON.stringify({
      version: 1,
      currentSessionId: state.sessionId,
      chats: state.chats,
    }));
  } catch {
    state.storageDisabled = true;
    showStorageWarning(
      "CHAT_STORAGE_UNAVAILABLE",
      "This chat remains open, but browser storage could not save it.",
    );
  }
}

function initializeChatHistory() {
  state.welcomeCard = elements.messages.querySelector(".welcome-card")?.cloneNode(true) || null;
  let saved;
  try {
    const raw = localStorage.getItem(CHAT_STORAGE_KEY);
    if (raw) {
      try {
        saved = JSON.parse(raw);
      } catch {
        state.storageDisabled = true;
        state.corruptStorageWarning = true;
      }
    }
  } catch {
    state.storageDisabled = true;
  }

  if (saved?.version === 1 && Array.isArray(saved.chats)) {
    state.chats = saved.chats.filter((chat) =>
      chat && typeof chat.id === "string" && Array.isArray(chat.messages),
    ).map((chat) => ({
      id: chat.id,
      createdAt: Number(chat.createdAt) || Date.now(),
      updatedAt: Number(chat.updatedAt) || Number(chat.createdAt) || Date.now(),
      messages: chat.messages.filter((message) =>
        message && ["user", "assistant", "status"].includes(message.role)
        && typeof message.content === "string",
      ).map((message) => ({
        id: typeof message.id === "string" ? message.id : crypto.randomUUID(),
        role: message.role,
        content: message.content,
        status: message.status === "pending" ? "interrupted" : message.status,
        operationId: typeof message.operationId === "string" ? message.operationId : null,
      })),
    }));
    const current = state.chats.find((chat) => chat.id === saved.currentSessionId);
    state.sessionId = current?.id || state.chats.at(-1)?.id || "";
  } else if (saved) {
    state.storageDisabled = true;
    state.corruptStorageWarning = true;
  }
  if (!state.sessionId) {
    const chat = createChatRecord();
    state.chats.push(chat);
    state.sessionId = chat.id;
  }
  renderCurrentChat();
  if (state.corruptStorageWarning) {
    appendSystemNotice(
      "CHAT_HISTORY_INVALID: Saved browser history was left untouched; a temporary new chat is available.",
    );
  } else if (state.storageDisabled) {
    showStorageWarning(
      "CHAT_STORAGE_UNAVAILABLE",
      "Browser storage could not be read; the current chat is available for this page only.",
    );
  } else if (!state.storageDisabled) {
    persistChatState();
  }
}

function renderSavedMessage(message) {
  if (message.role === "user") {
    const article = document.createElement("article");
    article.className = "message message-user";
    article.textContent = message.content;
    elements.messages.append(article);
    return;
  }
  if (message.role === "status") {
    const notice = document.createElement("div");
    notice.className = "usage-row";
    notice.textContent = message.content;
    elements.messages.append(notice);
    return;
  }
  const article = document.createElement("article");
  article.className = "message message-agent";
  const orb = document.createElement("span");
  orb.className = "agent-orb";
  orb.textContent = "S";
  const body = document.createElement("div");
  body.className = "agent-body";
  if (message.status === "pending" || message.status === "interrupted") {
    const pending = document.createElement("p");
    pending.className = "muted-note";
    pending.textContent = message.status === "interrupted"
      ? "Operation status was preserved as unknown after the page closed. It was not retried automatically."
      : message.content;
    body.append(pending);
  } else {
    const answer = document.createElement("div");
    answer.className = "answer";
    renderFormattedAnswer(answer, message.content);
    body.append(answer);
  }
  article.append(orb, body);
  elements.messages.append(article);
}

function renderCurrentChat() {
  elements.messages.replaceChildren();
  const chat = activeChat();
  if (!chat || chat.messages.length === 0) {
    if (state.welcomeCard) elements.messages.append(state.welcomeCard.cloneNode(true));
  } else {
    chat.messages.forEach(renderSavedMessage);
  }
  scrollToBottom();
}

function saveChatMessage(sessionId, message) {
  const chat = state.chats.find((item) => item.id === sessionId);
  if (!chat) return;
  chat.messages.push(message);
  chat.updatedAt = Date.now();
  persistChatState();
}

function chatTitle(chat) {
  const first = chat.messages.find((message) => message.role === "user");
  return first?.content?.trim().slice(0, 72) || "New conversation";
}

function renderChatHistory() {
  elements.historyList.replaceChildren();
  [...state.chats].sort((left, right) => right.updatedAt - left.updatedAt)
    .forEach((chat) => {
      const row = document.createElement("div");
      row.className = "history-row";
      const open = document.createElement("button");
      open.type = "button";
      open.className = "history-open";
      open.textContent = chatTitle(chat);
      open.setAttribute("aria-current", chat.id === state.sessionId ? "true" : "false");
      open.addEventListener("click", () => {
        state.sessionId = chat.id;
        persistChatState();
        elements.historyDialog.close();
        renderCurrentChat();
        updateComposerState();
        elements.input.focus();
        void restoreSession(chat.id);
      });
      const date = document.createElement("span");
      date.className = "history-date";
      date.textContent = new Date(chat.updatedAt).toLocaleString();
      row.append(open, date);
      elements.historyList.append(row);
    });
  if (!state.chats.length) {
    const empty = document.createElement("p");
    empty.className = "muted-note";
    empty.textContent = "No saved conversations.";
    elements.historyList.append(empty);
  }
}

function updateComposerState() {
  const busy = state.busySessions.has(state.sessionId);
  elements.send.disabled = busy;
  elements.input.setAttribute("aria-busy", String(busy));
}

function replaceChatFromSnapshot(chat, snapshot) {
  const pendingMatches = chat.messages.some((message) =>
    ["pending", "interrupted"].includes(message.status)
    && message.operationId === snapshot.operation_id,
  );
  const hasLocalTranscript = chat.messages.some((message) =>
    ["user", "assistant"].includes(message.role),
  );
  if (hasLocalTranscript && !pendingMatches) return;
  const restored = snapshot.messages.map((message, index) => ({
    id: `restored-${index}`,
    role: message.role,
    content: message.content,
    status: "complete",
    operationId: null,
  }));
  chat.messages = restored;
  if (snapshot.state === "failed") {
    chat.messages.push({
      id: crypto.randomUUID(), role: "status",
      content: "The last operation failed before completion. Its input was preserved; it was not retried.",
      status: "complete", operationId: snapshot.operation_id || null,
    });
  }
  chat.updatedAt = Date.now();
  persistChatState();
  if (state.sessionId === chat.id) renderCurrentChat();
}

async function restoreSession(sessionId) {
  try {
    const snapshot = await api(`/api/session?session_id=${encodeURIComponent(sessionId)}`, {
      timeoutMs: 5000,
    });
    if (!snapshot.exists) return;
    const chat = state.chats.find((item) => item.id === sessionId);
    if (!chat) return;
    if (snapshot.state === "active") {
      if (!chat.messages.some((message) => message.status === "pending" || message.status === "interrupted")) {
        chat.messages.push({
          id: crypto.randomUUID(), role: "assistant", status: "pending",
          operationId: snapshot.operation_id || null,
          content: "SAVVA is still processing this conversation. It will not be submitted again.",
        });
        persistChatState();
        if (state.sessionId === sessionId) renderCurrentChat();
      }
      monitorSession(sessionId);
      return;
    }
    replaceChatFromSnapshot(chat, snapshot);
  } catch (error) {
    if (state.sessionId === sessionId) {
      appendSystemNotice(`SESSION_STATUS_UNAVAILABLE: ${error.message}`);
    }
  }
}

function monitorSession(sessionId) {
  if (state.monitoredSessions.has(sessionId)) return;
  state.monitoredSessions.add(sessionId);
  const poll = async (remaining) => {
    if (remaining <= 0) {
      state.monitoredSessions.delete(sessionId);
      if (state.sessionId === sessionId) {
        appendSystemNotice("OPERATION_STATUS_PENDING: The operation is still recorded; it was not resubmitted.");
      }
      return;
    }
    try {
      const snapshot = await api(`/api/session?session_id=${encodeURIComponent(sessionId)}`, {
        timeoutMs: 5000,
      });
      if (snapshot.state !== "active") {
        const chat = state.chats.find((item) => item.id === sessionId);
        if (chat && snapshot.exists) replaceChatFromSnapshot(chat, snapshot);
        state.monitoredSessions.delete(sessionId);
        return;
      }
    } catch (error) {
      if (remaining <= 1 && state.sessionId === sessionId) {
        appendSystemNotice(`SESSION_STATUS_UNAVAILABLE: ${error.message}`);
      }
    }
    window.setTimeout(() => { void poll(remaining - 1); }, 1500);
  };
  window.setTimeout(() => { void poll(80); }, 1500);
}

async function restoreCurrentSession() {
  await restoreSession(state.sessionId);
}

async function api(path, options = {}) {
  const { timeoutMs = 180000, ...requestOptions } = options;
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), timeoutMs);
  let response;
  try {
    response = await fetch(path, {
      ...requestOptions,
      headers: {
        "Content-Type": "application/json",
        "X-SAVVA-Request": "1",
        ...(requestOptions.headers || {}),
      },
      signal: requestOptions.signal || controller.signal,
    });
  } catch (cause) {
    const error = new Error(cause.name === "AbortError"
      ? "The request timed out. Its operation status is preserved in this chat."
      : "The SAVVA server could not be reached.");
    error.code = cause.name === "AbortError" ? "REQUEST_TIMEOUT" : "SERVER_UNAVAILABLE";
    throw error;
  } finally {
    window.clearTimeout(timeout);
  }
  let document;
  try {
    document = await response.json();
  } catch {
    const error = new Error("The SAVVA server returned an unreadable response.");
    error.code = "INVALID_RESPONSE";
    throw error;
  }
  if (!response.ok) {
    const error = new Error(document.error?.message || `HTTP ${response.status}`);
    error.code = document.error?.code || "HTTP_ERROR";
    throw error;
  }
  return document;
}

async function initialize() {
  try {
    renderHealth(await api("/api/health"));
  } catch (error) {
    elements.connection.textContent = "Offline";
    elements.connection.className = "pill error";
    appendSystemNotice(`${error.code}: ${error.message}`);
  }
  const auxiliary = [
    ["Tools", async () => renderTools((await api("/api/tools")).tools)],
    ["Settings", async () => applySettings(await api("/api/settings"))],
    ["Network status", async () => renderNetwork((await api("/api/network")).tailscale)],
  ];
  const results = await Promise.allSettled(auxiliary.map(([, load]) => load()));
  results.forEach((result, index) => {
    if (result.status === "rejected") {
      const error = result.reason;
      appendSystemNotice(`${auxiliary[index][0]} unavailable (${error.code || "REQUEST_FAILED"}).`);
    }
  });
  await restoreCurrentSession();
}

function renderNetwork(tailscale) {
  if (!tailscale) return;
  const ready = Boolean(tailscale.ready);
  const hostname = tailscale.exit_node_hostname || "exit node unavailable";
  elements.network.textContent = `Tailscale: ${hostname}`;
  elements.network.className = `pill ${ready ? "ready" : "error"}`;
  elements.network.title = ready
    ? `Exit node: ${tailscale.exit_node_dns_name || hostname}`
    : tailscale.error || "Tailscale exit node is unavailable.";
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
  if (result.egress) renderNetwork(result.egress);
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
    if (event.type === "proposal_ready" && event.proposal) {
      timeline.append(createProposalCard(event.proposal));
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
  text.textContent = "Модель запросила запись. Проверьте точный diff и подтвердите операцию.";
  card.append(text);
  if (event.proposal) {
    card.append(createReviewButton(event.proposal.proposal_id));
  }
  return card;
}

function createProposalCard(proposal) {
  const card = document.createElement("div");
  card.className = "proposal-card";
  const copy = document.createElement("div");
  const title = document.createElement("strong");
  title.textContent = proposal.summary || "Project proposal";
  const details = document.createElement("p");
  details.textContent =
    `${proposal.file_count} files · ${proposal.total_bytes} bytes · ` +
    `fingerprint ${String(proposal.fingerprint || "").slice(0, 12)}…`;
  copy.append(title, details);
  card.append(copy, createReviewButton(proposal.proposal_id));
  return card;
}

function createReviewButton(proposalId) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "button primary";
  button.textContent = "Review diff";
  button.addEventListener("click", () => openProposal(proposalId));
  return button;
}

async function openProposal(proposalId) {
  elements.proposalError.textContent = "";
  try {
    const result = await api(
      `/api/proposals/${encodeURIComponent(proposalId)}?session_id=${encodeURIComponent(state.sessionId)}`,
    );
    state.currentProposal = result.proposal;
    elements.proposalTitle.textContent = result.proposal.summary || "Review proposal";
    elements.proposalSummary.textContent =
      `${result.proposal.file_count} files, ${result.proposal.total_bytes} bytes. ` +
      `This approval is one-time and bound to fingerprint ${result.proposal.fingerprint}.`;
    elements.proposalChanges.replaceChildren();
    for (const change of result.proposal.changes || []) {
      const chip = document.createElement("span");
      chip.className = `change-chip ${change.action}`;
      chip.textContent = `${change.action}: ${change.path} (${change.size_bytes} B)`;
      elements.proposalChanges.append(chip);
    }
    elements.proposalDiff.textContent = result.proposal.diff || "No diff available.";
    elements.approveProposal.disabled = false;
    elements.rejectProposal.disabled = false;
    elements.proposalDialog.showModal();
  } catch (error) {
    appendSystemNotice(`${error.code}: ${error.message}`);
  }
}

async function decideProposal(decision) {
  if (!state.currentProposal) return;
  elements.approveProposal.disabled = true;
  elements.rejectProposal.disabled = true;
  elements.proposalError.textContent = "";
  try {
    await api("/api/confirm", {
      method: "POST",
      body: JSON.stringify({
        proposal_id: state.currentProposal.proposal_id,
        confirmation_token: state.currentProposal.confirmation_token,
        session_id: state.sessionId,
        decision,
      }),
    });
    elements.proposalDialog.close();
    state.currentProposal = null;
    appendSystemNotice(
      decision === "approve"
        ? "Files written after exact proposal approval."
        : "Proposal rejected. No files were changed.",
    );
    await Promise.all([loadProjectTree(), loadActions()]);
  } catch (error) {
    elements.proposalError.textContent = `${error.code}: ${error.message}`;
    elements.approveProposal.disabled = false;
    elements.rejectProposal.disabled = false;
  }
}

function appendSystemNotice(text) {
  const notice = document.createElement("div");
  notice.className = "usage-row";
  notice.textContent = text;
  elements.messages.append(notice);
  scrollToBottom();
}

function showInspector(view) {
  const projects = view === "projects";
  elements.toolsView.classList.toggle("hidden", projects);
  elements.projectsView.classList.toggle("hidden", !projects);
  elements.tabTools.classList.toggle("active", !projects);
  elements.tabProjects.classList.toggle("active", projects);
  if (projects) {
    loadProjectTree();
    loadActions();
  }
}

async function loadProjectTree() {
  elements.projectTree.replaceChildren();
  try {
    const result = await api("/api/projects/tree?path=.");
    if (!result.entries?.length) {
      const empty = document.createElement("p");
      empty.className = "muted-note";
      empty.textContent = "Workspace is empty. Ask SAVVA to create a project.";
      elements.projectTree.append(empty);
      return;
    }
    for (const entry of result.entries) {
      const item = document.createElement(entry.type === "file" ? "button" : "div");
      item.className = "tree-item";
      if (entry.type === "file") item.type = "button";
      const depth = Math.max(0, entry.path.split("/").length - 1);
      item.style.paddingLeft = `${8 + depth * 14}px`;
      const icon = document.createElement("span");
      icon.className = "tree-icon";
      icon.textContent = entry.type === "directory" ? "▾" : "·";
      const path = document.createElement("span");
      path.textContent = entry.path.split("/").at(-1);
      const size = document.createElement("span");
      size.className = "tree-size";
      size.textContent = entry.type === "file" ? `${entry.size_bytes} B` : "";
      item.append(icon, path, size);
      if (entry.type === "file") {
        item.addEventListener("click", () => openFile(entry.path));
      }
      elements.projectTree.append(item);
    }
  } catch (error) {
    const message = document.createElement("p");
    message.className = "muted-note";
    message.textContent = `${error.code}: ${error.message}`;
    elements.projectTree.append(message);
  }
}

async function openFile(path) {
  elements.proposalError.textContent = "";
  try {
    const result = await api(`/api/files/content?path=${encodeURIComponent(path)}`);
    elements.fileDialogTitle.textContent = result.path;
    elements.fileContent.textContent = result.content;
    elements.fileDialog.showModal();
  } catch (error) {
    appendSystemNotice(`${error.code}: ${error.message}`);
  }
}

async function loadActions() {
  elements.actionJournal.replaceChildren();
  try {
    const result = await api("/api/actions");
    if (!result.actions?.length) {
      const empty = document.createElement("p");
      empty.className = "muted-note";
      empty.textContent = "No agent actions yet.";
      elements.actionJournal.append(empty);
      return;
    }
    for (const action of result.actions.slice(-20).reverse()) {
      const entry = document.createElement("article");
      entry.className = "action-entry";
      const title = document.createElement("strong");
      title.textContent = `${action.action} · ${action.status}`;
      const details = document.createElement("p");
      details.textContent =
        `${action.project_path || "."} · ${action.paths?.length || 0} files · ` +
        `${action.message || ""}`;
      const timestamp = document.createElement("time");
      timestamp.textContent = new Date(action.timestamp_unix * 1000).toLocaleString();
      entry.append(title, details, timestamp);
      elements.actionJournal.append(entry);
    }
  } catch (error) {
    const message = document.createElement("p");
    message.className = "muted-note";
    message.textContent = `${error.code}: ${error.message}`;
    elements.actionJournal.append(message);
  }
}

function scrollToBottom() {
  requestAnimationFrame(() => {
    elements.messages.scrollTop = elements.messages.scrollHeight;
  });
}

async function sendMessage(text) {
  const sessionId = state.sessionId;
  if (state.busySessions.has(sessionId) || !text.trim()) return;
  const operationId = crypto.randomUUID();
  const pendingMessage = {
    id: operationId,
    role: "assistant",
    content: "SAVVA is working on this request…",
    status: "pending",
    operationId,
  };
  state.busySessions.add(sessionId);
  updateComposerState();
  elements.input.value = "";
  addUserMessage(text);
  saveChatMessage(sessionId, {
    id: crypto.randomUUID(), role: "user", content: text, status: "complete",
    operationId,
  });
  saveChatMessage(sessionId, pendingMessage);
  const shell = addAgentShell();
  try {
    const result = await api("/api/chat", {
      method: "POST",
      body: JSON.stringify({
        message: text, session_id: sessionId, operation_id: operationId,
      }),
    });
    pendingMessage.content = result.answer || "The model returned an empty answer.";
    pendingMessage.status = "complete";
    const chat = state.chats.find((item) => item.id === sessionId);
    if (chat) chat.updatedAt = Date.now();
    persistChatState();
    if (state.sessionId === sessionId) renderAgentResult(shell, result);
  } catch (error) {
    if (error.code === "REQUEST_TIMEOUT") {
      pendingMessage.content = "Request timed out; operation status is unknown and will not be retried automatically.";
      pendingMessage.status = "interrupted";
      monitorSession(sessionId);
    } else {
      pendingMessage.content = `${error.code || "REQUEST_FAILED"}: ${error.message}`;
      pendingMessage.status = "failed";
    }
    persistChatState();
    if (state.sessionId === sessionId) {
      shell.thinking.remove();
      const answer = document.createElement("div");
      answer.className = "answer";
      answer.textContent = pendingMessage.content;
      shell.body.append(answer);
    }
  } finally {
    state.busySessions.delete(sessionId);
    if (state.sessionId === sessionId) {
      updateComposerState();
      elements.input.focus();
    }
  }
}

function applySettings(settings) {
  state.settings = settings;
  const form = elements.settingsForm.elements;
  for (const [name, value] of Object.entries(settings)) {
    if (!form[name]) continue;
    if (form[name].type === "checkbox") {
      form[name].checked = Boolean(value);
    } else {
      form[name].value = value ?? "";
    }
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
    require_tailscale_exit_node:
      form.get("require_tailscale_exit_node") === "on",
    expected_exit_node: form.get("expected_exit_node"),
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

elements.messages.addEventListener("click", (event) => {
  const button = event.target.closest(".suggestion");
  if (button) void sendMessage(button.dataset.prompt || "");
});

document.querySelector("#open-settings").addEventListener("click", async () => {
  try {
    applySettings(await api("/api/settings"));
    elements.settingsDialog.showModal();
  } catch (error) {
    appendSystemNotice(`SETTINGS_UNAVAILABLE: ${error.message}`);
  }
});

document.querySelector("#new-chat").addEventListener("click", () => {
  const now = Date.now();
  if (now - state.lastNewChatAt < 250) return;
  state.lastNewChatAt = now;
  if (activeChat()?.messages.length === 0) {
    elements.input.focus();
    return;
  }
  const chat = createChatRecord();
  state.chats.push(chat);
  state.sessionId = chat.id;
  persistChatState();
  renderCurrentChat();
  updateComposerState();
  elements.input.focus();
});

document.querySelector("#chat-history").addEventListener("click", () => {
  renderChatHistory();
  elements.historyDialog.showModal();
});

elements.tabTools.addEventListener("click", () => showInspector("tools"));
elements.tabProjects.addEventListener("click", () => showInspector("projects"));
document.querySelector("#open-projects").addEventListener("click", () => {
  showInspector("projects");
  document.querySelector(".inspector-panel").scrollIntoView({ behavior: "smooth" });
});
document.querySelector("#refresh-projects").addEventListener("click", loadProjectTree);
document.querySelector("#refresh-actions").addEventListener("click", loadActions);
elements.approveProposal.addEventListener("click", () => decideProposal("approve"));
elements.rejectProposal.addEventListener("click", () => decideProposal("reject"));

document.querySelectorAll("[data-close-dialog]").forEach((button) => {
  button.addEventListener("click", () => {
    const dialog = document.querySelector(`#${button.dataset.closeDialog}`);
    dialog?.close();
  });
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

initializeChatHistory();
initialize();
