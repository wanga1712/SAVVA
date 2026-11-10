const state = {
  sessionId: crypto.randomUUID(),
  busy: false,
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
};

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: {
      "Content-Type": "application/json",
      "X-SAVVA-Request": "1",
    },
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
    const [health, tools, settings, network] = await Promise.all([
      api("/api/health"),
      api("/api/tools"),
      api("/api/settings"),
      api("/api/network"),
    ]);
    renderHealth(health);
    renderNetwork(network.tailscale);
    renderTools(tools.tools);
    applySettings(settings);
  } catch (error) {
    elements.connection.textContent = "Offline";
    elements.connection.className = "pill error";
  }
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

initialize();
