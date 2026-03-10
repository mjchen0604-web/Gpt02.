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
const sidebarLinks = Array.from(document.querySelectorAll(".sidebar-link"));

const BACKEND_MESSAGE_MAP = {
  "dashboard admin token required": "后台管理员口令必填",
  "dashboard admin token invalid": "后台管理员口令无效",
  "Missing gateway API key": "缺少网关 API Key",
  "invalid gateway api key": "网关 API Key 无效",
  "anonymous gateway access disabled": "已禁用匿名网关访问",
  "No gateway channels available": "当前没有可用渠道",
  "Missing ChatGPT credentials": "缺少 ChatGPT 凭据",
  "Gateway config validation failed": "渠道配置校验失败",
};

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

function localizeMessage(message) {
  if (typeof message !== "string") {
    return message;
  }
  return BACKEND_MESSAGE_MAP[message] || message;
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
    const localizedDetails =
      Array.isArray(payload.details) && payload.details.length ? payload.details.map((detail) => localizeMessage(detail)) : [];
    const detailText = localizedDetails.length ? `\n${localizedDetails.join("\n")}` : "";
    const rawErrorMessage =
      (typeof payload.error === "string" ? payload.error : payload?.error?.message) || payload.message || "";
    const errorMessage =
      localizeMessage(rawErrorMessage) ||
      `${response.status} ${response.statusText}`;
    if (response.status === 401 && dashboardAuthRequired) {
      updateDashboardAuthHint("后台鉴权：口令缺失或无效", true);
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
  return `US$${Number(value || 0).toFixed(6)}`;
}

function formatQuota(value) {
  return Number(value || 0) > 0 ? formatInteger(value) : "不限";
}

function formatDashboardAuthState(required, hasToken) {
  if (!required) {
    return "后台鉴权：已关闭";
  }
  return hasToken ? "后台鉴权：已载入口令" : "后台鉴权：需要管理员口令";
}

function formatServiceState(value) {
  const state = String(value || "").toLowerCase();
  if (!state) {
    return "未知";
  }
  const stateMap = {
    running: "运行中",
    started: "运行中",
    active: "运行中",
    awaiting_auth: "等待凭据",
    external: "外部模式",
    starting: "启动中",
    error: "异常",
    disabled: "已停用",
    stopped: "已停止",
    unknown: "未知",
  };
  return stateMap[state] || value;
}

function formatBooleanState(value, trueLabel = "是", falseLabel = "否") {
  return value ? trueLabel : falseLabel;
}

function formatStatusLabel(value) {
  const status = String(value || "").toLowerCase();
  if (status === "active" || status === "enabled") {
    return "启用";
  }
  if (status === "disabled") {
    return "停用";
  }
  if (status === "ready") {
    return "就绪";
  }
  if (status === "cooldown") {
    return "冷却中";
  }
  return value || "-";
}

function formatActionLabel(action) {
  const actionMap = {
    restart: "重启",
    start: "启动",
    stop: "停止",
  };
  return actionMap[action] || action;
}

function bindSidebarNav() {
  if (!sidebarLinks.length) {
    return;
  }
  const targets = sidebarLinks
    .map((link) => {
      const href = link.getAttribute("href");
      if (!href || !href.startsWith("#")) {
        return null;
      }
      const section = document.querySelector(href);
      return section ? { link, section, href } : null;
    })
    .filter(Boolean);

  const activate = (href) => {
    sidebarLinks.forEach((link) => link.classList.toggle("active", link.getAttribute("href") === href));
  };

  targets.forEach(({ link, href }) => {
    link.addEventListener("click", () => activate(href));
  });

  if (!("IntersectionObserver" in window) || !targets.length) {
    activate(targets[0]?.href || "#overview");
    return;
  }

  const observer = new IntersectionObserver(
    (entries) => {
      const activeEntry = entries
        .filter((entry) => entry.isIntersecting)
        .sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top)[0];
      if (!activeEntry) {
        return;
      }
      activate(`#${activeEntry.target.id}`);
    },
    {
      rootMargin: "-30% 0px -48% 0px",
      threshold: [0.2, 0.4, 0.6],
    }
  );

  targets.forEach(({ section }) => observer.observe(section));
  activate(targets[0].href);
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
  updateDashboardAuthHint(formatDashboardAuthState(dashboardAuthRequired, Boolean(getDashboardToken())), dashboardAuthRequired && !getDashboardToken());
}

function classifyServiceChip(health) {
  const serviceState = String(health?.service?.status || "").toLowerCase();
  const listening = Boolean(health?.listening);
  const hasModels = Number(health?.models?.count || 0) > 0;
  const serviceReady = ["started", "running", "active"].includes(serviceState);
  if (serviceReady && listening && hasModels) {
    return { text: "运行中", className: "status-chip ok" };
  }
  if (serviceState === "awaiting_auth") {
    return { text: "待凭据", className: "status-chip warn" };
  }
  if (serviceState === "external") {
    return { text: "外部模式", className: "status-chip warn" };
  }
  if (serviceState === "starting") {
    return { text: "启动中", className: "status-chip warn" };
  }
  if (serviceState === "error") {
    return { text: "异常", className: "status-chip bad" };
  }
  if (serviceReady) {
    return { text: "降级", className: "status-chip warn" };
  }
  return { text: "离线", className: "status-chip bad" };
}

function renderHealth(health) {
  const serviceState = health?.service?.status || "unknown";
  const listening = health?.listening ? "已监听" : "未监听";
  const modelCount = Number(health?.models?.count || 0);
  const authCount = Number(health?.accounts?.count || 0);
  const checkedAt = health?.now ? new Date(health.now).toLocaleString() : "-";
  nodes.metricService.textContent = formatServiceState(serviceState);
  nodes.metricPort.textContent = listening;
  nodes.metricModels.textContent = String(modelCount);
  nodes.metricAuths.textContent = String(authCount);
  let detail = "";
  if (serviceState === "awaiting_auth") {
    detail = "等待上传账号凭据";
  } else if (serviceState === "external") {
    detail = "当前使用外部 Codex App Server";
  } else if (serviceState === "starting") {
    detail = "正在启动 Codex App Server";
  } else if (serviceState === "running" && health?.service?.url) {
    detail = String(health.service.url);
  }
  if (health?.gateway?.enabled) {
    const gatewayText = `网关 ${Number(health.gateway.channels || 0)} 个渠道 / ${Number(health.gateway.apiKeys || 0)} 把密钥`;
    detail = detail ? `${detail} · ${gatewayText}` : gatewayText;
  }
  nodes.healthHint.textContent = detail ? `最近检查：${checkedAt} · ${detail}` : `最近检查：${checkedAt}`;
  const chip = classifyServiceChip(health);
  nodes.serviceChip.textContent = chip.text;
  nodes.serviceChip.className = chip.className;
}

function tokenPill(label, ok) {
  const cls = ok ? "token-pill ok" : "token-pill no";
  return `<span class="${cls}">${label}：${ok ? "有" : "无"}</span>`;
}

function renderAccounts(payload) {
  const accounts = Array.isArray(payload?.accounts) ? payload.accounts : [];
  if (!accounts.length) {
    nodes.accounts.innerHTML = `<div class="account-card">暂未上传账号凭据</div>`;
    return;
  }
  nodes.accounts.innerHTML = accounts
    .map((acc) => {
      if (acc.error) {
        return `
          <article class="account-card">
            <div class="account-top">
              <div>
                <div class="account-file">${acc.label || "未知文件"}</div>
                <div class="account-mail">账号读取失败</div>
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
            <div class="account-file">状态：${localizeMessage(acc.last_status ?? "-")}</div>
          </div>
          <div class="account-meta">
            <div>账号 ID：${acc.account_id || "-"}</div>
            <div>最近刷新：${acc.last_refresh || "-"}</div>
            <div>失败次数：${acc.failures || 0}</div>
            <div>冷却剩余：${acc.cooldown_remaining || 0}s</div>
            <div>Fast 状态：${acc.fast_status || "-"} ${acc.fast_port ? `@${acc.fast_port}` : ""}</div>
            <div>Fast 请求：${acc.fast_request_successes || 0}/${acc.fast_request_count || 0}</div>
          </div>
          ${tokenPill("访问令牌", Boolean(acc.has_access_token))}
          ${tokenPill("刷新令牌", Boolean(acc.has_refresh_token))}
          ${tokenPill("身份令牌", Boolean(acc.has_id_token))}
        </article>
      `;
    })
    .join("");
}

function renderModels(payload) {
  const ids = Array.isArray(payload?.ids) ? payload.ids : [];
  if (!ids.length) {
    nodes.models.innerHTML = `<span class="model-chip">暂无模型数据</span>`;
    return;
  }
  nodes.models.innerHTML = ids.map((id) => `<span class="model-chip">${id}</span>`).join("");
}

function renderConfig(payload) {
  nodes.localConfig.textContent = payload?.localConfig || "读取失败";
  nodes.activeConfig.textContent = payload?.activeConfig || "读取失败";
}

function renderLogs(payload) {
  nodes.logs.textContent = payload?.text || "读取失败";
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
  const existsText = payload?.exists ? "配置文件已存在" : "保存时将创建配置文件";
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
    const validationText = validationErrors.length ? ` · 校验错误 ${validationErrors.length} 条` : " · 配置校验通过";
    nodes.gatewaySummary.textContent = `${existsText} · 渠道 ${channels} 个 · 密钥 ${apiKeys} 把${validationText}`;
  }
  if (validationErrors.length) {
    setOutput(`[渠道配置校验]\n${validationErrors.map((detail) => localizeMessage(detail)).join("\n")}`);
  }
}

function renderGatewayStatus(payload = {}) {
  const channels = Array.isArray(payload?.channels) ? payload.channels : [];
  if (!nodes.gatewayStatusGrid) {
    return;
  }
  if (!channels.length) {
    nodes.gatewayStatusGrid.innerHTML = `<div class="gateway-card">当前还没有加载任何网关渠道</div>`;
    return;
  }
  nodes.gatewayStatusGrid.innerHTML = channels
    .map((channel) => {
      const healthy = Boolean(channel?.healthy) && Boolean(channel?.enabled);
      const disabled = !Boolean(channel?.enabled);
      const cardClass = disabled ? "gateway-card warn" : healthy ? "gateway-card" : "gateway-card bad";
      const stateText = disabled ? "已停用" : healthy ? "就绪" : "冷却中";
      const groups = Array.isArray(channel?.groups) ? channel.groups.join(", ") : "-";
      const models = Array.isArray(channel?.models) ? channel.models.join(", ") : "-";
      const families = Array.isArray(channel?.routeFamilies) ? channel.routeFamilies.join(", ") : "-";
      const lastError = channel?.lastError ? `<div class="gateway-card-error">${localizeMessage(channel.lastError)}</div>` : "";
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
            <div>优先级：${channel?.priority ?? 0}</div>
            <div>权重：${channel?.weight ?? 1}</div>
            <div>分组：${groups || "-"}</div>
            <div>路由族：${families || "-"}</div>
            <div>冷却剩余：${channel?.cooldownRemaining ?? 0}s</div>
            <div>失败次数：${channel?.failures ?? 0}</div>
            <div>上次状态：${channel?.lastStatus ?? "-"}</div>
            <div>认证源：${channel?.authLabel || "-"}</div>
            <div>模型：${models || "-"}</div>
            <div>超时：${channel?.timeoutSeconds ?? 600}s</div>
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
    nodes.adminKeyUserId.innerHTML = `<option value="">暂无用户</option>`;
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
    nodes.adminUsers.innerHTML = `<div class="admin-card">暂未创建托管用户</div>`;
    return;
  }
  nodes.adminUsers.innerHTML = users
    .map(
      (user) => `
        <article class="admin-card">
          <div class="admin-card-head">
            <div>
              <div class="admin-card-title">#${user.id} ${user.name}</div>
              <div class="admin-card-subtle">${user.email || "未填写邮箱"} · ${formatStatusLabel(user.status)}</div>
            </div>
            <span class="status-chip ${user.status === "active" ? "ok" : "warn"}">${formatStatusLabel(user.status)}</span>
          </div>
          <div class="admin-card-meta">
            <div>月额度：${formatQuota(user.monthlyQuotaTokens)}</div>
            <div>密钥数量：${formatInteger(user.keyCount)}</div>
            <div>本月用量：${formatInteger(user.usedTokensMonth)}</div>
            <div>本月费用：${formatMoney(user.estimatedCostMonth)}</div>
            <div>输入单价 / 百万：${formatMoney(user.promptPricePerMillion)}</div>
            <div>输出单价 / 百万：${formatMoney(user.completionPricePerMillion)}</div>
          </div>
          <div class="admin-inline">
            <button class="btn" data-action="edit-user" data-user-id="${user.id}">编辑</button>
            <button class="btn btn-danger" data-action="delete-user" data-user-id="${user.id}">删除</button>
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
    nodes.adminKeys.innerHTML = `<div class="admin-card">暂未创建托管密钥</div>`;
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
            <span class="status-chip ${item.status === "active" ? "ok" : "warn"}">${formatStatusLabel(item.status)}</span>
          </div>
          <div class="admin-card-meta">
            <div>所属用户：${item.userName || "-"}</div>
            <div>用户状态：${formatStatusLabel(item.userStatus)}</div>
            <div>分组：${(item.groups || []).join(", ") || "-"}</div>
            <div>模型：${(item.models || []).join(", ") || "-"}</div>
            <div>到期时间：${item.expiresAt || "未设置"}</div>
            <div>上次使用：${item.lastUsedAt || "-"}</div>
            <div>本月用量：${formatInteger(item.usedTokensMonth)}</div>
            <div>本月费用：${formatMoney(item.estimatedCostMonth)}</div>
          </div>
          <div class="admin-inline">
            <button class="btn" data-action="edit-key" data-key-id="${item.id}">编辑</button>
            <button class="btn ${item.status === "active" ? "btn-danger" : ""}" data-action="toggle-key" data-key-id="${item.id}" data-next-status="${item.status === "active" ? "disabled" : "active"}">
              ${item.status === "active" ? "停用" : "启用"}
            </button>
            <button class="btn btn-danger" data-action="delete-key" data-key-id="${item.id}">删除</button>
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
    nodes.adminUsageWindow.textContent = `统计窗口：${payload.monthStart || "-"} 至 ${payload.monthEnd || "-"}`;
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
                  <div>总令牌：${formatInteger(item.totalTokens)}</div>
                  <div>估算费用：${formatMoney(item.estimatedCost)}</div>
                </div>
              </article>
            `
          )
          .join("")
      : `<div class="admin-card">当前还没有用量记录</div>`;
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
                  <div>总令牌：${formatInteger(item.totalTokens)}</div>
                  <div>估算费用：${formatMoney(item.estimatedCost)}</div>
                </div>
              </article>
            `
          )
          .join("")
      : `<div class="admin-card">当前还没有密钥用量</div>`;
  }
  if (nodes.adminUsageEvents) {
    nodes.adminUsageEvents.textContent = events.length
      ? events
          .map(
            (event) =>
              `[${event.createdAt}] ${event.userName}/${event.apiKeyName} 接口=${event.endpoint} 模型=${event.model} 总令牌=${event.totalTokens} 费用=${formatMoney(event.estimatedCost)} 状态码=${event.statusCode} 渠道=${event.channelId || "-"}`
          )
          .join("\n")
      : "当前还没有用量事件";
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
    setOutput(`[用户已保存]\n#${payload?.user?.id || "-"} ${payload?.user?.name || ""}`);
    showToast("用户保存成功");
  } catch (error) {
    setOutput(String(error.message || error));
    showToast("用户保存失败", true);
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
    const createdToken = payload?.apiKey?.token ? `\n访问密钥=${payload.apiKey.token}` : "";
    setOutput(`[密钥已保存]\n#${payload?.apiKey?.id || "-"} ${payload?.apiKey?.name || ""}${createdToken}`);
    showToast("密钥保存成功");
  } catch (error) {
    setOutput(String(error.message || error));
    showToast("密钥保存失败", true);
  }
}

async function toggleApiKey(keyId, nextStatus) {
  try {
    await api(`/api/admin/keys/${keyId}`, {
      method: "POST",
      body: JSON.stringify({ status: nextStatus }),
    });
    await refreshAdmin();
    showToast(`密钥状态已更新为${formatStatusLabel(nextStatus)}`);
  } catch (error) {
    setOutput(String(error.message || error));
    showToast("密钥状态更新失败", true);
  }
}

async function deleteUser(userId) {
  if (!window.confirm(`确认删除用户 #${userId} 吗？这会一并删除关联密钥和用量记录。`)) {
    return;
  }
  try {
    await api(`/api/admin/users/${userId}`, { method: "DELETE" });
    if (Number(nodes.adminUserId?.value || 0) === Number(userId)) {
      fillUserForm({ status: "active" });
    }
    await refreshAdmin();
    setOutput(`[用户已删除]\n#${userId}`);
    showToast("用户已删除");
  } catch (error) {
    setOutput(String(error.message || error));
    showToast("用户删除失败", true);
  }
}

async function deleteApiKey(keyId) {
  if (!window.confirm(`确认删除密钥 #${keyId} 吗？`)) {
    return;
  }
  try {
    await api(`/api/admin/keys/${keyId}`, { method: "DELETE" });
    if (Number(nodes.adminKeyId?.value || 0) === Number(keyId)) {
      fillKeyForm({ status: "active", groups: ["default"], models: ["*"] });
    }
    await refreshAdmin();
    setOutput(`[密钥已删除]\n#${keyId}`);
    showToast("密钥已删除");
  } catch (error) {
    setOutput(String(error.message || error));
    showToast("密钥删除失败", true);
  }
}

async function loadSettings() {
  const payload = await api("/api/settings");
  applySettingsForm(payload?.settings || {});
  if (nodes.settingsPath) {
    nodes.settingsPath.textContent = payload?.settingsPath || "-";
  }
  setSettingsHint("自动保存：已加载");
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
  setSettingsHint("自动保存：保存中...");
  try {
    const payload = await api("/api/settings", {
      method: "POST",
      body: JSON.stringify(readSettingsForm()),
    });
    if (nodes.settingsPath) {
      nodes.settingsPath.textContent = payload?.settingsPath || "-";
    }
    setSettingsHint("自动保存：已保存");
    if (showOkToast) {
      showToast("设置已保存");
    }
  } catch (error) {
    setOutput(String(error.message || error));
    setSettingsHint("自动保存：保存失败", true);
    showToast("设置保存失败", true);
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
  setSettingsHint("自动保存：等待写入...");
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
    setOutput(`模型检查失败：${health.models.error}`);
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
    const message = rejected[0]?.reason?.message || "部分模块刷新失败";
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
    showToast("渠道 JSON 格式无效", true);
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
    showToast("渠道配置已保存");
  } catch (error) {
    setOutput(String(error.message || error));
    showToast("渠道配置保存失败", true);
  }
}

async function runSync() {
  try {
    const payload = await api("/api/actions/sync", { method: "POST" });
    setOutput((payload.stdout || "").trim() || "账号同步完成");
    if (payload.health) {
      renderHealth(payload.health);
    }
    await refreshAccounts();
    showToast("账号同步完成");
  } catch (error) {
    setOutput(String(error.message || error));
    showToast("账号同步失败", true);
  }
}

async function runServiceAction(action) {
  try {
    const payload = await api("/api/actions/service", {
      method: "POST",
      body: JSON.stringify({ action }),
    });
    const report = [`[服务操作：${formatActionLabel(action)}]`, String(payload.stdout || "").trim(), String(payload.stderr || "").trim()]
      .filter(Boolean)
      .join("\n");
    setOutput(report || `${formatActionLabel(action)}完成`);
    if (payload.health) {
      renderHealth(payload.health);
    }
    await refreshModels();
    showToast(`服务操作已完成：${formatActionLabel(action)}`);
  } catch (error) {
    setOutput(String(error.message || error));
    showToast(`服务操作失败：${formatActionLabel(action)}`, true);
  }
}

async function uploadAuthFiles() {
  const fileInput = nodes.authFiles;
  if (!fileInput || !fileInput.files || fileInput.files.length === 0) {
    showToast("请先选择一个或多个 auth.json 文件", true);
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
      const message = localizeMessage(payload.error || payload.message || `${response.status} ${response.statusText}`);
      throw new Error(message);
    }

    const lines = [
      `[上传结果] 上传=${payload.uploaded || 0} 新建=${payload.created || 0} 更新=${payload.updated || 0}`,
      Number(payload?.service?.status?.instanceCount || 0) > 0
        ? `Fast 实例：${payload.service.status.instanceCount} 个，当前活跃 ${payload.service.status.activeCount || 0} 个`
        : "",
      ...(Array.isArray(payload.results)
        ? payload.results.map((item) => {
            const action = item?.action || "created";
            const accountId = item?.accountId || "未知";
            const target = item?.target || "";
            if (action === "updated") {
              return `[更新] ${item?.filename || "未知文件"} -> ${target}（同 account_id：${accountId}）`;
            }
            return `[新建] ${item?.filename || "未知文件"} -> ${target}（account_id：${accountId}）`;
          })
        : Array.isArray(payload.written)
          ? payload.written
          : []),
      ...(Array.isArray(payload.errors) && payload.errors.length ? ["错误：", ...payload.errors] : []),
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
    showToast("凭据上传成功");
  } catch (error) {
    setOutput(String(error.message || error));
    showToast("凭据上传失败", true);
  }
}

function bindActions() {
  $("btn-refresh").addEventListener("click", async () => {
    await refreshAll();
    showToast("总览刷新完成");
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
      showToast("控制面数据已刷新");
    } catch (error) {
      setOutput(String(error.message || error));
      showToast("控制面数据刷新失败", true);
    }
  });
  $("btn-user-save").addEventListener("click", saveUser);
  $("btn-user-new").addEventListener("click", () => fillUserForm({ status: "active" }));
  $("btn-key-save").addEventListener("click", saveApiKey);
  $("btn-key-new").addEventListener("click", () => fillKeyForm({ status: "active", groups: ["default"], models: ["*"] }));
  $("btn-dashboard-token-save").addEventListener("click", async () => {
    const token = nodes.dashboardToken?.value || "";
    setDashboardToken(token.trim());
    updateDashboardAuthHint(
      token.trim() ? "后台鉴权：口令已保存" : "后台鉴权：口令已清空",
      !token.trim() && dashboardAuthRequired
    );
    try {
      await Promise.allSettled([loadSettings(), refreshAll(), loadGatewayConfig(), loadGatewayStatus()]);
      settingsLoaded = true;
      setSettingsHint("自动保存：已启用");
      showToast("后台口令已应用");
    } catch (error) {
      setOutput(String(error.message || error));
      showToast("后台口令校验失败", true);
    }
  });
  $("btn-dashboard-token-clear").addEventListener("click", () => {
    setDashboardToken("");
    if (nodes.dashboardToken) {
      nodes.dashboardToken.value = "";
    }
    updateDashboardAuthHint(
      dashboardAuthRequired ? "后台鉴权：需要管理员口令" : "后台鉴权：已关闭",
      dashboardAuthRequired
    );
    showToast("后台口令已清空");
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
  bindSidebarNav();
  bindActions();
  bindSettingsAutosave();
  fillUserForm({ status: "active" });
  fillKeyForm({ status: "active", groups: ["default"], models: ["*"] });
  await loadDashboardAuthStatus();
  if (dashboardAuthRequired && !getDashboardToken()) {
    settingsLoaded = false;
    setSettingsHint("自动保存：等待后台管理员口令", true);
    return;
  }

  await Promise.allSettled([loadSettings(), refreshAll(), loadGatewayConfig(), loadGatewayStatus()]);
  settingsLoaded = true;
  setSettingsHint("自动保存：已启用");
  setInterval(refreshHealth, 15000);
}

init();
