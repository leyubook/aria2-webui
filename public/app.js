const state = {
  snapshot: null,
  currentView: "smart",
  currentExplorer: "remote",
  logs: [],
  wsConnected: false,
  pollTimer: null,
  apiToken: localStorage.getItem("authToken") || "",
  username: localStorage.getItem("username") || "",
  rssPresets: [],
};

const elements = {
  addTaskForm: document.getElementById("addTaskForm"),
  forceAddButton: document.getElementById("forceAddButton"),
  scanWebdavButton: document.getElementById("scanWebdavButton"),
  refreshLocalButton: document.getElementById("refreshLocalButton"),
  statsGrid: document.getElementById("statsGrid"),
  healthGrid: document.getElementById("healthGrid"),
  smartBoard: document.getElementById("smartBoard"),
  taskList: document.getElementById("taskList"),
  remoteTable: document.getElementById("remoteTable"),
  remoteExplorer: document.getElementById("remoteExplorer"),
  localExplorer: document.getElementById("localExplorer"),
  attentionPanel: document.getElementById("attentionPanel"),
  logConsole: document.getElementById("logConsole"),
  settingsForm: document.getElementById("settingsForm"),
  changePasswordForm: document.getElementById("changePasswordForm"),
  dialog: document.getElementById("feedbackDialog"),
  dialogBody: document.getElementById("dialogBody"),
  socketState: document.getElementById("socketState"),
  scanMeta: document.getElementById("scanMeta"),
  themeToggle: document.getElementById("themeToggle"),
  authPage: document.getElementById("authPage"),
  mainApp: document.getElementById("mainApp"),
  authForm: document.getElementById("authForm"),
  authUsername: document.getElementById("authUsername"),
  authPassword: document.getElementById("authPassword"),
  authTitle: document.getElementById("authTitle"),
  authSubmit: document.getElementById("authSubmit"),
  authError: document.getElementById("authError"),
  exportButton: document.getElementById("exportButton"),
  importButton: document.getElementById("importButton"),
  importFile: document.getElementById("importFile"),
  logoutButton: document.getElementById("logoutButton"),
  // 追番订阅
  subscriptionGrid: document.getElementById("subscriptionGrid"),
  subscriptionDialog: document.getElementById("subscriptionDialog"),
  subscriptionForm: document.getElementById("subscriptionForm"),
  addSubscriptionBtn: document.getElementById("addSubscriptionBtn"),
  rssCheckAllBtn: document.getElementById("rssCheckAllBtn"),
  parseMikanBtn: document.getElementById("parseMikanBtn"),
  subEpisodePreset: document.getElementById("subEpisodePreset"),
  // 集成/通知设置
  integrationForm: document.getElementById("integrationForm"),
  notifyForm: document.getElementById("notifyForm"),
  testNotifyBtn: document.getElementById("testNotifyBtn"),
};

function showMessage(title, message, detail = "") {
  elements.dialogBody.innerHTML = `
    <h3>${escapeHtml(title)}</h3>
    <p>${escapeHtml(message)}</p>
    ${detail ? `<pre>${escapeHtml(detail)}</pre>` : ""}
  `;
  elements.dialog.showModal();
}

async function api(url, options = {}) {
  const headers = { "Content-Type": "application/json" };
  if (state.apiToken) {
    headers["X-Api-Token"] = state.apiToken;
  }
  const response = await fetch(url, {
    headers,
    ...options,
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.detail || data.message || `请求失败：${response.status}`);
  }
  return data;
}

function escapeHtml(value = "") {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function formatBytes(bytes = 0) {
  if (!bytes) {
    return "0 B";
  }
  const units = ["B", "KB", "MB", "GB", "TB"];
  let index = 0;
  let value = Number(bytes);
  while (value >= 1024 && index < units.length - 1) {
    value /= 1024;
    index += 1;
  }
  return `${value.toFixed(value >= 100 || index === 0 ? 0 : 1)} ${units[index]}`;
}

function formatSpeed(value = 0) {
  return value ? `${formatBytes(value)}/s` : "0 B/s";
}

function formatTime(value) {
  if (!value) {
    return "未扫描";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return date.toLocaleString();
}

function statusLabel(status) {
  const mapping = {
    queued: "排队中",
    downloading: "下载中",
    uploading: "正在搬运至 WebDAV",
    upload_failed: "搬运失败",
    uploaded: "已搬运",
    downloaded: "下载完成",
    error: "下载失败",
    paused: "已暂停",
    removed: "已移除",
  };
  return mapping[status] || status;
}

function renderStats(snapshot) {
  const metrics = [
    ["总任务数", snapshot.stats.total],
    ["排队中", snapshot.stats.queued],
    ["下载中", snapshot.stats.downloading],
    ["搬运中", snapshot.stats.uploading],
    ["待处理", snapshot.stats.attention],
    ["已完成", snapshot.stats.completed],
  ];
  elements.statsGrid.innerHTML = metrics
    .map(
      ([label, value]) => `
        <article class="metric-card">
          <span>${escapeHtml(label)}</span>
          <strong>${escapeHtml(value)}</strong>
        </article>
      `,
    )
    .join("");

  const health = [
    [
      "Aria2 RPC",
      snapshot.health.aria2_online ? "在线" : "离线",
      snapshot.health.aria2_online ? "online" : "offline",
    ],
    [
      "Rclone",
      snapshot.health.rclone_online ? "可用" : "待检测 / 异常",
      snapshot.health.rclone_online ? "online" : "idle",
    ],
    ["WebSocket", `${snapshot.health.websocket_clients} 个客户端`, "idle"],
  ];
  elements.healthGrid.innerHTML = health
    .map(
      ([label, value, cls]) => `
        <article class="health-card">
          <span>${escapeHtml(label)}</span>
          <strong class="${escapeHtml(cls)}">${escapeHtml(value)}</strong>
        </article>
      `,
    )
    .join("");
}

function buildFileTree(files) {
  const root = {};
  files.forEach((file) => {
    const parts = file.path.split("/").filter(Boolean);
    let node = root;
    parts.forEach((part, index) => {
      if (index === parts.length - 1) {
        node[part] = file;
        return;
      }
      node[part] = node[part] || {};
      node = node[part];
    });
  });
  return root;
}

function renderTreeNode(name, value, remoteMode = false, prefix = "") {
  if (value && value.path) {
    const action = remoteMode
      ? `<button class="ghost-button verify-md5-button" data-remote-path="${escapeHtml(
          value.path,
        )}" data-file-name="${escapeHtml(value.name)}">校验本地 MD5</button>`
      : "";
    return `
      <li>
        <div class="tree-node">
          <div class="task-head">
            <strong>${escapeHtml(name)}</strong>
            <span class="tree-meta">${escapeHtml(formatBytes(value.size || 0))}</span>
          </div>
          <div class="task-footer">
            <span>${escapeHtml(prefix ? `${prefix}/${name}` : name)}</span>
            ${action}
          </div>
        </div>
      </li>
    `;
  }

  const children = Object.entries(value)
    .sort((a, b) => a[0].localeCompare(b[0], "zh-CN"))
    .map(([childName, childValue]) => renderTreeNode(childName, childValue, remoteMode, prefix ? `${prefix}/${name}` : name))
    .join("");

  return `
    <li>
      <div class="tree-node">
        <details open>
          <summary>${escapeHtml(name)}</summary>
          <ul>${children}</ul>
        </details>
      </div>
    </li>
  `;
}

function renderExplorer(files, target, remoteMode = false) {
  if (!files.length) {
    target.innerHTML = `<div class="empty-state">暂无${remoteMode ? "远程" : "本地"}文件数据</div>`;
    return;
  }
  const tree = buildFileTree(files);
  const nodes = Object.entries(tree)
    .sort((a, b) => a[0].localeCompare(b[0], "zh-CN"))
    .map(([name, value]) => renderTreeNode(name, value, remoteMode))
    .join("");
  target.innerHTML = `<div class="tree"><ul>${nodes}</ul></div>`;
}

function renderRemoteTable(files) {
  if (!files.length) {
    elements.remoteTable.innerHTML = `<div class="empty-state">扫描 WebDAV 后会在这里显示远端文件列表。</div>`;
    return;
  }
  elements.remoteTable.innerHTML = `
    <div class="file-table active">
      <div class="table-head">
        <span>远端路径</span>
        <span>大小</span>
        <span>动作</span>
      </div>
      ${files
        .map(
          (file) => `
            <div class="table-row">
              <div class="table-path">
                <strong>${escapeHtml(file.name)}</strong>
                <span>${escapeHtml(file.path)}</span>
              </div>
              <span>${escapeHtml(formatBytes(file.size))}</span>
              <button class="ghost-button verify-md5-button" data-remote-path="${escapeHtml(
                file.path,
              )}" data-file-name="${escapeHtml(file.name)}">校验本地 MD5</button>
            </div>
          `,
        )
        .join("")}
    </div>
  `;
}

function renderTaskCard(task) {
  const progress = Math.max(0, Math.min(100, Number(task.progress || 0)));
  const retryButton =
    task.ui_status === "upload_failed"
      ? `<button class="danger-button retry-upload-button" data-gid="${escapeHtml(task.gid)}">手动重试上传</button>`
      : "";
  const queueLabel =
    task.queue_position != null ? `队列位置 #${task.queue_position}` : task.info_hash || "实时同步";
  return `
    <article class="task-card ${escapeHtml(task.ui_status)}">
      <div class="task-head">
        <div>
          <h3 class="task-title">${escapeHtml(task.name)}</h3>
          <div class="task-meta">${escapeHtml(queueLabel)}</div>
        </div>
        <span class="badge ${escapeHtml(task.ui_status)}">${escapeHtml(statusLabel(task.ui_status))}</span>
      </div>
      <div class="progress-track">
        <div class="progress-bar" style="width:${progress}%"></div>
      </div>
      <div class="task-footer">
        <span>${escapeHtml(formatBytes(task.completed_length))} / ${escapeHtml(
          formatBytes(task.total_length),
        )}</span>
        <span>${escapeHtml(formatSpeed(task.download_speed))}</span>
      </div>
      <div class="task-footer">
        <span>${escapeHtml(task.last_message || "等待状态更新")}</span>
      </div>
      ${task.remote_target ? `<div class="task-meta">目标：${escapeHtml(task.remote_target)}</div>` : ""}
      ${task.upload_error ? `<div class="task-meta">错误：${escapeHtml(task.upload_error)}</div>` : ""}
      ${retryButton}
    </article>
  `;
}

function renderSmartBoard(tasks) {
  const groups = {
    queued: [],
    downloading: [],
    uploading: [],
    attention: [],
    done: [],
  };
  tasks.forEach((task) => {
    if (task.ui_status === "queued") {
      groups.queued.push(task);
    } else if (task.ui_status === "downloading") {
      groups.downloading.push(task);
    } else if (task.ui_status === "uploading") {
      groups.uploading.push(task);
    } else if (["upload_failed", "error"].includes(task.ui_status)) {
      groups.attention.push(task);
    } else {
      groups.done.push(task);
    }
  });

  const layout = [
    ["排队中", groups.queued],
    ["下载中", groups.downloading],
    ["上传搬运中", groups.uploading],
    ["异常待处理", groups.attention],
    ["完成归档", groups.done],
  ];
  elements.smartBoard.innerHTML = layout
    .map(
      ([label, items]) => `
        <section class="lane">
          <h3>${escapeHtml(label)} <span class="task-meta">${items.length}</span></h3>
          <div class="lane-body">
            ${items.length ? items.map(renderTaskCard).join("") : `<div class="empty-state">暂无任务</div>`}
          </div>
        </section>
      `,
    )
    .join("");
}

function renderAttention(tasks) {
  const attentionItems = tasks.filter((task) => ["upload_failed", "error"].includes(task.ui_status));
  if (!attentionItems.length) {
    elements.attentionPanel.innerHTML = `<div class="empty-state">当前没有需要人工干预的任务</div>`;
    return;
  }
  elements.attentionPanel.innerHTML = attentionItems
    .map(
      (task) => `
        <article class="attention-card">
          <strong>${escapeHtml(task.name)}</strong>
          <p>${escapeHtml(task.upload_error || task.last_message || "任务出现异常")}</p>
          <button class="danger-button retry-upload-button" data-gid="${escapeHtml(task.gid)}">手动重试上传</button>
        </article>
      `,
    )
    .join("");
}

function renderLogs(logs) {
  state.logs = logs.slice(-180);
  elements.logConsole.innerHTML = state.logs
    .map((line) => {
      const lower = line.toLowerCase();
      const cls = lower.includes("[error]") ? "error" : lower.includes("[warning]") ? "warning" : "info";
      return `<div class="log-line ${cls}">${escapeHtml(line)}</div>`;
    })
    .join("");
  elements.logConsole.scrollTop = elements.logConsole.scrollHeight;
}

function populateSettings(settings) {
  // 填充主设置表单
  Object.entries(settings).forEach(([key, value]) => {
    const input = elements.settingsForm.elements.namedItem(key);
    if (!input) return;
    if (input.type === "password" && value === "***") {
      input.placeholder = "已设置，留空保持不变";
      input.value = "";
      return;
    }
    input.value = value;
  });
  // 填充集成设置表单
  if (elements.integrationForm) {
    Object.entries(settings).forEach(([key, value]) => {
      const input = elements.integrationForm.elements.namedItem(key);
      if (!input) return;
      if (input.type === "password" && value === "***") {
        input.placeholder = "已设置，留空保持不变";
        input.value = "";
        return;
      }
      input.value = value;
    });
  }
  // 填充通知设置表单
  if (elements.notifyForm) {
    Object.entries(settings).forEach(([key, value]) => {
      const input = elements.notifyForm.elements.namedItem(key);
      if (!input) return;
      if (input.type === "password" && value === "***") {
        input.placeholder = "已设置，留空保持不变";
        input.value = "";
        return;
      }
      input.value = value;
    });
  }
}

// ── RSS 预设加载 ─────────────────────────────────────────

function loadRssPresets() {
  api("/api/rss/presets")
    .then((data) => {
      state.rssPresets = data.episode_patterns || [];
      // 追番订阅对话框下拉
      const subSel = elements.subEpisodePreset;
      if (subSel) {
        subSel.innerHTML = `<option value="">-- 选择预设 --</option>`;
        state.rssPresets.forEach((pattern, i) => {
          const opt = document.createElement("option");
          opt.value = pattern;
          opt.textContent = `预设 ${i + 1}: ${pattern.substring(0, 50)}...`;
          subSel.appendChild(opt);
        });
      }
    })
    .catch(() => {});
}

function renderSubscriptions(subscriptions) {
  const grid = elements.subscriptionGrid;
  if (!grid) return;
  if (!subscriptions || !subscriptions.length) {
    grid.innerHTML = '<div class="empty-state">尚未添加任何追番订阅。点击「+ 新增订阅」开始。</div>';
    return;
  }
  grid.innerHTML = subscriptions.map((sub) => {
    const epCount = Object.keys(sub.downloaded_episodes || {}).length;
    const progress = sub.total_episodes > 0 ? Math.min(100, Math.round(epCount / sub.total_episodes * 100)) : 0;
    const poster = sub.poster_url
      ? `<img src="${escapeHtml(sub.poster_url)}" class="sub-poster" onerror="this.outerHTML='<div class=\\'sub-poster sub-poster-placeholder\\'>${escapeHtml(sub.name.charAt(0))}</div>'" />`
      : `<div class="sub-poster sub-poster-placeholder">${escapeHtml(sub.name.charAt(0))}</div>`;
    const desc = sub.description ? `<p class="sub-desc">${escapeHtml(sub.description.substring(0, 100))}</p>` : "";
    const meta = [
      sub.season > 1 ? `<span>S${sub.season}</span>` : "",
      `<span>EP ${epCount}/${sub.total_episodes || "?"}</span>`,
      sub.air_date ? `<span>${escapeHtml(sub.air_date)}</span>` : "",
    ].filter(Boolean).join("");
    const progressBar = sub.total_episodes > 0
      ? `<div class="progress-track"><div class="progress-bar" style="width:${progress}%"></div></div>` : "";
    return `
      <article class="subscription-card ${sub.enabled ? "" : "sub-disabled"}">
        ${poster}
        <div class="sub-info">
          <div class="sub-head">
            <strong>${escapeHtml(sub.name)}</strong>
            <span class="badge ${sub.enabled ? "online" : "idle"}">${sub.enabled ? "追番中" : "已暂停"}</span>
          </div>
          ${desc}
          <div class="sub-meta">${meta}</div>
          ${progressBar}
          <div class="sub-actions">
            <button class="ghost-button sub-toggle" data-id="${escapeHtml(sub.id)}">${sub.enabled ? "暂停" : "恢复"}</button>
            <button class="ghost-button sub-edit" data-id="${escapeHtml(sub.id)}">编辑</button>
            <button class="ghost-button sub-check" data-id="${escapeHtml(sub.id)}">检查</button>
            <button class="danger-button sub-delete" data-id="${escapeHtml(sub.id)}">删除</button>
          </div>
        </div>
      </article>`;
  }).join("");
}

function openSubscriptionDialog(sub) {
  const form = elements.subscriptionForm;
  const title = document.getElementById("subDialogTitle");
  if (sub) {
    title.textContent = "编辑订阅";
    document.getElementById("subId").value = sub.id;
    document.getElementById("subMikanUrl").value = sub.mikan_url || "";
    document.getElementById("subName").value = sub.name || "";
    document.getElementById("subRssUrl").value = sub.rss_url || "";
    document.getElementById("subStandbyRssUrl").value = sub.standby_rss_url || "";
    document.getElementById("subTmdbId").value = sub.tmdb_id || "";
    document.getElementById("subBangumiUrl").value = sub.bangumi_url || "";
    document.getElementById("subMatchPattern").value = sub.match_pattern || "";
    document.getElementById("subExcludePattern").value = sub.exclude_pattern || "";
    document.getElementById("subEpisodeRegex").value = sub.episode_regex || "";
    document.getElementById("subEpisodeGroupIndex").value = sub.episode_group_index || 1;
    document.getElementById("subEpisodeOffset").value = sub.episode_offset || 0;
    document.getElementById("subSeason").value = sub.season || 1;
    document.getElementById("subTotalEpisodes").value = sub.total_episodes || 0;
    document.getElementById("subDownloadDirTemplate").value = sub.download_dir_template || "";
    document.getElementById("subSlackingDays").value = sub.slacking_days || 0;
    document.getElementById("subAutoDisable").checked = sub.auto_disable_when_complete !== false;
    document.getElementById("subSkipHalf").checked = !!sub.skip_half_episodes;
    document.getElementById("subOnlyLatest").checked = !!sub.download_only_latest;
    document.getElementById("subOmissionDetect").checked = !!sub.omission_detection;
    document.getElementById("subNotifyDownload").checked = sub.notify_on_download !== false;
    document.getElementById("subNotifyComplete").checked = sub.notify_on_complete !== false;
    document.getElementById("subNotifyMissing").checked = !!sub.notify_on_missing;
  } else {
    title.textContent = "新增订阅";
    form.reset();
    document.getElementById("subId").value = "";
    document.getElementById("subAutoDisable").checked = true;
    document.getElementById("subNotifyDownload").checked = true;
    document.getElementById("subNotifyComplete").checked = true;
    document.getElementById("subEpisodeGroupIndex").value = 1;
    document.getElementById("subSeason").value = 1;
  }
  elements.subscriptionDialog.showModal();
}

function render(snapshot) {
  state.snapshot = snapshot;
  renderStats(snapshot);
  renderExplorer(snapshot.remote_files || [], elements.remoteExplorer, true);
  renderExplorer(snapshot.local_files || [], elements.localExplorer, false);
  renderRemoteTable(snapshot.remote_files || []);
  renderSmartBoard(snapshot.tasks || []);
  elements.taskList.innerHTML = snapshot.tasks.length
    ? snapshot.tasks.map(renderTaskCard).join("")
    : `<div class="empty-state">尚未添加任何任务</div>`;
  renderAttention(snapshot.tasks || []);
  renderLogs(snapshot.logs || []);
  renderSubscriptions(snapshot.subscriptions || []);
  populateSettings(snapshot.settings || {});
  elements.scanMeta.textContent = snapshot.last_scan_at
    ? `远端上次扫描：${formatTime(snapshot.last_scan_at)}`
    : "尚未扫描远端";
}

function toggleView(viewName) {
  state.currentView = viewName;
  document.querySelectorAll(".nav-tab").forEach((button) => {
    button.classList.toggle("active", button.dataset.view === viewName);
  });
  document.querySelectorAll(".view-panel").forEach((panel) => {
    panel.classList.toggle("active", panel.id === `view-${viewName}`);
  });
}

function toggleExplorer(name) {
  state.currentExplorer = name;
  document.querySelectorAll(".mini-tab").forEach((button) => {
    button.classList.toggle("active", button.dataset.explorer === name);
  });
  elements.remoteExplorer.classList.toggle("active", name === "remote");
  elements.localExplorer.classList.toggle("active", name === "local");
}

async function loadDashboard() {
  const snapshot = await api("/api/dashboard");
  render(snapshot);
}

async function submitTask(force = false) {
  const payload = {
    uri: elements.addTaskForm.uri.value.trim(),
    filename_hint: elements.addTaskForm.filename_hint.value.trim() || null,
    force,
  };
  const result = await api("/api/tasks/add", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  if (result.duplicate) {
    showMessage("去重拦截", result.message, (result.matches || []).join("\n"));
    return;
  }
  showMessage("任务已下发", result.message || "任务已成功进入 Aria2 队列。");
  elements.addTaskForm.reset();
  await loadDashboard();
}

async function scanWebdav() {
  const result = await api("/api/webdav/scan", { method: "POST" });
  showMessage("扫描完成", `共发现 ${result.files.length} 个远端文件。`);
  await loadDashboard();
}

async function verifyMd5(remotePath, fileName) {
  const result = await api("/api/webdav/verify-md5", {
    method: "POST",
    body: JSON.stringify({ remote_path: remotePath, local_file_name: fileName }),
  });
  showMessage(
    result.matched ? "MD5 一致" : "MD5 不一致",
    `${result.local_path}\n本地：${result.local_md5}\n远端：${result.remote_md5}`,
  );
}

async function retryUpload(gid) {
  const result = await api(`/api/tasks/${gid}/retry-upload`, { method: "POST" });
  showMessage("重试已触发", result.message || "上传任务已重新启动。");
}

function connectSocket() {
  const protocol = location.protocol === "https:" ? "wss:" : "ws:";
  const socket = new WebSocket(`${protocol}//${location.host}/ws/events`);
  elements.socketState.textContent = "WS 连接中";
  elements.socketState.className = "status-pill idle";

  socket.addEventListener("open", () => {
    state.wsConnected = true;
    elements.socketState.textContent = "WS 已连接";
    elements.socketState.className = "status-pill online";
    stopPolling();
  });

  socket.addEventListener("message", (event) => {
    const payload = JSON.parse(event.data);
    if (payload.type === "snapshot") {
      render(payload.data);
    } else if (payload.type === "log") {
      renderLogs([...(state.logs || []), payload.entry]);
    }
  });

  socket.addEventListener("close", () => {
    state.wsConnected = false;
    elements.socketState.textContent = "WS 已断开";
    elements.socketState.className = "status-pill offline";
    startPolling();
    window.setTimeout(connectSocket, 3000);
  });
}

let _eventsBound = false;
function bindEvents() {
  if (_eventsBound) return;
  _eventsBound = true;
  document.querySelectorAll(".nav-tab").forEach((button) => {
    button.addEventListener("click", () => toggleView(button.dataset.view));
  });

  document.querySelectorAll(".mini-tab").forEach((button) => {
    button.addEventListener("click", () => toggleExplorer(button.dataset.explorer));
  });

  elements.addTaskForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      await submitTask(false);
    } catch (error) {
      showMessage("下发失败", error.message);
    }
  });

  elements.forceAddButton.addEventListener("click", async () => {
    try {
      await submitTask(true);
    } catch (error) {
      showMessage("强制下发失败", error.message);
    }
  });

  elements.scanWebdavButton.addEventListener("click", async () => {
    try {
      await scanWebdav();
    } catch (error) {
      showMessage("扫描失败", error.message);
    }
  });

  elements.refreshLocalButton.addEventListener("click", async () => {
    try {
      const result = await api("/api/local/refresh", { method: "POST" });
      showMessage("本地已刷新", `共发现 ${result.files.length} 个本地文件。`);
    } catch (error) {
      showMessage("刷新失败", error.message);
    }
  });

  document.body.addEventListener("click", async (event) => {
    // ── Aria2 任务操作 ──
    const verifyButton = event.target.closest(".verify-md5-button");
    if (verifyButton) {
      try {
        await verifyMd5(verifyButton.dataset.remotePath, verifyButton.dataset.fileName);
      } catch (error) {
        showMessage("校验失败", error.message);
      }
      return;
    }

    const retryButton = event.target.closest(".retry-upload-button");
    if (retryButton) {
      try {
        await retryUpload(retryButton.dataset.gid);
      } catch (error) {
        showMessage("重试失败", error.message);
      }
      return;
    }

    // ── 追番订阅操作 ──
    const subToggle = event.target.closest(".sub-toggle");
    if (subToggle) {
      try {
        await api(`/api/subscriptions/${subToggle.dataset.id}/toggle`, { method: "POST" });
        await loadDashboard();
      } catch (error) {
        showMessage("操作失败", error.message);
      }
      return;
    }

    const subEdit = event.target.closest(".sub-edit");
    if (subEdit) {
      const subs = (state.snapshot && state.snapshot.subscriptions) || [];
      const sub = subs.find((s) => s.id === subEdit.dataset.id);
      if (sub) openSubscriptionDialog(sub);
      return;
    }

    const subCheck = event.target.closest(".sub-check");
    if (subCheck) {
      try {
        await api(`/api/subscriptions/${subCheck.dataset.id}/check`, { method: "POST" });
        showMessage("检查完成", "单个订阅检查已完成，请查看日志。");
        await loadDashboard();
      } catch (error) {
        showMessage("检查失败", error.message);
      }
      return;
    }

    const subDelete = event.target.closest(".sub-delete");
    if (subDelete) {
      if (!confirm("确定删除该追番订阅？已记录的下载历史将丢失。")) return;
      try {
        await api(`/api/subscriptions/${subDelete.dataset.id}`, { method: "DELETE" });
        await loadDashboard();
      } catch (error) {
        showMessage("删除失败", error.message);
      }
    }
  });

  // ── 追番订阅表单事件 ────────────────────────────────────

  elements.addSubscriptionBtn.addEventListener("click", () => openSubscriptionDialog(null));

  elements.rssCheckAllBtn.addEventListener("click", async () => {
    try {
      await api("/api/rss/check", { method: "POST" });
      showMessage("检查完成", "全部 RSS 检查已完成，请查看日志。");
    } catch (error) {
      showMessage("检查失败", error.message);
    }
  });

  elements.parseMikanBtn.addEventListener("click", async () => {
    const url = document.getElementById("subMikanUrl").value.trim();
    if (!url) { showMessage("请输入 Mikan URL", "请先填写 Mikan 页面地址。"); return; }
    try {
      const result = await api("/api/mikan/parse", { method: "POST", body: JSON.stringify({ url }) });
      if (result.rss_url) document.getElementById("subRssUrl").value = result.rss_url;
      if (result.bangumi_id) showMessage("解析成功", `BangumiId: ${result.bangumi_id}, SubgroupId: ${result.subgroup_id || "无"}`);
    } catch (error) {
      showMessage("解析失败", error.message);
    }
  });

  // TMDB 元数据获取按钮
  const fetchTmdbBtn = document.getElementById("fetchTmdbBtn");
  if (fetchTmdbBtn) {
    fetchTmdbBtn.addEventListener("click", async () => {
      const tmdbId = document.getElementById("subTmdbId").value.trim();
      if (!tmdbId) { showMessage("请输入 TMDB ID", "请先填写 TMDB ID。"); return; }
      try {
        const result = await api("/api/tmdb/fetch", { method: "POST", body: JSON.stringify({ tmdb_id: tmdbId }) });
        if (result.poster_url) {
          showMessage("获取成功", `封面：${result.poster_url}\n简介：${(result.description || "").substring(0, 100)}...`);
        } else {
          showMessage("获取成功", `已获取 TMDB 信息，但无封面图片。`);
        }
      } catch (error) {
        showMessage("获取失败", error.message);
      }
    });
  }

  // Bangumi 信息获取按钮
  const fetchBangumiBtn = document.getElementById("fetchBangumiBtn");
  if (fetchBangumiBtn) {
    fetchBangumiBtn.addEventListener("click", async () => {
      const bangumiUrl = document.getElementById("subBangumiUrl").value.trim();
      if (!bangumiUrl) { showMessage("请输入 Bangumi URL", "请先填写 Bangumi 页面地址。"); return; }
      try {
        const result = await api("/api/bangumi/fetch", { method: "POST", body: JSON.stringify({ url: bangumiUrl }) });
        showMessage("获取成功", `名称：${result.name || "未知"}\n简介：${(result.summary || "").substring(0, 100)}...`);
      } catch (error) {
        showMessage("获取失败", error.message);
      }
    });
  }

  elements.subscriptionForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = elements.subscriptionForm;
    const subId = document.getElementById("subId").value;
    const payload = {
      name: document.getElementById("subName").value.trim(),
      rss_url: document.getElementById("subRssUrl").value.trim(),
      mikan_url: document.getElementById("subMikanUrl").value.trim(),
      standby_rss_url: document.getElementById("subStandbyRssUrl").value.trim(),
      tmdb_id: document.getElementById("subTmdbId").value.trim(),
      bangumi_url: document.getElementById("subBangumiUrl").value.trim(),
      match_pattern: document.getElementById("subMatchPattern").value.trim(),
      exclude_pattern: document.getElementById("subExcludePattern").value.trim(),
      episode_regex: document.getElementById("subEpisodeRegex").value.trim(),
      episode_group_index: Number(document.getElementById("subEpisodeGroupIndex").value) || 1,
      episode_offset: Number(document.getElementById("subEpisodeOffset").value) || 0,
      season: Number(document.getElementById("subSeason").value) || 1,
      total_episodes: Number(document.getElementById("subTotalEpisodes").value) || 0,
      download_dir_template: document.getElementById("subDownloadDirTemplate").value.trim(),
      slacking_days: Number(document.getElementById("subSlackingDays").value) || 0,
      auto_disable_when_complete: document.getElementById("subAutoDisable").checked,
      skip_half_episodes: document.getElementById("subSkipHalf").checked,
      download_only_latest: document.getElementById("subOnlyLatest").checked,
      omission_detection: document.getElementById("subOmissionDetect").checked,
      notify_on_download: document.getElementById("subNotifyDownload").checked,
      notify_on_complete: document.getElementById("subNotifyComplete").checked,
      notify_on_missing: document.getElementById("subNotifyMissing").checked,
    };
    if (!payload.name) { showMessage("请输入订阅名称", "名称不能为空。"); return; }
    try {
      if (subId) {
        await api(`/api/subscriptions/${subId}`, { method: "PUT", body: JSON.stringify(payload) });
      } else {
        await api("/api/subscriptions", { method: "POST", body: JSON.stringify(payload) });
      }
      elements.subscriptionDialog.close();
      await loadDashboard();
    } catch (error) {
      showMessage("保存失败", error.message);
    }
  });

  // Episode preset for subscription dialog
  if (elements.subEpisodePreset) {
    elements.subEpisodePreset.addEventListener("change", () => {
      document.getElementById("subEpisodeRegex").value = elements.subEpisodePreset.value;
    });
  }

  // ── 集成 / 通知设置表单 ─────────────────────────────────

  if (elements.integrationForm) {
    elements.integrationForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      const payload = Object.fromEntries(new FormData(elements.integrationForm).entries());
      try {
        await api("/api/settings", { method: "PUT", body: JSON.stringify(payload) });
        showMessage("集成设置已保存", "TMDB / Bangumi / Mikan 配置已更新。");
      } catch (error) {
        showMessage("保存失败", error.message);
      }
    });
  }

  if (elements.notifyForm) {
    elements.notifyForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      const payload = Object.fromEntries(new FormData(elements.notifyForm).entries());
      payload.notify_email_smtp_port = Number(payload.notify_email_smtp_port) || 465;
      try {
        await api("/api/settings", { method: "PUT", body: JSON.stringify(payload) });
        showMessage("通知设置已保存", "推送通知配置已更新。");
      } catch (error) {
        showMessage("保存失败", error.message);
      }
    });
  }

  if (elements.testNotifyBtn) {
    elements.testNotifyBtn.addEventListener("click", async () => {
      try {
        await api("/api/notify/test", { method: "POST", body: JSON.stringify({}) });
        showMessage("测试通知已发送", "请检查 Telegram / 邮箱 / Server酱 是否收到通知。");
      } catch (error) {
        showMessage("发送失败", error.message);
      }
    });
  }

  elements.settingsForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const payload = Object.fromEntries(new FormData(elements.settingsForm).entries());
    payload.webdav_scan_depth = Number(payload.webdav_scan_depth);
    payload.webdav_scan_ttl_seconds = Number(payload.webdav_scan_ttl_seconds);
    payload.aria2_poll_interval_seconds = Number(payload.aria2_poll_interval_seconds);
    payload.api_port = Number(payload.api_port);
    payload.rss_poll_interval_minutes = Number(payload.rss_poll_interval_minutes) || 15;
    const tokenInput = elements.settingsForm.elements.namedItem("api_token");
    if (tokenInput && tokenInput.value && tokenInput.value !== "***") {
      state.apiToken = tokenInput.value;
    }
    try {
      await api("/api/settings", {
        method: "PUT",
        body: JSON.stringify(payload),
      });
      showMessage("设置已保存", "配置已写入 data/settings.json。若修改了 API 端口，请重启服务。");
    } catch (error) {
      showMessage("保存失败", error.message);
    }
  });

  elements.changePasswordForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = elements.changePasswordForm;
    const old_password = form.elements.namedItem("old_password").value;
    const new_password = form.elements.namedItem("new_password").value;
    const confirm = form.elements.namedItem("confirm_password").value;
    if (new_password !== confirm) {
      showMessage("修改失败", "两次输入的新密码不一致");
      return;
    }
    try {
      await api("/api/auth/change-password", {
        method: "POST",
        body: JSON.stringify({ old_password, new_password }),
      });
      showMessage("修改成功", "密码已修改，请重新登录");
      localStorage.removeItem("authToken");
      localStorage.removeItem("username");
      state.apiToken = "";
      state.username = "";
      setTimeout(() => location.reload(), 1500);
    } catch (error) {
      showMessage("修改失败", error.message);
    }
    form.reset();
  });

  elements.themeToggle.addEventListener("click", () => {
    const current = document.body.dataset.theme;
    document.body.dataset.theme = current === "dark" ? "light" : "dark";
  });

}

function startPolling() {
  if (state.pollTimer) return;
  state.pollTimer = window.setInterval(() => {
    loadDashboard().catch((error) => {
      console.error(error);
    });
  }, 8000);
}

function stopPolling() {
  if (state.pollTimer) {
    window.clearInterval(state.pollTimer);
    state.pollTimer = null;
  }
}

async function boot() {
  // 绑定认证事件（首次加载时）
  bindAuthEvents();

  // 检查认证状态
  const authStatus = await checkAuthStatus();
  if (!authStatus) {
    return;
  }

  bindEvents();
  toggleView("smart");
  toggleExplorer("remote");
  connectSocket();
  loadRssPresets();
  try {
    await loadDashboard();
  } catch (error) {
    showMessage("初始化失败", error.message);
  }
  if (!state.wsConnected) {
    startPolling();
  }
}

async function checkAuthStatus() {
  try {
    const response = await fetch("/api/auth/status");
    const data = await response.json();

    if (!data.has_auth) {
      // 未设置账户，显示设置页面
      showAuthPage("setup");
      return false;
    }

    // 已设置账户，检查是否已登录
    if (state.apiToken) {
      // 已登录，显示主应用
      showMainApp();
      return true;
    }

    // 未登录，显示登录页面
    showAuthPage("login");
    return false;
  } catch (error) {
    console.error("认证状态检查失败:", error);
    showMessage("错误", "无法检查认证状态");
    return false;
  }
}

function showAuthPage(mode) {
  elements.authPage.style.display = "flex";
  elements.mainApp.style.display = "none";
  elements.authError.textContent = "";

  if (mode === "setup") {
    elements.authTitle.textContent = "首次使用，请设置管理账户";
    elements.authSubmit.textContent = "设置账户";
    elements.authForm.dataset.mode = "setup";
  } else {
    elements.authTitle.textContent = "登录管理后台";
    elements.authSubmit.textContent = "登录";
    elements.authForm.dataset.mode = "login";
  }
}

function showMainApp() {
  elements.authPage.style.display = "none";
  elements.mainApp.style.display = "block";
}

let _authBound = false;
function bindAuthEvents() {
  if (_authBound) return;
  _authBound = true;
  elements.authForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const username = elements.authUsername.value.trim();
    const password = elements.authPassword.value.trim();
    const mode = elements.authForm.dataset.mode;

    if (!username || !password) {
      elements.authError.textContent = "用户名和密码不能为空";
      return;
    }

    try {
      const endpoint = mode === "setup" ? "/api/auth/setup" : "/api/auth/login";
      const response = await fetch(endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, password }),
      });

      const data = await response.json();

      if (!response.ok) {
        elements.authError.textContent = data.detail || "操作失败";
        return;
      }

      if (mode === "login") {
        // 登录成功，保存 token
        state.apiToken = data.token;
        state.username = data.username;
        localStorage.setItem("authToken", data.token);
        localStorage.setItem("username", data.username);
      }

      // 切换到主应用
      showMainApp();
      boot();
    } catch (error) {
      elements.authError.textContent = "网络错误，请重试";
    }
  });

  elements.exportButton.addEventListener("click", async () => {
    try {
      const response = await fetch("/api/export");
      if (!response.ok) {
        throw new Error("导出失败");
      }
      const blob = await response.blob();
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "aria2-plus-export.zip";
      document.body.appendChild(a);
      a.click();
      window.URL.revokeObjectURL(url);
      a.remove();
      showMessage("导出成功", "配置文件已下载");
    } catch (error) {
      showMessage("导出失败", error.message);
    }
  });

  elements.importButton.addEventListener("click", () => {
    elements.importFile.click();
  });

  elements.importFile.addEventListener("change", async (event) => {
    const file = event.target.files[0];
    if (!file) return;
    try {
      const formData = new FormData();
      formData.append("file", file);
      const headers = {};
      if (state.apiToken) {
        headers["X-Api-Token"] = state.apiToken;
      }
      const response = await fetch("/api/import", {
        method: "POST",
        headers,
        body: formData,
      });
      const data = await response.json();
      if (!response.ok) {
        throw new Error(data.detail || "导入失败");
      }
      showMessage("导入成功", "配置已恢复，请重新登录", data.detail || "");
      localStorage.removeItem("authToken");
      localStorage.removeItem("username");
      state.apiToken = "";
      state.username = "";
      setTimeout(() => location.reload(), 1500);
    } catch (error) {
      showMessage("导入失败", error.message);
    }
    event.target.value = "";
  });

  elements.logoutButton.addEventListener("click", async () => {
    try {
      await fetch("/api/auth/logout", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Api-Token": state.apiToken,
        },
      });
    } catch (error) {
      // 忽略错误
    }

    // 清除本地状态
    state.apiToken = "";
    state.username = "";
    localStorage.removeItem("authToken");
    localStorage.removeItem("username");

    // 停止轮询
    stopPolling();

    // 显示登录页面
    showAuthPage("login");
  });
}

boot();
