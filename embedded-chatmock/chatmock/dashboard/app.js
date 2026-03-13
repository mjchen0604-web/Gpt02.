const $ = (id) => document.getElementById(id);

const nodes = {
  serviceChip: $("service-chip"),
  metricService: $("metric-service"),
  metricPort: $("metric-port"),
  metricModels: $("metric-models"),
  metricAuths: $("metric-auths"),
  healthHint: $("health-hint"),
  output: $("ops-output"),
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
  setChatgptAuthAccessToken: $("set-chatgpt-auth-access-token"),
  setChatgptAuthAccountId: $("set-chatgpt-auth-account-id"),
  setChatgptAuthPlanType: $("set-chatgpt-auth-plan-type"),
};

let settingsLoaded = false;
let settingsSaveTimer = null;
let savingSettings = false;

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: {
      "Content-Type": "application/json",
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
    const message = payload.error || payload.message || `${response.status} ${response.statusText}`;
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
            <div class="account-file">status: ${acc.status || "ready"} / ${acc.last_status ?? "-"}</div>
          </div>
          <div class="account-meta">
            <div>account: ${acc.account_id || "-"}</div>
            <div>refresh: ${acc.last_refresh || "-"}</div>
            <div>failures: ${acc.failures || 0}</div>
            <div>cooldown: ${acc.cooldown_remaining || 0}s</div>
            <div>classification: ${acc.last_classification || "-"}</div>
            <div>raw code: ${acc.last_raw_code || "-"}</div>
            <div>raw message: ${acc.last_raw_message || "-"}</div>
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
    chatgptAuthAccessToken: nodes.setChatgptAuthAccessToken?.value || "",
    chatgptAuthAccountId: nodes.setChatgptAuthAccountId?.value || "",
    chatgptAuthPlanType: nodes.setChatgptAuthPlanType?.value || "",
    uploadReplaceDefault: Boolean(nodes.authReplace?.checked),
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
  if (nodes.setChatgptAuthAccessToken) {
    nodes.setChatgptAuthAccessToken.value = settings.chatgptAuthAccessToken || "";
  }
  if (nodes.setChatgptAuthAccountId) {
    nodes.setChatgptAuthAccountId.value = settings.chatgptAuthAccountId || "";
  }
  if (nodes.setChatgptAuthPlanType) {
    nodes.setChatgptAuthPlanType.value = settings.chatgptAuthPlanType || "";
  }
  if (nodes.authReplace) {
    nodes.authReplace.checked = Boolean(settings.uploadReplaceDefault);
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
    nodes.setChatgptAuthAccessToken,
    nodes.setChatgptAuthAccountId,
    nodes.setChatgptAuthPlanType,
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
  const tasks = [refreshHealth(), refreshAccounts(), refreshModels(), refreshConfig(), refreshLogs()];
  const results = await Promise.allSettled(tasks);
  const rejected = results.filter((item) => item.status === "rejected");
  if (rejected.length) {
    const message = rejected[0]?.reason?.message || "Partial refresh failed";
    showToast(message, true);
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
    const response = await fetch("/api/actions/upload_auths", {
      method: "POST",
      body: formData,
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

  const uploadBtn = $("btn-upload-auth");
  if (uploadBtn) {
    uploadBtn.addEventListener("click", uploadAuthFiles);
  }
}

async function init() {
  bindActions();
  bindSettingsAutosave();

  await Promise.allSettled([loadSettings(), refreshAll()]);
  settingsLoaded = true;
  setSettingsHint("autosave: enabled");
  setInterval(refreshHealth, 15000);
}

init();


