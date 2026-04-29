const state = {
  snapshot: null,
  currentView: "smart",
  currentExplorer: "remote",
  logs: [],
  wsConnected: false,
  pollTimer: null,
  apiToken: "",
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
  dialog: document.getElementById("feedbackDialog"),
  dialogBody: document.getElementById("dialogBody"),
  socketState: document.getElementById("socketState"),
  scanMeta: document.getElementById("scanMeta"),
  themeToggle: document.getElementById("themeToggle"),
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
  Object.entries(settings).forEach(([key, value]) => {
    const input = elements.settingsForm.elements.namedItem(key);
    if (!input) {
      return;
    }
    if (input.type === "password" && value === "***") {
      input.placeholder = "已设置，留空保持不变";
      input.value = "";
      return;
    }
    input.value = value;
  });
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

function bindEvents() {
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
    }
  });

  elements.settingsForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const payload = Object.fromEntries(new FormData(elements.settingsForm).entries());
    payload.webdav_scan_depth = Number(payload.webdav_scan_depth);
    payload.webdav_scan_ttl_seconds = Number(payload.webdav_scan_ttl_seconds);
    payload.aria2_poll_interval_seconds = Number(payload.aria2_poll_interval_seconds);
    payload.api_port = Number(payload.api_port);
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
  bindEvents();
  toggleView("smart");
  toggleExplorer("remote");
  connectSocket();
  try {
    await loadDashboard();
  } catch (error) {
    showMessage("初始化失败", error.message);
  }
  if (!state.wsConnected) {
    startPolling();
  }
}

boot();
