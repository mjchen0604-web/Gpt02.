const $ = (id) => document.getElementById(id);

const nodes = {
  serviceChip: $("service-chip"),
  metricService: $("metric-service"),
  metricPort: $("metric-port"),
  metricModels: $("metric-models"),
  metricAuths: $("metric-auths"),
  healthHint: $("health-hint"),
  output: $("ops-output"),
  dashboardToken: $("dashboard-token"),
  dashboardAuthHint: $("dashboard-auth-hint"),
  accounts: $("accounts-grid"),
  models: $("models-grid"),
  localConfig: $("local-config"),
  activeConfig: $("active-config"),
  logs: $("logs-box"),
  authFiles: $("auth-files"),
  authReplace: $("auth-replace"),
  toast: $("toast"),
  settingsPath: $("settings-path"),
  settingsSaveHint: $("settings-save-hint"),
  gatewaySummary: $("gateway-summary"),
  gatewayConfig: $("gateway-config"),
  gatewayStatusGrid: $("gateway-status-grid"),
  adminDbPath: $("admin-db-path"),
  adminUsers: $("admin-users-grid"),
  adminKeys: $("admin-keys-grid"),
  adminUsageUsers: $("admin-usage-users"),
  adminUsageKeys: $("admin-usage-keys"),
  adminUsageEvents: $("admin-usage-events"),
  adminUsageWindow: $("admin-usage-window"),
  adminUsagePrompt: $("admin-usage-prompt"),
  adminUsageCompletion: $("admin-usage-completion"),
  adminUsageTotal: $("admin-usage-total"),
  adminUsageCost: $("admin-usage-cost"),
  adminUserId: $("admin-user-id"),
  adminUserName: $("admin-user-name"),
  adminUserEmail: $("admin-user-email"),
  adminUserStatus: $("admin-user-status"),
  adminUserQuota: $("admin-user-quota"),
  adminUserPromptPrice: $("admin-user-prompt-price"),
  adminUserCompletionPrice: $("admin-user-completion-price"),
  adminKeyId: $("admin-key-id"),
  adminKeyUserId: $("admin-key-user-id"),
  adminKeyName: $("admin-key-name"),
  adminKeyStatus: $("admin-key-status"),
  adminKeyToken: $("admin-key-token"),
  adminKeyGroups: $("admin-key-groups"),
  adminKeyModels: $("admin-key-models"),
  adminKeyExpiresAt: $("admin-key-expires-at"),

  setRoutingStrategy: $("set-routing-strategy"),
  setRequestRetry: $("set-request-retry"),
  setMaxRetryInterval: $("set-max-retry-interval"),
  setReasoningEffort: $("set-reasoning-effort"),
  setReasoningSummary: $("set-reasoning-summary"),
  setReasoningCompat: $("set-reasoning-compat"),
  setExposeReasoningModels: $("set-expose-reasoning-models"),
  setEnableWebSearch: $("set-enable-web-search"),
  setVerbose: $("set-verbose"),
  setVerboseObfuscation: $("set-verbose-obfuscation"),
  setHttpProxy: $("set-http-proxy"),
  setHttpsProxy: $("set-https-proxy"),
  setAllProxy: $("set-all-proxy"),
  setNoProxy: $("set-no-proxy"),
  setChannelsPath: $("set-channels-path"),
};

let settingsLoaded = false;
let settingsSaveTimer = null;
let savingSettings = false;
let adminUsersCache = [];
let adminKeysCache = [];
let dashboardAuthRequired = false;

function getDashboardToken() {
  try {
    return localStorage.getItem("chatmock.dashboardToken") || "";
  } catch {
    return "";
  }
}

function setDashboardToken(token) {
  try {
    if (token) {
      localStorage.setItem("chatmock.dashboardToken", token);
    } else {
      localStorage.removeItem("chatmock.dashboardToken");
    }
  } catch {
    return;
  }
}

function updateDashboardAuthHint(text, isError = false) {
  if (!nodes.dashboardAuthHint) {
    return;
  }
  nodes.dashboardAuthHint.textContent = text;
  nodes.dashboardAuthHint.style.color = isError ? "#ffb5b5" : "";
}

async function api(path, options = {}) {
  const dashboardToken = getDashboardToken();
  const response = await fetch(path, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(dashboardToken ? { "X-Dashboard-Token": dashboardToken } : {}),
      ...(options.headers || {}),
    },
  });
  let payload = {};
  try {
    payload = await response.json();
  } catch {
    payload = {};
  }
  if (!response.ok) {
    const detailText = Array.isArray(payload.details) && payload.details.length ? `\n${payload.details.join("\n")}` : "";
    const errorMessage =
      (typeof payload.error === "string" ? payload.error : payload?.error?.message) ||
      payload.message ||
      `${response.status} ${response.statusText}`;
    if (response.status === 401 && dashboardAuthRequired) {
      updateDashboardAuthHint("dashboard auth: token missing or invalid", true);
    }
    const message = `${errorMessage}${detailText}`;
    throw new Error(message);
  }
  return payload;
}

function setOutput(text) {
  nodes.output.textContent = text || "";
}

function showToast(message, isError = false) {
  nodes.toast.textContent = message;
  nodes.toast.style.borderColor = isError ? "rgba(255,94,94,0.66)" : "rgba(97,224,200,0.55)";
  nodes.toast.classList.add("show");
  setTimeout(() => nodes.toast.classList.remove("show"), 2100);
}

function setSettingsHint(text, isError = false) {
  if (!nodes.settingsSaveHint) {
    return;
  }
  nodes.settingsSaveHint.textContent = text;
  nodes.settingsSaveHint.style.color = isError ? "#ffb5b5" : "";
}

function formatInteger(value) {
  return Number(value || 0).toLocaleString();
}

function formatMoney(value) {
  return `$${Number(value || 0).toFixed(6)}`;
}

function parseCsvList(value, fallback = []) {
  const items = String(value || "")
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
  return items.length ? items : [...fallback];
}

async function loadDashboardAuthStatus() {
  const response = await fetch("/api/dashboard/auth-status", {
    headers: { "Content-Type": "application/json" },
  });
  const payload = await response.json().catch(() => ({}));
  dashboardAuthRequired = Boolean(payload?.required);
  if (nodes.dashboardToken) {
    nodes.dashboardToken.value = getDashboardToken();
  }
  if (dashboardAuthRequired) {
    updateDashboardAuthHint(
      getDashboardToken() ? "dashboard auth: token loaded" : "dashboard auth: token required",
      !getDashboardToken()
    );
  } else {
    updateDashboardAuthHint("dashboard auth: disabled");
  }
}

function classifyServiceChip(health) {
  const serviceState = String(health?.service?.status || "").toLowerCase();
  const listening = Boolean(health?.listening);
  const hasModels = Number(health?.models?.count || 0) > 0;
  const serviceReady = ["started", "running", "active"].includes(serviceState);
  if (serviceReady && listening && hasModels) {
    return { text: "ONLINE", className: "status-chip ok" };
  }
  if (serviceState === "awaiting_auth") {
    return { text: "AUTH", className: "status-chip warn" };
  }
  if (serviceState === "external") {
    return { text: "EXTERNAL", className: "status-chip warn" };
  }
  if (serviceState === "starting") {
    return { text: "STARTING", className: "status-chip warn" };
  }
  if (serviceState === "error") {
    return { text: "ERROR", className: "status-chip bad" };
  }
  if (serviceReady) {
    return { text: "DEGRADED", className: "status-chip warn" };
  }
  return { text: "OFFLINE", className: "status-chip bad" };
}

function renderHealth(health) {
  const serviceState = health?.service?.status || "unknown";
  const listening = health?.listening ? "YES" : "NO";
  const modelCount = Number(health?.models?.count || 0);
  const authCount = Number(health?.accounts?.count || 0);
  const checkedAt = health?.now ? new Date(health.now).toLocaleString() : "-";
  nodes.metricService.textContent = serviceState;
  nodes.metricPort.textContent = listening;
  nodes.metricModels.textContent = String(modelCount);
  nodes.metricAuths.textContent = String(authCount);
  let detail = "";
  if (serviceState === "awaiting_auth") {
    detail = "waiting for uploaded auth";
  } else if (serviceState === "external") {
    detail = "using external codex app-server";
  } else if (serviceState === "starting") {
    detail = "starting codex app-server";
  } else if (serviceState === "running" && health?.service?.url) {
    detail = String(health.service.url);
  }
  if (health?.gateway?.enabled) {
    const gatewayText = `gateway ${Number(health.gateway.channels || 0)}ch / ${Number(health.gateway.apiKeys || 0)}keys`;
    detail = detail ? `${detail} · ${gatewayText}` : gatewayText;
  }
  nodes.healthHint.textContent = detail
    ? `status checked: ${checkedAt} · ${detail}`
    : `status checked: ${checkedAt}`;
  const chip = classifyServiceChip(health);
  nodes.serviceChip.textContent = chip.text;
  nodes.serviceChip.className = chip.className;
}

function tokenPill(label, ok) {
  const cls = ok ? "token-pill ok" : "token-pill no";
  return `<span class="${cls}">${label}:${ok ? "Y" : "N"}</span>`;
}

function renderAccounts(payload) {
  const accounts = Array.isArray(payload?.accounts) ? payload.accounts : [];
  if (!accounts.length) {
    nodes.accounts.innerHTML = `<div class="account-card">No accounts uploaded</div>`;
    return;
  }
  nodes.accounts.innerHTML = accounts
    .map((acc) => {
      if (acc.error) {
        return `
          <article class="account-card">
            <div class="account-top">
              <div>
                <div class="account-file">${acc.label || "unknown"}</div>
                <div class="account-mail">Failed to read account</div>
              </div>
            </div>
            <div class="account-meta"><div>${acc.error}</div></div>
          </article>
        `;
      }
      return `
        <article class="account-card">
          <div class="account-top">
            <div>
              <div class="account-file">${acc.label || "-"}</div>
              <div class="account-mail">${acc.source || "-"}</div>
            </div>
            <div class="account-file">status: ${acc.last_status ?? "-"}</div>
          </div>
          <div class="account-meta">
            <div>account: ${acc.account_id || "-"}</div>
            <div>refresh: ${acc.last_refresh || "-"}</div>
            <div>failures: ${acc.failures || 0}</div>
            <div>cooldown: ${acc.cooldown_remaining || 0}s</div>
            <div>fast: ${acc.fast_status || "-"} ${acc.fast_port ? `@${acc.fast_port}` : ""}</div>
            <div>fast requests: ${acc.fast_request_successes || 0}/${acc.fast_request_count || 0}</div>
          </div>
          ${tokenPill("access", Boolean(acc.has_access_token))}
          ${tokenPill("refresh", Boolean(acc.has_refresh_token))}
          ${tokenPill("id", Boolean(acc.has_id_token))}
        </article>
      `;
    })
    .join("");
}

function renderModels(payload) {
  const ids = Array.isArray(payload?.ids) ? payload.ids : [];
  if (!ids.length) {
    nodes.models.innerHTML = `<span class="model-chip">No model data</span>`;
    return;
  }
  nodes.models.innerHTML = ids.map((id) => `<span class="model-chip">${id}</span>`).join("");
}

function renderConfig(payload) {
  nodes.localConfig.textContent = payload?.localConfig || "Failed to read";
  nodes.activeConfig.textContent = payload?.activeConfig || "Failed to read";
}

function renderLogs(payload) {
  nodes.logs.textContent = payload?.text || "Failed to read";
}

function readSettingsForm() {
  return {
    routingStrategy: nodes.setRoutingStrategy?.value || "round-robin",
    requestRetry: Number(nodes.setRequestRetry?.value || 0),
    maxRetryInterval: Number(nodes.setMaxRetryInterval?.value || 30),
    reasoningEffort: nodes.setReasoningEffort?.value || "medium",
    reasoningSummary: nodes.setReasoningSummary?.value || "auto",
    reasoningCompat: nodes.setReasoningCompat?.value || "think-tags",
    exposeReasoningModels: Boolean(nodes.setExposeReasoningModels?.checked),
    enableWebSearch: Boolean(nodes.setEnableWebSearch?.checked),
    verbose: Boolean(nodes.setVerbose?.checked),
    verboseObfuscation: Boolean(nodes.setVerboseObfuscation?.checked),
    httpProxy: nodes.setHttpProxy?.value || "",
    httpsProxy: nodes.setHttpsProxy?.value || "",
    allProxy: nodes.setAllProxy?.value || "",
    noProxy: nodes.setNoProxy?.value || "",
    uploadReplaceDefault: Boolean(nodes.authReplace?.checked),
    channelsPath: nodes.setChannelsPath?.value || "",
  };
}

function applySettingsForm(settings = {}) {
  if (nodes.setRoutingStrategy) {
    nodes.setRoutingStrategy.value = settings.routingStrategy || "round-robin";
  }
  if (nodes.setRequestRetry) {
    nodes.setRequestRetry.value = String(settings.requestRetry ?? 3);
  }
  if (nodes.setMaxRetryInterval) {
    nodes.setMaxRetryInterval.value = String(settings.maxRetryInterval ?? 30);
  }
  if (nodes.setReasoningEffort) {
    nodes.setReasoningEffort.value = settings.reasoningEffort || "medium";
  }
  if (nodes.setReasoningSummary) {
    nodes.setReasoningSummary.value = settings.reasoningSummary || "auto";
  }
  if (nodes.setReasoningCompat) {
    nodes.setReasoningCompat.value = settings.reasoningCompat || "think-tags";
  }
  if (nodes.setExposeReasoningModels) {
    nodes.setExposeReasoningModels.checked = Boolean(settings.exposeReasoningModels);
  }
  if (nodes.setEnableWebSearch) {
    nodes.setEnableWebSearch.checked = Boolean(settings.enableWebSearch);
  }
  if (nodes.setVerbose) {
    nodes.setVerbose.checked = Boolean(settings.verbose);
  }
  if (nodes.setVerboseObfuscation) {
    nodes.setVerboseObfuscation.checked = Boolean(settings.verboseObfuscation);
  }
  if (nodes.setHttpProxy) {
    nodes.setHttpProxy.value = settings.httpProxy || "";
  }
  if (nodes.setHttpsProxy) {
    nodes.setHttpsProxy.value = settings.httpsProxy || "";
  }
  if (nodes.setAllProxy) {
    nodes.setAllProxy.value = settings.allProxy || "";
  }
  if (nodes.setNoProxy) {
    nodes.setNoProxy.value = settings.noProxy || "";
  }
  if (nodes.setChannelsPath) {
    nodes.setChannelsPath.value = settings.channelsPath || "";
  }
  if (nodes.authReplace) {
    nodes.authReplace.checked = Boolean(settings.uploadReplaceDefault);
  }
}

function renderGatewayConfig(payload = {}) {
  const summary = payload?.summary || {};
  const existsText = payload?.exists ? "file exists" : "file will be created on save";
  const channels = Array.isArray(summary?.channels) ? summary.channels.length : 0;
  const apiKeys = Array.isArray(summary?.api_keys) ? summary.api_keys.length : 0;
  const validationErrors = Array.isArray(payload?.validationErrors) ? payload.validationErrors : [];
  if (nodes.setChannelsPath && payload?.path) {
    nodes.setChannelsPath.value = payload.path;
  }
  if (nodes.gatewayConfig) {
    nodes.gatewayConfig.value = payload?.config || '{\n  "api_keys": [],\n  "channels": []\n}';
  }
  if (nodes.gatewaySummary) {
    const validationText = validationErrors.length ? ` · invalid=${validationErrors.length}` : " · config valid";
    nodes.gatewaySummary.textContent = `${existsText} · channels=${channels} · api_keys=${apiKeys}${validationText}`;
  }
  if (validationErrors.length) {
    setOutput(`[gateway config validation]\\n${validationErrors.join("\\n")}`);
  }
}

function renderGatewayStatus(payload = {}) {
  const channels = Array.isArray(payload?.channels) ? payload.channels : [];
  if (!nodes.gatewayStatusGrid) {
    return;
  }
  if (!channels.length) {
    nodes.gatewayStatusGrid.innerHTML = `<div class="gateway-card">No gateway channels loaded</div>`;
    return;
  }
  nodes.gatewayStatusGrid.innerHTML = channels
    .map((channel) => {
      const healthy = Boolean(channel?.healthy) && Boolean(channel?.enabled);
      const disabled = !Boolean(channel?.enabled);
      const cardClass = disabled ? "gateway-card warn" : healthy ? "gateway-card" : "gateway-card bad";
      const stateText = disabled ? "DISABLED" : healthy ? "READY" : "COOLDOWN";
      const groups = Array.isArray(channel?.groups) ? channel.groups.join(", ") : "-";
      const models = Array.isArray(channel?.models) ? channel.models.join(", ") : "-";
      const families = Array.isArray(channel?.routeFamilies) ? channel.routeFamilies.join(", ") : "-";
      const lastError = channel?.lastError ? `<div class="gateway-card-error">${channel.lastError}</div>` : "";
      return `
        <article class="${cardClass}">
          <div class="gateway-card-head">
            <div>
              <div class="gateway-card-id">${channel?.id || "-"}</div>
              <div class="gateway-card-transport">${channel?.transport || "-"} · ${stateText}</div>
            </div>
            <span class="status-chip ${healthy ? "ok" : disabled ? "warn" : "bad"}">${stateText}</span>
          </div>
          <div class="gateway-card-meta">
            <div>priority: ${channel?.priority ?? 0}</div>
            <div>weight: ${channel?.weight ?? 1}</div>
            <div>groups: ${groups || "-"}</div>
            <div>families: ${families || "-"}</div>
            <div>cooldown: ${channel?.cooldownRemaining ?? 0}s</div>
            <div>failures: ${channel?.failures ?? 0}</div>
            <div>last status: ${channel?.lastStatus ?? "-"}</div>
            <div>auth: ${channel?.authLabel || "-"}</div>
            <div>models: ${models || "-"}</div>
            <div>timeout: ${channel?.timeoutSeconds ?? 600}s</div>
          </div>
          ${lastError}
        </article>
      `;
    })
    .join("");
}

function syncKeyUserOptions() {
  if (!nodes.adminKeyUserId) {
    return;
  }
  const currentValue = String(nodes.adminKeyUserId.value || "");
  if (!adminUsersCache.length) {
    nodes.adminKeyUserId.innerHTML = `<option value="">No users</option>`;
    return;
  }
  nodes.adminKeyUserId.innerHTML = adminUsersCache
    .map((user) => {
      const selected = String(user.id) === currentValue ? " selected" : "";
      return `<option value="${user.id}"${selected}>#${user.id} ${user.name}</option>`;
    })
    .join("");
  if (!nodes.adminKeyUserId.value) {
    nodes.adminKeyUserId.value = String(adminUsersCache[0]?.id || "");
  }
}

function fillUserForm(user = {}) {
  if (nodes.adminUserId) {
    nodes.adminUserId.value = String(user.id || "");
  }
  if (nodes.adminUserName) {
    nodes.adminUserName.value = user.name || "";
  }
  if (nodes.adminUserEmail) {
    nodes.adminUserEmail.value = user.email || "";
  }
  if (nodes.adminUserStatus) {
    nodes.adminUserStatus.value = user.status || "active";
  }
  if (nodes.adminUserQuota) {
    nodes.adminUserQuota.value = String(user.monthlyQuotaTokens ?? 0);
  }
  if (nodes.adminUserPromptPrice) {
    nodes.adminUserPromptPrice.value = String(user.promptPricePerMillion ?? 0);
  }
  if (nodes.adminUserCompletionPrice) {
    nodes.adminUserCompletionPrice.value = String(user.completionPricePerMillion ?? 0);
  }
}

function readUserForm() {
  return {
    id: Number(nodes.adminUserId?.value || 0),
    name: nodes.adminUserName?.value || "",
    email: nodes.adminUserEmail?.value || "",
    status: nodes.adminUserStatus?.value || "active",
    monthlyQuotaTokens: Number(nodes.adminUserQuota?.value || 0),
    promptPricePerMillion: Number(nodes.adminUserPromptPrice?.value || 0),
    completionPricePerMillion: Number(nodes.adminUserCompletionPrice?.value || 0),
  };
}

function renderAdminUsers(payload = {}) {
  const users = Array.isArray(payload?.users) ? payload.users : [];
  adminUsersCache = users;
  if (nodes.adminDbPath && payload?.dbPath) {
    nodes.adminDbPath.textContent = payload.dbPath;
  }
  syncKeyUserOptions();
  if (!nodes.adminUsers) {
    return;
  }
  if (!users.length) {
    nodes.adminUsers.innerHTML = `<div class="admin-card">No managed users</div>`;
    return;
  }
  nodes.adminUsers.innerHTML = users
    .map(
      (user) => `
        <article class="admin-card">
          <div class="admin-card-head">
            <div>
              <div class="admin-card-title">#${user.id} ${user.name}</div>
              <div class="admin-card-subtle">${user.email || "no email"} · ${user.status}</div>
            </div>
            <span class="status-chip ${user.status === "active" ? "ok" : "warn"}">${String(user.status || "").toUpperCase()}</span>
          </div>
          <div class="admin-card-meta">
            <div>quota: ${formatInteger(user.monthlyQuotaTokens)}</div>
            <div>keys: ${formatInteger(user.keyCount)}</div>
            <div>used this month: ${formatInteger(user.usedTokensMonth)}</div>
            <div>cost this month: ${formatMoney(user.estimatedCostMonth)}</div>
            <div>prompt / 1M: ${formatMoney(user.promptPricePerMillion)}</div>
            <div>completion / 1M: ${formatMoney(user.completionPricePerMillion)}</div>
          </div>
          <div class="admin-inline">
            <button class="btn" data-action="edit-user" data-user-id="${user.id}">Edit</button>
            <button class="btn btn-danger" data-action="delete-user" data-user-id="${user.id}">Delete</button>
          </div>
        </article>
      `
    )
    .join("");
}

function fillKeyForm(item = {}) {
  if (nodes.adminKeyId) {
    nodes.adminKeyId.value = String(item.id || "");
  }
  if (nodes.adminKeyUserId) {
    nodes.adminKeyUserId.value = String(item.userId || nodes.adminKeyUserId.value || "");
  }
  if (nodes.adminKeyName) {
    nodes.adminKeyName.value = item.name || "";
  }
  if (nodes.adminKeyStatus) {
    nodes.adminKeyStatus.value = item.status || "active";
  }
  if (nodes.adminKeyToken) {
    nodes.adminKeyToken.value = "";
  }
  if (nodes.adminKeyGroups) {
    nodes.adminKeyGroups.value = Array.isArray(item.groups) ? item.groups.join(",") : "default";
  }
  if (nodes.adminKeyModels) {
    nodes.adminKeyModels.value = Array.isArray(item.models) ? item.models.join(",") : "*";
  }
  if (nodes.adminKeyExpiresAt) {
    nodes.adminKeyExpiresAt.value = item.expiresAt || "";
  }
}

function readKeyForm() {
  return {
    id: Number(nodes.adminKeyId?.value || 0),
    userId: Number(nodes.adminKeyUserId?.value || 0),
    name: nodes.adminKeyName?.value || "",
    status: nodes.adminKeyStatus?.value || "active",
    token: nodes.adminKeyToken?.value || "",
    groups: parseCsvList(nodes.adminKeyGroups?.value || "", ["default"]),
    models: parseCsvList(nodes.adminKeyModels?.value || "", ["*"]),
    expiresAt: nodes.adminKeyExpiresAt?.value || "",
  };
}

function renderAdminKeys(payload = {}) {
  const keys = Array.isArray(payload?.keys) ? payload.keys : [];
  adminKeysCache = keys;
  if (nodes.adminDbPath && payload?.dbPath) {
    nodes.adminDbPath.textContent = payload.dbPath;
  }
  if (!nodes.adminKeys) {
    return;
  }
  if (!keys.length) {
    nodes.adminKeys.innerHTML = `<div class="admin-card">No managed API keys</div>`;
    return;
  }
  nodes.adminKeys.innerHTML = keys
    .map(
      (item) => `
        <article class="admin-card">
          <div class="admin-card-head">
            <div>
              <div class="admin-card-title">#${item.id} ${item.name}</div>
              <div class="admin-card-subtle">${item.maskedToken || "-"} · ${item.userName || "-"}</div>
            </div>
            <span class="status-chip ${item.status === "active" ? "ok" : "warn"}">${String(item.status || "").toUpperCase()}</span>
          </div>
          <div class="admin-card-meta">
            <div>user: ${item.userName || "-"}</div>
            <div>user status: ${item.userStatus || "-"}</div>
            <div>groups: ${(item.groups || []).join(", ") || "-"}</div>
            <div>models: ${(item.models || []).join(", ") || "-"}</div>
            <div>expires: ${item.expiresAt || "-"}</div>
            <div>last used: ${item.lastUsedAt || "-"}</div>
            <div>used this month: ${formatInteger(item.usedTokensMonth)}</div>
            <div>cost this month: ${formatMoney(item.estimatedCostMonth)}</div>
          </div>
          <div class="admin-inline">
            <button class="btn" data-action="edit-key" data-key-id="${item.id}">Edit</button>
            <button class="btn ${item.status === "active" ? "btn-danger" : ""}" data-action="toggle-key" data-key-id="${item.id}" data-next-status="${item.status === "active" ? "disabled" : "active"}">
              ${item.status === "active" ? "Disable" : "Enable"}
            </button>
            <button class="btn btn-danger" data-action="delete-key" data-key-id="${item.id}">Delete</button>
          </div>
        </article>
      `
    )
    .join("");
}

function renderAdminUsage(payload = {}) {
  const totals = payload?.totals || {};
  const byUser = Array.isArray(payload?.byUser) ? payload.byUser : [];
  const byKey = Array.isArray(payload?.byKey) ? payload.byKey : [];
  const events = Array.isArray(payload?.events) ? payload.events : [];
  if (nodes.adminDbPath && payload?.dbPath) {
    nodes.adminDbPath.textContent = payload.dbPath;
  }
  if (nodes.adminUsageWindow) {
    nodes.adminUsageWindow.textContent = `统计窗口：${payload.monthStart || "-"} -> ${payload.monthEnd || "-"}`;
  }
  if (nodes.adminUsagePrompt) {
    nodes.adminUsagePrompt.textContent = formatInteger(totals.promptTokens);
  }
  if (nodes.adminUsageCompletion) {
    nodes.adminUsageCompletion.textContent = formatInteger(totals.completionTokens);
  }
  if (nodes.adminUsageTotal) {
    nodes.adminUsageTotal.textContent = formatInteger(totals.totalTokens);
  }
  if (nodes.adminUsageCost) {
    nodes.adminUsageCost.textContent = formatMoney(totals.estimatedCost);
  }
  if (nodes.adminUsageUsers) {
    nodes.adminUsageUsers.innerHTML = byUser.length
      ? byUser
          .map(
            (item) => `
              <article class="admin-card">
                <div class="admin-card-title">${item.userName || "-"}</div>
                <div class="admin-card-meta">
                  <div>tokens: ${formatInteger(item.totalTokens)}</div>
                  <div>cost: ${formatMoney(item.estimatedCost)}</div>
                </div>
              </article>
            `
          )
          .join("")
      : `<div class="admin-card">No usage yet</div>`;
  }
  if (nodes.adminUsageKeys) {
    nodes.adminUsageKeys.innerHTML = byKey.length
      ? byKey
          .map(
            (item) => `
              <article class="admin-card">
                <div class="admin-card-title">${item.apiKeyName || "-"}</div>
                <div class="admin-card-subtle">${item.userName || "-"}</div>
                <div class="admin-card-meta">
                  <div>tokens: ${formatInteger(item.totalTokens)}</div>
                  <div>cost: ${formatMoney(item.estimatedCost)}</div>
                </div>
              </article>
            `
          )
          .join("")
      : `<div class="admin-card">No key usage yet</div>`;
  }
  if (nodes.adminUsageEvents) {
    nodes.adminUsageEvents.textContent = events.length
      ? events
          .map(
            (event) =>
              `[${event.createdAt}] ${event.userName}/${event.apiKeyName} ${event.endpoint} ${event.model} tokens=${event.totalTokens} cost=${formatMoney(event.estimatedCost)} status=${event.statusCode} channel=${event.channelId || "-"}`
          )
          .join("\n")
      : "No usage events";
  }
}

async function loadAdminUsers() {
  const payload = await api("/api/admin/users");
  renderAdminUsers(payload);
}

async function loadAdminKeys() {
  const payload = await api("/api/admin/keys");
  renderAdminKeys(payload);
}

async function loadAdminUsage() {
  const payload = await api("/api/admin/usage?limit=200");
  renderAdminUsage(payload);
}

async function refreshAdmin() {
  const results = await Promise.allSettled([loadAdminUsers(), loadAdminKeys(), loadAdminUsage()]);
  const rejected = results.filter((item) => item.status === "rejected");
  if (rejected.length) {
    throw rejected[0].reason;
  }
}

async function saveUser() {
  try {
    const payload = await api("/api/admin/users", {
      method: "POST",
      body: JSON.stringify(readUserForm()),
    });
    fillUserForm(payload?.user || {});
    await refreshAdmin();
    setOutput(`[user saved]\n#${payload?.user?.id || "-"} ${payload?.user?.name || ""}`);
    showToast("User saved");
  } catch (error) {
    setOutput(String(error.message || error));
    showToast("User save failed", true);
  }
}

async function saveApiKey() {
  try {
    const form = readKeyForm();
    const isUpdate = Number(form.id || 0) > 0;
    const path = isUpdate ? `/api/admin/keys/${form.id}` : "/api/admin/keys";
    const payload = await api(path, {
      method: "POST",
      body: JSON.stringify(form),
    });
    fillKeyForm(payload?.apiKey || {});
    await refreshAdmin();
    const createdToken = payload?.apiKey?.token ? `\ntoken=${payload.apiKey.token}` : "";
    setOutput(`[api key saved]\n#${payload?.apiKey?.id || "-"} ${payload?.apiKey?.name || ""}${createdToken}`);
    showToast("API key saved");
  } catch (error) {
    setOutput(String(error.message || error));
    showToast("API key save failed", true);
  }
}

async function toggleApiKey(keyId, nextStatus) {
  try {
    await api(`/api/admin/keys/${keyId}`, {
      method: "POST",
      body: JSON.stringify({ status: nextStatus }),
    });
    await refreshAdmin();
    showToast(`API key ${nextStatus}`);
  } catch (error) {
    setOutput(String(error.message || error));
    showToast("API key update failed", true);
  }
}

async function deleteUser(userId) {
  if (!window.confirm(`Delete user #${userId}? This also removes linked API keys and usage events.`)) {
    return;
  }
  try {
    await api(`/api/admin/users/${userId}`, { method: "DELETE" });
    if (Number(nodes.adminUserId?.value || 0) === Number(userId)) {
      fillUserForm({ status: "active" });
    }
    await refreshAdmin();
    setOutput(`[user deleted]\n#${userId}`);
    showToast("User deleted");
  } catch (error) {
    setOutput(String(error.message || error));
    showToast("User delete failed", true);
  }
}

async function deleteApiKey(keyId) {
  if (!window.confirm(`Delete API key #${keyId}?`)) {
    return;
  }
  try {
    await api(`/api/admin/keys/${keyId}`, { method: "DELETE" });
    if (Number(nodes.adminKeyId?.value || 0) === Number(keyId)) {
      fillKeyForm({ status: "active", groups: ["default"], models: ["*"] });
    }
    await refreshAdmin();
    setOutput(`[api key deleted]\n#${keyId}`);
    showToast("API key deleted");
  } catch (error) {
    setOutput(String(error.message || error));
    showToast("API key delete failed", true);
  }
}

async function loadSettings() {
  const payload = await api("/api/settings");
  applySettingsForm(payload?.settings || {});
  if (nodes.settingsPath) {
    nodes.settingsPath.textContent = payload?.settingsPath || "-";
  }
  setSettingsHint("autosave: loaded");
}

async function loadGatewayConfig() {
  const payload = await api("/api/gateway/config");
  renderGatewayConfig(payload);
}

async function loadGatewayStatus() {
  const payload = await api("/api/gateway/status");
  renderGatewayStatus(payload);
}

async function saveSettings(showOkToast = false) {
  if (!settingsLoaded || savingSettings) {
    return;
  }
  savingSettings = true;
  setSettingsHint("autosave: saving...");
  try {
    const payload = await api("/api/settings", {
      method: "POST",
      body: JSON.stringify(readSettingsForm()),
    });
    if (nodes.settingsPath) {
      nodes.settingsPath.textContent = payload?.settingsPath || "-";
    }
    setSettingsHint("autosave: saved");
    if (showOkToast) {
      showToast("Settings saved");
    }
  } catch (error) {
    setOutput(String(error.message || error));
    setSettingsHint("autosave: failed", true);
    showToast("Failed to save settings", true);
  } finally {
    savingSettings = false;
  }
}

function scheduleSettingsSave() {
  if (!settingsLoaded) {
    return;
  }
  if (settingsSaveTimer) {
    clearTimeout(settingsSaveTimer);
  }
  setSettingsHint("autosave: waiting...");
  settingsSaveTimer = setTimeout(() => {
    saveSettings(false);
  }, 450);
}

function bindSettingsAutosave() {
  const controls = [
    nodes.setRoutingStrategy,
    nodes.setRequestRetry,
    nodes.setMaxRetryInterval,
    nodes.setReasoningEffort,
    nodes.setReasoningSummary,
    nodes.setReasoningCompat,
    nodes.setExposeReasoningModels,
    nodes.setEnableWebSearch,
    nodes.setVerbose,
    nodes.setVerboseObfuscation,
    nodes.setHttpProxy,
    nodes.setHttpsProxy,
    nodes.setAllProxy,
    nodes.setNoProxy,
    nodes.setChannelsPath,
    nodes.authReplace,
  ].filter(Boolean);

  controls.forEach((node) => {
    node.addEventListener("change", scheduleSettingsSave);
    if (node.tagName === "INPUT" && node.type !== "checkbox") {
      node.addEventListener("input", scheduleSettingsSave);
    }
  });
}

async function refreshHealth() {
  const health = await api("/api/health");
  renderHealth(health);
  if (health?.models?.error) {
    setOutput(`Model check failed: ${health.models.error}`);
  }
}

async function refreshAccounts() {
  const payload = await api("/api/accounts");
  renderAccounts(payload);
}

async function refreshModels() {
  const payload = await api("/api/models");
  renderModels(payload);
}

async function refreshConfig() {
  const payload = await api("/api/config");
  renderConfig(payload);
}

async function refreshLogs() {
  const payload = await api("/api/logs?lines=180");
  renderLogs(payload);
}

async function refreshAll() {
  const tasks = [refreshHealth(), refreshAccounts(), refreshModels(), refreshConfig(), refreshLogs(), loadGatewayStatus(), refreshAdmin()];
  const results = await Promise.allSettled(tasks);
  const rejected = results.filter((item) => item.status === "rejected");
  if (rejected.length) {
    const message = rejected[0]?.reason?.message || "Partial refresh failed";
    showToast(message, true);
  }
}

async function saveGatewayConfig() {
  if (!nodes.gatewayConfig) {
    return;
  }
  let parsedConfig;
  try {
    parsedConfig = JSON.parse(nodes.gatewayConfig.value || "{}");
  } catch (error) {
    setOutput(String(error.message || error));
    showToast("Gateway JSON invalid", true);
    return;
  }

  try {
    const payload = await api("/api/gateway/config", {
      method: "POST",
      body: JSON.stringify({
        path: nodes.setChannelsPath?.value || "",
        config: parsedConfig,
      }),
    });
    if (nodes.settingsPath && payload?.settingsPath) {
      nodes.settingsPath.textContent = payload.settingsPath;
    }
    await loadGatewayConfig();
    await loadGatewayStatus();
    await refreshHealth();
    await refreshModels();
    await refreshConfig();
    showToast("Gateway config saved");
  } catch (error) {
    setOutput(String(error.message || error));
    showToast("Gateway save failed", true);
  }
}

async function runSync() {
  try {
    const payload = await api("/api/actions/sync", { method: "POST" });
    setOutput((payload.stdout || "").trim() || "sync done");
    if (payload.health) {
      renderHealth(payload.health);
    }
    await refreshAccounts();
    showToast("Accounts synced");
  } catch (error) {
    setOutput(String(error.message || error));
    showToast("Sync failed", true);
  }
}

async function runServiceAction(action) {
  try {
    const payload = await api("/api/actions/service", {
      method: "POST",
      body: JSON.stringify({ action }),
    });
    const report = [`[service ${action}]`, String(payload.stdout || "").trim(), String(payload.stderr || "").trim()]
      .filter(Boolean)
      .join("\n");
    setOutput(report || `${action} done`);
    if (payload.health) {
      renderHealth(payload.health);
    }
    await refreshModels();
    showToast(`Service action completed: ${action}`);
  } catch (error) {
    setOutput(String(error.message || error));
    showToast(`Service action failed: ${action}`, true);
  }
}

async function uploadAuthFiles() {
  const fileInput = nodes.authFiles;
  if (!fileInput || !fileInput.files || fileInput.files.length === 0) {
    showToast("Please select one or more auth.json files", true);
    return;
  }

  const formData = new FormData();
  Array.from(fileInput.files).forEach((file) => formData.append("files", file));
  formData.append("replace", nodes.authReplace?.checked ? "1" : "0");

  try {
    const dashboardToken = getDashboardToken();
    const response = await fetch("/api/actions/upload_auths", {
      method: "POST",
      body: formData,
      headers: dashboardToken ? { "X-Dashboard-Token": dashboardToken } : {},
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      const message = payload.error || payload.message || `${response.status} ${response.statusText}`;
      throw new Error(message);
    }

    const lines = [
      `[upload] uploaded=${payload.uploaded || 0} created=${payload.created || 0} updated=${payload.updated || 0}`,
      Number(payload?.service?.status?.instanceCount || 0) > 0
        ? `fast instances: ${payload.service.status.instanceCount} active=${payload.service.status.activeCount || 0}`
        : "",
      ...(Array.isArray(payload.results)
        ? payload.results.map((item) => {
            const action = item?.action || "created";
            const accountId = item?.accountId || "unknown";
            const target = item?.target || "";
            if (action === "updated") {
              return `[updated] ${item?.filename || "unknown"} -> ${target} (same account_id: ${accountId})`;
            }
            return `[created] ${item?.filename || "unknown"} -> ${target} (account_id: ${accountId})`;
          })
        : Array.isArray(payload.written)
          ? payload.written
          : []),
      ...(Array.isArray(payload.errors) && payload.errors.length ? ["errors:", ...payload.errors] : []),
    ];
    setOutput(lines.join("\n"));
    fileInput.value = "";

    if (payload.savedSettings) {
      applySettingsForm(payload.savedSettings);
    }
    if (nodes.settingsPath && payload.settingsPath) {
      nodes.settingsPath.textContent = payload.settingsPath;
    }

    await refreshAll();
    showToast("Credentials uploaded");
  } catch (error) {
    setOutput(String(error.message || error));
    showToast("Upload failed", true);
  }
}

function bindActions() {
  $("btn-refresh").addEventListener("click", async () => {
    await refreshAll();
    showToast("Refresh complete");
  });
  $("btn-sync").addEventListener("click", runSync);
  $("btn-restart").addEventListener("click", () => runServiceAction("restart"));
  $("btn-start").addEventListener("click", () => runServiceAction("start"));
  $("btn-stop").addEventListener("click", () => runServiceAction("stop"));
  $("btn-models").addEventListener("click", refreshModels);
  $("btn-logs").addEventListener("click", refreshLogs);
  $("btn-config").addEventListener("click", refreshConfig);
  $("btn-gateway-reload").addEventListener("click", loadGatewayConfig);
  $("btn-gateway-save").addEventListener("click", saveGatewayConfig);
  $("btn-gateway-status").addEventListener("click", loadGatewayStatus);
  $("btn-admin-refresh").addEventListener("click", async () => {
    try {
      await refreshAdmin();
      showToast("Control plane refreshed");
    } catch (error) {
      setOutput(String(error.message || error));
      showToast("Control plane refresh failed", true);
    }
  });
  $("btn-user-save").addEventListener("click", saveUser);
  $("btn-user-new").addEventListener("click", () => fillUserForm({ status: "active" }));
  $("btn-key-save").addEventListener("click", saveApiKey);
  $("btn-key-new").addEventListener("click", () => fillKeyForm({ status: "active", groups: ["default"], models: ["*"] }));
  $("btn-dashboard-token-save").addEventListener("click", async () => {
    const token = nodes.dashboardToken?.value || "";
    setDashboardToken(token.trim());
    updateDashboardAuthHint(token.trim() ? "dashboard auth: token saved" : "dashboard auth: token cleared", !token.trim() && dashboardAuthRequired);
    try {
      await Promise.allSettled([loadSettings(), refreshAll(), loadGatewayConfig(), loadGatewayStatus()]);
      settingsLoaded = true;
      setSettingsHint("autosave: enabled");
      showToast("Dashboard token applied");
    } catch (error) {
      setOutput(String(error.message || error));
      showToast("Dashboard token rejected", true);
    }
  });
  $("btn-dashboard-token-clear").addEventListener("click", () => {
    setDashboardToken("");
    if (nodes.dashboardToken) {
      nodes.dashboardToken.value = "";
    }
    updateDashboardAuthHint(dashboardAuthRequired ? "dashboard auth: token required" : "dashboard auth: disabled", dashboardAuthRequired);
    showToast("Dashboard token cleared");
  });

  if (nodes.adminUsers) {
    nodes.adminUsers.addEventListener("click", async (event) => {
      const target = event.target.closest("[data-action='edit-user']");
      if (target) {
        const userId = Number(target.dataset.userId || 0);
        const user = adminUsersCache.find((item) => Number(item.id) === userId);
        if (user) {
          fillUserForm(user);
        }
        return;
      }
      const deleteTarget = event.target.closest("[data-action='delete-user']");
      if (deleteTarget) {
        const userId = Number(deleteTarget.dataset.userId || 0);
        await deleteUser(userId);
      }
    });
  }

  if (nodes.adminKeys) {
    nodes.adminKeys.addEventListener("click", async (event) => {
      const editTarget = event.target.closest("[data-action='edit-key']");
      if (editTarget) {
        const keyId = Number(editTarget.dataset.keyId || 0);
        const item = adminKeysCache.find((entry) => Number(entry.id) === keyId);
        if (item) {
          fillKeyForm(item);
        }
        return;
      }
      const toggleTarget = event.target.closest("[data-action='toggle-key']");
      if (toggleTarget) {
        const keyId = Number(toggleTarget.dataset.keyId || 0);
        const nextStatus = toggleTarget.dataset.nextStatus || "disabled";
        await toggleApiKey(keyId, nextStatus);
        return;
      }
      const deleteTarget = event.target.closest("[data-action='delete-key']");
      if (deleteTarget) {
        const keyId = Number(deleteTarget.dataset.keyId || 0);
        await deleteApiKey(keyId);
      }
    });
  }

  const uploadBtn = $("btn-upload-auth");
  if (uploadBtn) {
    uploadBtn.addEventListener("click", uploadAuthFiles);
  }
}

async function init() {
  bindActions();
  bindSettingsAutosave();
  fillUserForm({ status: "active" });
  fillKeyForm({ status: "active", groups: ["default"], models: ["*"] });
  await loadDashboardAuthStatus();
  if (dashboardAuthRequired && !getDashboardToken()) {
    settingsLoaded = false;
    setSettingsHint("autosave: waiting for dashboard token", true);
    return;
  }

  await Promise.allSettled([loadSettings(), refreshAll(), loadGatewayConfig(), loadGatewayStatus()]);
  settingsLoaded = true;
  setSettingsHint("autosave: enabled");
  setInterval(refreshHealth, 15000);
}

init();
