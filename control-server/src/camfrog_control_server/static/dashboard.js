"use strict";

const tokenInput = document.getElementById("admin-token");
const notice = document.getElementById("notice");
const accountNames = new Map();
const agentNames = new Map();
let accessToken = "";
let sessionGeneration = 0;

function setNotice(message, kind = "") {
  notice.textContent = message;
  notice.className = `notice ${kind}`.trim();
}

function clearDashboardData() {
  accountNames.clear();
  agentNames.clear();
  for (const id of [
    "overview-cards", "agents-table", "accounts-table", "commands-table", "audit-table",
    "profiles-table", "cloud-bots-table", "store-table",
  ]) document.getElementById(id).replaceChildren();
  const agentSelect = document.querySelector('#account-form select[name="agent_id"]');
  agentSelect.replaceChildren(new Option("Unassigned", ""));
  document.querySelector('#command-form select[name="account_id"]').replaceChildren();
  const tokenResult = document.getElementById("agent-token-result");
  tokenResult.replaceChildren();
  tokenResult.hidden = true;
}

function resetDashboardSession() {
  sessionGeneration += 1;
  accessToken = "";
  clearDashboardData();
}

function cell(row, value) {
  const node = document.createElement("td");
  node.textContent = value === null || value === undefined || value === "" ? "—" : String(value);
  row.append(node);
  return node;
}

function emptyRow(body, columns, message) {
  const row = document.createElement("tr");
  const node = document.createElement("td");
  node.colSpan = columns;
  node.className = "empty-state";
  node.textContent = message;
  row.append(node);
  body.append(row);
}

function button(label, kind, action) {
  const element = document.createElement("button");
  element.type = "button";
  element.className = kind;
  element.textContent = label;
  element.addEventListener("click", action);
  return element;
}

async function api(path, options = {}) {
  if (!accessToken) throw new Error("Connect with the bootstrap admin token first.");
  const requestGeneration = sessionGeneration;
  const headers = new Headers(options.headers || {});
  headers.set("Authorization", `Bearer ${accessToken}`);
  headers.set("Accept", "application/json");
  if (options.body) headers.set("Content-Type", "application/json");
  const response = await fetch(`/api/v1${path}`, { ...options, headers, credentials: "same-origin" });
  if (!response.ok) {
    if (response.status === 401 && requestGeneration === sessionGeneration) {
      resetDashboardSession();
      setNotice("Dashboard session expired. Connect again.", "error");
    }
    let message = `Request failed with HTTP ${response.status}.`;
    try {
      const data = await response.json();
      if (typeof data.detail === "string") message = data.detail;
    } catch (_) { /* Keep the status-only message. */ }
    throw new Error(message);
  }
  if (response.status === 204) return null;
  return response.json();
}

async function connect() {
  const attemptGeneration = ++sessionGeneration;
  accessToken = "";
  clearDashboardData();
  const bootstrapToken = tokenInput.value.trim();
  if (!bootstrapToken) { setNotice("Enter the bootstrap admin token.", "error"); return; }
  tokenInput.value = "";
  try {
    const response = await fetch("/api/v1/auth/session", {
      method: "POST",
      headers: { Authorization: `Bearer ${bootstrapToken}`, Accept: "application/json" },
      credentials: "same-origin",
    });
    if (!response.ok) throw new Error(`Authentication failed with HTTP ${response.status}.`);
    const session = await response.json();
    if (attemptGeneration !== sessionGeneration) return;
    accessToken = session.access_token;
    await load(attemptGeneration);
  } catch (error) {
    if (attemptGeneration !== sessionGeneration) return;
    accessToken = "";
    clearDashboardData();
    setNotice(error.message, "error");
  }
}

function formatDate(value) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "—" : date.toLocaleString();
}

function renderOverview(data) {
  const cards = [
    ["Active agents", data.agents_active],
    ["Enabled accounts", data.accounts_enabled],
    ["Pending commands", data.commands_pending],
    ["Claimed commands", data.commands_claimed],
    ["Profiles reported", data.profiles_reported],
    ["Cloud Bots registered", data.cloud_bots_registered],
    ["Awaiting purchase review", data.purchase_intents_pending],
  ];
  const root = document.getElementById("overview-cards");
  root.replaceChildren();
  for (const [label, value] of cards) {
    const card = document.createElement("div");
    card.className = "card";
    const count = document.createElement("strong");
    count.textContent = String(value);
    const caption = document.createElement("span");
    caption.textContent = label;
    card.append(count, caption);
    root.append(card);
  }
}

function renderAgents(items) {
  agentNames.clear();
  const body = document.getElementById("agents-table");
  body.replaceChildren();
  const select = document.querySelector('#account-form select[name="agent_id"]');
  select.replaceChildren(new Option("Unassigned", ""));
  if (!items.length) { emptyRow(body, 4, "No agents registered yet."); return; }
  for (const item of items) {
    agentNames.set(item.id, item.name);
    if (!item.revoked_at) select.add(new Option(item.name, item.id));
    const row = document.createElement("tr");
    cell(row, item.name);
    cell(row, item.runtime_state);
    cell(row, formatDate(item.last_seen_at));
    const action = document.createElement("td");
    if (item.revoked_at) action.textContent = "Revoked";
    else action.append(button("Revoke", "danger", async () => {
      if (!window.confirm(`Revoke agent ${item.name}? Pending commands will be canceled. Claimed commands will be marked effect unknown because an action may already be running and cannot be interrupted.`)) return;
      const requestGeneration = sessionGeneration;
      try {
        await api(`/agents/${item.id}/revoke`, { method: "POST" });
        if (requestGeneration !== sessionGeneration) return;
        await load(requestGeneration);
        if (requestGeneration === sessionGeneration) setNotice("Agent revoked.", "success");
      } catch (error) {
        if (requestGeneration === sessionGeneration) setNotice(error.message, "error");
      }
    }));
    row.append(action);
    body.append(row);
  }
}

function renderAccounts(items) {
  accountNames.clear();
  const body = document.getElementById("accounts-table");
  const select = document.querySelector('#command-form select[name="account_id"]');
  body.replaceChildren();
  select.replaceChildren();
  if (!items.length) { emptyRow(body, 4, "No accounts registered yet."); return; }
  for (const item of items) {
    accountNames.set(item.id, item.nickname);
    if (item.enabled && item.agent_id) select.add(new Option(item.nickname, item.id));
    const row = document.createElement("tr");
    cell(row, item.nickname);
    cell(row, item.agent_id ? (agentNames.get(item.agent_id) || item.agent_id) : "Unassigned");
    cell(row, item.enabled ? "Yes" : "No");
    cell(row, formatDate(item.created_at));
    body.append(row);
  }
}

function renderCommands(items) {
  const body = document.getElementById("commands-table");
  body.replaceChildren();
  if (!items.length) { emptyRow(body, 6, "No commands in the queue."); return; }
  for (const item of items) {
    const row = document.createElement("tr");
    cell(row, formatDate(item.created_at));
    cell(row, accountNames.get(item.account_id) || item.account_id);
    cell(row, item.operation);
    cell(row, item.state === "effect_unknown" ? "Effect unknown" : item.state);
    cell(row, item.result_code);
    const action = document.createElement("td");
    if (item.state === "pending") action.append(button("Cancel", "secondary", async () => {
      const requestGeneration = sessionGeneration;
      try {
        await api(`/commands/${item.id}/cancel`, { method: "POST" });
        if (requestGeneration !== sessionGeneration) return;
        await load(requestGeneration);
        if (requestGeneration === sessionGeneration) setNotice("Command canceled.", "success");
      } catch (error) {
        if (requestGeneration === sessionGeneration) setNotice(error.message, "error");
      }
    }));
    else action.textContent = "—";
    row.append(action);
    body.append(row);
  }
}

function renderAudit(items) {
  const body = document.getElementById("audit-table");
  body.replaceChildren();
  if (!items.length) { emptyRow(body, 5, "No audit events yet."); return; }
  for (const item of items) {
    const row = document.createElement("tr");
    cell(row, formatDate(item.created_at));
    cell(row, `${item.actor_type}${item.actor_id ? ` / ${item.actor_id}` : ""}`);
    cell(row, item.action);
    cell(row, `${item.resource_type}${item.resource_id ? ` / ${item.resource_id}` : ""}`);
    cell(row, JSON.stringify(item.details));
    body.append(row);
  }
}

function renderProfiles(items) {
  const body = document.getElementById("profiles-table");
  body.replaceChildren();
  if (!items.length) { emptyRow(body, 7, "No profile snapshots reported yet."); return; }
  for (const item of items) {
    const row = document.createElement("tr");
    cell(row, item.nickname);
    cell(row, item.process_state || "unknown");
    cell(row, item.room_state || "unknown");
    cell(row, item.auth_state || "unknown");
    cell(row, item.client_presence || "unknown");
    cell(row, item.subscription_type);
    cell(row, formatDate(item.observed_at));
    body.append(row);
  }
}

function renderCloudBots(items) {
  const body = document.getElementById("cloud-bots-table");
  body.replaceChildren();
  if (!items.length) { emptyRow(body, 5, "No Cloud Bots registered yet."); return; }
  for (const item of items) {
    const row = document.createElement("tr");
    cell(row, item.name);
    cell(row, item.state);
    cell(row, item.reason);
    cell(row, item.uptime_seconds === null ? "—" : `${item.uptime_seconds}s`);
    cell(row, formatDate(item.observed_at));
    body.append(row);
  }
}

function renderStoreItems(items) {
  const body = document.getElementById("store-table");
  body.replaceChildren();
  if (!items.length) { emptyRow(body, 5, "No Store catalog items imported yet."); return; }
  for (const item of items) {
    const row = document.createElement("tr");
    cell(row, item.item_type);
    cell(row, item.name);
    cell(row, `${item.price_minor} ${item.currency}`);
    cell(row, item.available ? "Available" : "Unavailable");
    cell(row, formatDate(item.updated_at));
    body.append(row);
  }
}

async function load(expectedGeneration = sessionGeneration) {
  if (!accessToken) return;
  try {
    const [overview, agents, accounts, commands, audit, cloudBots, storeItems] = await Promise.all([
      api("/overview"), api("/agents"), api("/accounts"), api("/commands?limit=100"), api("/audit?limit=100"),
      api("/cloud-bots"), api("/store/catalog/items?available_only=false"),
    ]);
    const profiles = await Promise.all(accounts.map(async (account) => {
      const [profile, runtime] = await Promise.all([
        api(`/accounts/${account.id}/profile`).catch(() => ({ nickname: account.nickname })),
        api(`/accounts/${account.id}/runtime`).catch(() => ({})),
      ]);
      return {
        ...profile,
        process_state: runtime.process_state || "unknown",
        room_state: runtime.room_state || "unknown",
      };
    }));
    const cloudBotStatuses = await Promise.all(cloudBots.map(async (bot) => {
      try { return { name: bot.name, ...(await api(`/cloud-bots/${bot.id}/status`)) }; }
      catch (_) { return { name: bot.name, state: "unknown" }; }
    }));
    if (expectedGeneration !== sessionGeneration || !accessToken) return;
    renderOverview(overview);
    renderAgents(agents);
    renderAccounts(accounts);
    renderCommands(commands);
    renderAudit(audit);
    renderProfiles(profiles);
    renderCloudBots(cloudBotStatuses);
    renderStoreItems(storeItems);
    setNotice("Connected. Data refreshed from the control plane.", "success");
  } catch (error) {
    if (expectedGeneration !== sessionGeneration) return;
    setNotice(error.message, "error");
  }
}

document.getElementById("connect-button").addEventListener("click", connect);
document.getElementById("refresh-button").addEventListener("click", load);
document.getElementById("clear-token-button").addEventListener("click", () => {
  resetDashboardSession();
  tokenInput.value = "";
  setNotice("Dashboard session cleared from memory.");
});

document.getElementById("agent-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const formElement = event.currentTarget;
  const requestGeneration = sessionGeneration;
  const form = new FormData(formElement);
  try {
    const agent = await api("/agents", { method: "POST", body: JSON.stringify({ name: form.get("name") }) });
    if (requestGeneration !== sessionGeneration) return;
    const result = document.getElementById("agent-token-result");
    result.replaceChildren();
    const title = document.createElement("strong");
    title.textContent = `Copy the one-time token for ${agent.name} now.`;
    const token = document.createElement("code");
    token.textContent = agent.agent_token;
    const copy = button("Copy token", "secondary", async () => {
      await navigator.clipboard.writeText(agent.agent_token);
      setNotice("Agent token copied. It will not be shown again by the server.", "success");
    });
    result.append(title, token, copy);
    result.hidden = false;
    formElement.reset();
    await load();
  } catch (error) {
    if (requestGeneration === sessionGeneration) setNotice(error.message, "error");
  }
});

document.getElementById("account-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const formElement = event.currentTarget;
  const requestGeneration = sessionGeneration;
  const form = new FormData(formElement);
  const agentId = form.get("agent_id");
  try {
    await api("/accounts", { method: "POST", body: JSON.stringify({ nickname: form.get("nickname"), agent_id: agentId || null, enabled: true }) });
    if (requestGeneration !== sessionGeneration) return;
    formElement.reset();
    await load();
  } catch (error) {
    if (requestGeneration === sessionGeneration) setNotice(error.message, "error");
  }
});

document.getElementById("command-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const formElement = event.currentTarget;
  const requestGeneration = sessionGeneration;
  const form = new FormData(formElement);
  const operation = String(form.get("operation"));
  const target = String(form.get("target")).trim();
  if (["join_room", "add_friend", "open_im"].includes(operation) && !target) {
    setNotice("Enter the room name or nickname.", "error");
    return;
  }
  const payload = operation === "join_room" ? { room: target }
    : ["add_friend", "open_im"].includes(operation) ? { nickname: target } : {};
  try {
    await api("/commands", { method: "POST", body: JSON.stringify({ account_id: form.get("account_id"), operation, payload }) });
    if (requestGeneration !== sessionGeneration) return;
    formElement.reset();
    await load();
  } catch (error) {
    if (requestGeneration === sessionGeneration) setNotice(error.message, "error");
  }
});
