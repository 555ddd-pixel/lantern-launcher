/* Lantern Launcher 前端逻辑：无框架，直接操作 DOM。 */

const APP_TITLE = document.title.replace(" · Minecraft 启动器", "");

const state = {
  versions: [],
  local: [],
  config: {},
  latest: { release: "", snapshot: "" },
  filter: "release",
  query: "",
  taskRunning: false,
  gameRunning: false,
  gameVersion: "",
  lastConsoleSig: "",
  booted: false,
  appVersion: "",
  update: {},
  liveSeen: 0,
  bannerDismissed: false,
};

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

window.addEventListener("error", (e) => {
  document.title = "ERR: " + e.message + " @ " + (e.filename || "").split("/").pop() + ":" + e.lineno;
  const el = document.createElement("div");
  el.style.cssText = "position:fixed;top:0;left:0;right:0;z-index:9999;background:#d1483f;color:#fff;font:12px monospace;padding:6px 10px;";
  el.textContent = "JS错误: " + e.message + " (" + (e.filename || "").split("/").pop() + ":" + e.lineno + ")";
  document.body ? document.body.appendChild(el) : addEventListener("DOMContentLoaded", () => document.body.appendChild(el));
});

/* ---------------- 工具 ---------------- */

async function api(path, opts) {
  const res = await fetch(path, Object.assign({
    headers: { "Content-Type": "application/json" },
  }, opts || {}));
  const data = await res.json().catch(() => ({}));
  if (!res.ok || data.ok === false) throw new Error(data.error || `请求失败 (${res.status})`);
  return data;
}

function toast(msg, kind) {
  const el = document.createElement("div");
  el.className = "toast" + (kind ? " " + kind : "");
  el.textContent = msg;
  $("#toasts").appendChild(el);
  setTimeout(() => {
    el.style.transition = "opacity .25s, transform .25s";
    el.style.opacity = "0";
    el.style.transform = "translateX(12px)";
    setTimeout(() => el.remove(), 260);
  }, 3600);
}

function fmtBytes(n) {
  if (!n) return "0 B";
  const u = ["B", "KB", "MB", "GB"];
  let i = 0, v = n;
  while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
  return v.toFixed(v >= 100 || i === 0 ? 0 : 1) + " " + u[i];
}

function fmtDate(s) {
  if (!s) return "";
  const d = new Date(s);
  if (isNaN(d)) return s;
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

function esc(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

/* ---------------- 导航 ---------------- */

function go(page) {
  if (!document.getElementById("page-" + page)) page = "home";
  if (location.hash !== "#" + page) history.replaceState(null, "", "#" + page);
  $$(".nav-item").forEach((b) => b.classList.toggle("active", b.dataset.page === page));
  $$(".page").forEach((p) => p.classList.toggle("active", p.id === "page-" + page));
  $(".main").scrollTop = 0;
  document.title = (PAGE_TITLES[page] ? PAGE_TITLES[page] + " · " : "") + APP_TITLE;
  if (page === "logs") refreshConsole(true);
  if (page === "settings") loadJava();
}

$("#nav").addEventListener("click", (e) => {
  const btn = e.target.closest(".nav-item");
  if (btn) go(btn.dataset.page);
});
$("#goVersions").addEventListener("click", () => go("versions"));
$("#heroDownload").addEventListener("click", () => go("download"));
$("#heroLaunch").addEventListener("click", () => {
  const target = state.gameRunning ? null : (state.local[0] ? state.local[0].id : null);
  if (!target) { go("download"); toast("先下载一个版本吧", "err"); return; }
  launchVersion(target);
});

/* ---------------- 数据加载 ---------------- */

async function loadConfig() {
  const { config } = await api("/api/config");
  state.config = config;
  fillSettings();
  renderAccount();
}

async function loadVersions(force) {
  try {
    const data = await api("/api/versions" + (force ? "?refresh=1" : ""));
    state.versions = data.versions;
    state.latest = { release: data.latest_release, snapshot: data.latest_snapshot };
    $("#statVersions").textContent = data.count;
    $("#statLatest").textContent = data.latest_release || "—";
    renderVersionList();
    renderHome();
  } catch (e) {
    $("#versionList").innerHTML = `<div class="empty">读取版本清单失败：${esc(e.message)}</div>`;
    toast("读取版本清单失败：" + e.message, "err");
  }
}

async function loadLocal() {
  const data = await api("/api/local");
  state.local = data.versions;
  $("#statInstalled").textContent = data.versions.length;
  renderLocal();
  renderHome();
  renderVersionList();
}

async function loadJava() {
  try {
    const data = await api("/api/java");
    const box = $("#javaBox");
    if (!data.runtimes.length) {
      box.innerHTML = `<div class="java-item"><span class="muted">未检测到 Java，安装时会自动下载</span></div>`;
      return;
    }
    box.innerHTML = data.runtimes.slice(0, 5).map((r) => `
      <div class="java-item">
        <span class="mono" title="${esc(r.path)}">Java ${r.major}${r.vendor ? " · " + esc(r.vendor) : ""}</span>
        <span class="muted mono">${esc(r.path)}</span>
      </div>`).join("");
  } catch (e) { /* 忽略 */ }
}

/* ---------------- 主页 ---------------- */

function renderHome() {
  const installed = state.local;
  const latest = state.latest.release;
  $("#statMemory").textContent = (state.config.memory_mb || 2048) + " MB";

  const quick = $("#quickList");
  if (!installed.length) {
    quick.innerHTML = `<div class="empty">还没有安装任何版本，去「下载」页面挑一个吧。</div>`;
    $("#heroTitle").textContent = "开始你的世界";
    $("#heroSub").textContent = latest
      ? `最新正式版 ${latest} 还没下载，点右侧按钮开始。`
      : "选择任意 Minecraft 版本，一键下载并启动。";
    $("#heroLaunch").disabled = true;
    $("#heroKicker").textContent = "准备就绪";
    return;
  }
  $("#heroLaunch").disabled = false;
  const first = installed[0];
  $("#heroTitle").textContent = first.id;
  $("#heroKicker").textContent = state.gameRunning ? "游戏运行中" : "可以启动";
  $("#heroSub").textContent = `Java ${first.java} · ${fmtBytes(first.size)} · ${first.loader || "原版"}`;

  quick.innerHTML = installed.slice(0, 5).map((v) => `
    <div class="quick-item">
      <div class="v-icon">${v.id.replace(/[^0-9]/g, "").slice(0, 2) || "MC"}</div>
      <div class="v-meta">
        <strong>${esc(v.id)}</strong>
        <span>${esc(v.loader || "原版")} · Java ${v.java} · ${fmtBytes(v.size)}</span>
      </div>
      <button class="btn btn-primary btn-mini" data-play="${esc(v.id)}">启动</button>
    </div>`).join("");

  quick.querySelectorAll("[data-play]").forEach((b) => {
    b.addEventListener("click", () => launchVersion(b.dataset.play));
  });
}

/* ---------------- 下载页 ---------------- */

function currentList() {
  let list = state.versions;
  if (state.filter === "release") list = list.filter((v) => v.type === "release");
  else if (state.filter === "snapshot") list = list.filter((v) => v.type === "snapshot");
  else if (state.filter === "old") list = list.filter((v) => v.type === "old_beta" || v.type === "old_alpha");

  const q = state.query.trim().toLowerCase();
  if (q) list = list.filter((v) => v.id.toLowerCase().includes(q));
  return list;
}

function renderVersionList() {
  const list = currentList();
  const box = $("#versionList");
  if (!state.versions.length) {
    box.innerHTML = `<div class="empty">正在读取版本清单…</div>`;
    return;
  }
  if (!list.length) {
    box.innerHTML = `<div class="empty">没有匹配的版本</div>`;
    $("#versionCount").textContent = "";
    return;
  }
  const shown = list.slice(0, 400);
  const installedIds = new Set(state.local.map((v) => v.id));
  box.innerHTML = shown.map((v) => {
    const local = installedIds.has(v.id);
    const typeCls = v.type === "snapshot" ? "snapshot" : (v.type.startsWith("old") ? "old" : "");
    return `<div class="version-row">
      <span class="pill ${typeCls}">${esc(v.type_label)}</span>
      <span class="v-name">${esc(v.id)}</span>
      <span class="v-time">${fmtDate(v.release_time)}</span>
      ${local ? '<span class="pill local">已安装</span>' : ""}
      <span class="v-actions">
        <button class="btn btn-ghost btn-mini" data-install="${esc(v.id)}">${local ? "重新下载" : "下载"}</button>
        ${local ? `<button class="btn btn-primary btn-mini" data-play="${esc(v.id)}">启动</button>` : ""}
      </span>
    </div>`;
  }).join("");

  $("#versionCount").textContent = list.length > shown.length
    ? `共 ${list.length} 个版本，仅显示前 ${shown.length} 个，请用搜索缩小范围`
    : `共 ${list.length} 个版本`;

  box.querySelectorAll("[data-install]").forEach((b) => {
    b.addEventListener("click", () => installVersion(b.dataset.install));
  });
  box.querySelectorAll("[data-play]").forEach((b) => {
    b.addEventListener("click", () => launchVersion(b.dataset.play));
  });
}

$("#typeTabs").addEventListener("click", (e) => {
  const tab = e.target.closest(".tab");
  if (!tab) return;
  $$("#typeTabs .tab").forEach((t) => t.classList.toggle("active", t === tab));
  state.filter = tab.dataset.type;
  renderVersionList();
});

$("#searchInput").addEventListener("input", (e) => {
  state.query = e.target.value;
  renderVersionList();
});

$("#refreshVersions").addEventListener("click", () => {
  toast("正在刷新版本清单…");
  loadVersions(true);
});

/* ---------------- 已安装版本 ---------------- */

function renderLocal() {
  const box = $("#localList");
  $("#gameDirLabel").textContent = state.config.game_dir || "";
  if (!state.local.length) {
    box.innerHTML = `<div class="empty">暂无已安装版本。</div>`;
    return;
  }
  box.innerHTML = state.local.map((v) => `
    <div class="card">
      <div class="card-top">
        <span class="card-title">${esc(v.id)}</span>
        <span class="pill ${v.has_jar ? "local" : ""}">${v.has_jar ? "可启动" : "文件不完整"}</span>
      </div>
      <div class="card-sub">${esc(v.loader || "原版")} · Java ${v.java} · ${fmtBytes(v.size)}</div>
      <div class="card-actions">
        <button class="btn btn-primary btn-mini" data-play="${esc(v.id)}">启动</button>
        <button class="btn btn-ghost btn-mini" data-repair="${esc(v.id)}">补全文件</button>
        <button class="btn btn-ghost btn-mini" data-del="${esc(v.id)}">删除</button>
      </div>
    </div>`).join("");

  box.querySelectorAll("[data-play]").forEach((b) => b.addEventListener("click", () => launchVersion(b.dataset.play)));
  box.querySelectorAll("[data-repair]").forEach((b) => b.addEventListener("click", () => installVersion(b.dataset.repair)));
  box.querySelectorAll("[data-del]").forEach((b) => b.addEventListener("click", async () => {
    if (!confirm(`确定删除版本 ${b.dataset.del}？游戏存档不会被删除。`)) return;
    await api("/api/version/delete", { method: "POST", body: JSON.stringify({ id: b.dataset.del }) });
    toast("已删除 " + b.dataset.del, "ok");
    loadLocal();
  }));
}

$("#openGameDir").addEventListener("click", () => api("/api/open", {
  method: "POST", body: JSON.stringify({ target: "game_dir" }),
}).catch((e) => toast(e.message, "err")));

/* ---------------- 安装 / 启动 ---------------- */

async function installVersion(id) {
  try {
    $("#taskbar").classList.add("show");
    $("#taskTitle").textContent = "准备下载 " + id;
    $("#taskSub").textContent = "";
    $("#taskFill").style.width = "2%";
    $("#taskPercent").textContent = "0%";
    await api("/api/install", { method: "POST", body: JSON.stringify({ id }) });
    toast("开始下载 " + id);
  } catch (e) {
    toast(e.message, "err");
    $("#taskbar").classList.remove("show");
  }
}

async function launchVersion(id) {
  try {
    toast("正在启动 " + id + "…");
    await api("/api/launch", { method: "POST", body: JSON.stringify({ id }) });
    go("logs");
  } catch (e) {
    toast("启动失败：" + e.message, "err");
  }
}

$("#taskCancel").addEventListener("click", async () => {
  await api("/api/cancel", { method: "POST", body: "{}" }).catch(() => {});
});

/* ---------------- 状态：SSE 实时推送 + 轮询兜底 ---------------- */

let sse = null;
let pollTimer = null;

function sleep(ms) { return new Promise((r) => setTimeout(r, ms)); }

function setConnState(ok) {
  [$("#liveDot"), $("#liveDot2")].forEach((d) => {
    if (!d) return;
    d.classList.toggle("on", ok);
    d.classList.toggle("off", !ok);
    d.title = "实时连接：" + (ok ? "已连接" : "已断开");
  });
  const t = $("#liveText");
  if (t) t.textContent = ok
    ? "实时连接中 · 安装进度与游戏日志即时推送"
    : "实时连接已断开 · 已回退为轮询模式";
}

function connectStream() {
  // ?nosse=1 用于诊断：强制走轮询
  if (!window.EventSource || new URLSearchParams(location.search).has("nosse")) {
    startPolling();
    return;
  }
  try {
    sse = new EventSource("/api/stream");
  } catch (e) { startPolling(); return; }
  sse.onopen = () => setConnState(true);
  sse.onmessage = (ev) => {
    setConnState(true);
    try { applyStatus(JSON.parse(ev.data)); } catch (e) { /* 忽略坏帧 */ }
  };
  sse.onerror = () => {
    setConnState(false);
    const u = state.update || {};
    if (u.applying || u.phase === "applying") { waitForRestart(); return; }
    if (sse) { sse.close(); sse = null; }
    startPolling();
  };
}

function startPolling() {
  if (pollTimer) return;
  pollTimer = setInterval(poll, 900);
  poll();
}

async function poll() {
  try {
    const data = await api("/api/status");
    applyStatus(data);
    if (pollTimer && !sse) setConnState(true);
  } catch (e) {
    /* 服务重启中，静默重试 */
    if (!sse) setConnState(false);
  }
}

function applyStatus(data) {
  const t = data.task;

  // 底部任务条
  if (t.running) {
    $("#taskbar").classList.add("show");
    const p = t.progress || {};
    const pct = Math.round((p.ratio || 0) * 100);
    $("#taskTitle").textContent = t.kind === "java" ? "下载 Java 运行时" : `下载 ${t.version}`;
    $("#taskPercent").textContent = pct + "%";
    $("#taskFill").style.width = Math.max(2, pct) + "%";
    $("#taskSub").textContent = [
      p.total_files ? `${p.done_files}/${p.total_files} 个文件` : "",
      p.total_bytes ? `${fmtBytes(p.done_bytes)} / ${fmtBytes(p.total_bytes)}` : "",
      p.speed ? fmtBytes(p.speed) + "/s" : "",
      p.current || "",
    ].filter(Boolean).join(" · ");
    state.taskRunning = true;
  } else if (state.taskRunning) {
    state.taskRunning = false;
    $("#taskbar").classList.remove("show");
    if (t.error) toast("任务失败：" + t.error, "err");
    else if (t.phase === "完成") toast("下载完成", "ok");
    loadLocal();
    loadVersions(false);
  }

  // 游戏状态
  const wasRunning = state.gameRunning;
  state.gameRunning = data.game.running;
  state.gameVersion = data.game.version;
  if (wasRunning !== state.gameRunning) {
    $("#heroKicker").textContent = state.gameRunning ? "游戏运行中" : "可以启动";
    toast(data.game.running ? `游戏已启动（${data.game.version}）` : "游戏已退出",
      data.game.running ? "ok" : "");
    loadLocal();
  }

  if (data.installed && data.installed.length !== state.local.length) loadLocal();

  // 控制台（只在日志页渲染，避免无谓重排）
  if (document.querySelector("#page-logs").classList.contains("active")) {
    renderConsole(t.logs || [], data.game.logs || []);
  }

  // 版本号 / 更新状态 / 实时版本推送
  if (data.app) {
    state.appVersion = data.app.version;
    $("#appVersion").textContent = "v" + data.app.version;
  }
  if (data.update) renderUpdate(data.update);
  if (data.live) renderLive(data.live);
}

/* ---------------- 启动器更新 ---------------- */

function pctOf(u) {
  const p = u.progress || {};
  if (u.phase === "downloading" && p.total_bytes) return Math.min(100, Math.round((p.ratio || 0) * 100));
  if (u.phase === "ready" || u.downloaded) return 100;
  return 0;
}

function renderUpdate(u) {
  const prev = state.update || {};
  state.update = u;
  const busy = u.phase === "downloading" || u.phase === "applying" || u.applying;
  const ready = u.phase === "ready" || !!u.downloaded;
  const pct = pctOf(u);

  // 左侧版本徽标
  const chip = $("#updateChip");
  if (u.has_update || busy || ready) {
    chip.hidden = false;
    $("#updateChipText").textContent = u.applying ? "正在重启…"
      : u.phase === "downloading" ? `更新中 ${pct}%`
        : ready ? "更新待安装"
          : `新版本 ${u.latest}`;
  } else {
    chip.hidden = true;
  }

  // 顶部横幅
  const banner = $("#updateBanner");
  if (u.has_update && !state.bannerDismissed) {
    banner.hidden = false;
    $("#ubTitle").textContent = `发现新版本 ${u.latest}（当前 v${u.current}）`;
    $("#ubNotes").textContent = u.applying ? "正在替换文件并重启…"
      : u.phase === "downloading" ? `正在下载 ${pct}%（${fmtBytes((u.progress || {}).speed || 0)}/s）`
        : ready ? "已下载完成，点击右侧按钮重启安装"
          : (u.notes || (u.published ? "发布于 " + u.published : ""));
    $("#ubFill").style.width = (u.phase === "downloading" ? Math.max(3, pct) : (ready ? 100 : 0)) + "%";
    $("#ubNow").disabled = busy;
    $("#ubNow").textContent = u.applying ? "重启中…" : ready ? "立即重启更新" : busy ? "下载中…" : "立即更新";
  } else {
    banner.hidden = true;
  }

  // 设置页面板
  if (!$("#upCurrent")) return;
  $("#upCurrent").textContent = "v" + u.current;
  $("#upMeta").textContent = [
    u.frozen ? "已打包（支持自动更新）" : "源码运行",
    u.build ? "构建 " + u.build : "",
    u.source ? "更新源：" + u.source : "",
  ].filter(Boolean).join(" · ");

  let html = "";
  if (u.applying) html = '<span class="tag">正在替换程序并重启，请稍候…</span>';
  else if (u.phase === "downloading") {
    html = `<span class="tag">正在下载 ${u.latest}：${pct}%</span>`
      + (u.progress && u.progress.total_bytes
        ? `<br><span class="muted">${fmtBytes(u.progress.done_bytes)} / ${fmtBytes(u.progress.total_bytes)} · ${fmtBytes(u.progress.speed || 0)}/s</span>`
        : "");
  } else if (ready) html = `<span class="ok">v${u.latest} 已下载完成</span>，点击右侧按钮重启安装。`;
  else if (u.has_update) html = `<span class="tag">发现新版本 v${u.latest}</span>${u.notes ? "<br>" + esc(u.notes) : ""}`;
  else if (u.error) html = `<span class="err">${esc(u.error)}</span>`;
  else if (u.checked_at) html = `<span class="ok">已是最新版本</span> <span class="muted">（检查于 ${new Date(u.checked_at * 1000).toLocaleTimeString()}）</span>`;
  else html = '<span class="muted small">尚未检查</span>';
  $("#upStatus").innerHTML = html;
  $("#upFill").style.width = (u.phase === "downloading" ? Math.max(3, pct) : ready ? 100 : 0) + "%";
  $("#applyUpdateBtn").disabled = (!u.has_update && !ready) || busy;
  $("#applyUpdateBtn").textContent = u.applying ? "重启中…" : ready ? "重启并完成更新" : "下载并重启更新";

  // 下载完成时提示一次
  if (!prev.downloaded && u.downloaded && u.phase === "ready") {
    toast("新版本已下载完成，可点击「立即重启更新」", "ok");
  }
}

async function startUpdate() {
  const u = state.update || {};
  try {
    if (u.phase === "ready" || u.downloaded) {
      if (!confirm("启动器将关闭并自动重启以完成更新，确定继续？")) return;
      await api("/api/update/apply", { method: "POST", body: JSON.stringify({ confirm: true }) });
      waitForRestart();
      return;
    }
    await api("/api/update/download", { method: "POST", body: "{}" });
    toast("开始下载新版本 " + (u.latest || ""));
  } catch (e) {
    toast(e.message, "err");
  }
}

async function waitForRestart() {
  const mask = document.createElement("div");
  mask.className = "restart-mask";
  mask.innerHTML = `<div class="restart-box">
      <div class="spinner"></div>
      <strong>正在更新 Lantern Launcher</strong>
      <span class="muted small" id="restartHint">替换程序文件并重启中，界面会自动恢复…</span>
    </div>`;
  document.body.appendChild(mask);
  for (let i = 0; i < 90; i++) {
    await sleep(800);
    try {
      const r = await fetch("/api/ping", { cache: "no-store" });
      if (r.ok) { location.reload(); return; }
    } catch (e) { /* 还没起来 */ }
  }
  const hint = $("#restartHint");
  if (hint) hint.textContent = "自动恢复超时，请手动重新打开启动器。";
}

/* ---------------- 实时版本推送 ---------------- */

function renderLive(live) {
  const evs = live.events || [];
  const last = evs.length ? evs[evs.length - 1].time : 0;
  if (!state.liveSeen) {
    state.liveSeen = last;   // 首次只记录，不补弹历史
  } else if (last > state.liveSeen) {
    const fresh = evs.filter((e) => e.time > state.liveSeen);
    state.liveSeen = last;
    fresh.forEach((e) => toast(`Minecraft 新${e.label}：${e.id}`, "ok"));
    if (fresh.length) loadVersions(true);
  }
  const t = $("#liveEventText");
  if (t) {
    t.textContent = live.last_check
      ? `清单检查 ${new Date(live.last_check * 1000).toLocaleTimeString()}`
      : "清单检查中…";
  }
}

/* ---------------- 控制台 ---------------- */

function renderConsole(taskLogs, gameLogs, force) {
  const all = [];
  taskLogs.forEach((l) => all.push(l));
  if (gameLogs.length) {
    all.push("");
    gameLogs.forEach((l) => all.push(l));
  }
  const sig = all.length + "|" + (all[all.length - 1] || "");
  if (!force && sig === state.lastConsoleSig) return;
  state.lastConsoleSig = sig;
  const box = $("#console");
  if (!all.length) {
    box.innerHTML = '<span class="muted">（暂无输出）</span>';
    return;
  }
  const nearBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 80;
  box.textContent = all.join("\n");
  if (nearBottom || force) box.scrollTop = box.scrollHeight;
}

async function refreshConsole(force) {
  try {
    const data = await api("/api/status");
    renderConsole(data.task.logs || [], data.game.logs || [], force);
  } catch (e) { /* ignore */ }
}

$("#clearLogs").addEventListener("click", () => {
  $("#console").innerHTML = '<span class="muted">（已清空显示，新输出会继续显示）</span>';
  state.lastConsoleSig = "";
});

/* ---------------- 设置 ---------------- */

function fillSettings() {
  const c = state.config;
  $("#setGameDir").value = c.game_dir || "";
  $("#setName").value = c.player_name || "";
  $("#setMemory").value = c.memory_mb || 2048;
  $("#setWindow").value = `${c.window_width || 854}x${c.window_height || 480}`;
  $("#setMirror").value = c.mirror || "auto";
  $("#setJava").value = c.java_path || "";
  $("#setConcurrent").value = c.concurrent || 16;
  $("#setAutoJava").checked = !!c.auto_download_java;
  $("#setJvmArgs").value = c.jvm_args || "";
  $("#setGameArgs").value = c.game_args || "";
  $("#setMsClient").value = c.ms_client_id || "";
  $("#setUpdateUrl").value = c.update_url || "";
  $("#setAutoCheck").checked = c.auto_check_update !== false;
  $("#setAutoDownload").checked = !!c.auto_download_update;
  $("#setUpdateInterval").value = c.update_interval_min || 30;
  $("#setLiveRefresh").checked = c.live_refresh !== false;
  $("#setCloseToTray").checked = !!c.close_to_tray;
}

$("#saveSettings").addEventListener("click", async () => {
  const win = ($("#setWindow").value || "854x480").split("x");
  const patch = {
    game_dir: $("#setGameDir").value.trim(),
    player_name: $("#setName").value.trim() || "Steve",
    memory_mb: parseInt($("#setMemory").value, 10) || 2048,
    window_width: parseInt(win[0], 10) || 854,
    window_height: parseInt(win[1], 10) || 480,
    mirror: $("#setMirror").value,
    java_path: $("#setJava").value.trim(),
    concurrent: parseInt($("#setConcurrent").value, 10) || 16,
    auto_download_java: $("#setAutoJava").checked,
    jvm_args: $("#setJvmArgs").value.trim(),
    game_args: $("#setGameArgs").value.trim(),
    ms_client_id: $("#setMsClient").value.trim(),
    update_url: $("#setUpdateUrl").value.trim(),
    auto_check_update: $("#setAutoCheck").checked,
    auto_download_update: $("#setAutoDownload").checked,
    update_interval_min: parseInt($("#setUpdateInterval").value, 10) || 30,
    live_refresh: $("#setLiveRefresh").checked,
    close_to_tray: $("#setCloseToTray").checked,
  };
  try {
    const { config } = await api("/api/config", { method: "POST", body: JSON.stringify({ config: patch }) });
    state.config = config;
    $("#saveHint").textContent = "已保存 " + new Date().toLocaleTimeString();
    toast("设置已保存", "ok");
    renderAccount();
    loadLocal();
    if (patch.update_url !== undefined) api("/api/update/check", { method: "POST", body: "{}" }).catch(() => {});
  } catch (e) { toast(e.message, "err"); }
});

/* ---------------- 更新相关按钮 ---------------- */

$("#checkUpdateBtn").addEventListener("click", async () => {
  $("#checkUpdateBtn").disabled = true;
  $("#upStatus").innerHTML = '<span class="muted">正在检查更新…</span>';
  try {
    await api("/api/update/check", { method: "POST", body: "{}" });
  } catch (e) {
    toast(e.message, "err");
  } finally {
    $("#checkUpdateBtn").disabled = false;
  }
});

$("#applyUpdateBtn").addEventListener("click", startUpdate);
$("#ubNow").addEventListener("click", startUpdate);
$("#ubLater").addEventListener("click", () => {
  state.bannerDismissed = true;
  $("#updateBanner").hidden = true;
});
$("#updateChip").addEventListener("click", () => {
  go("settings");
  startUpdate();
});

$("#msLoginBtn").addEventListener("click", async () => {
  const cid = $("#setMsClient").value.trim();
  try {
    await api("/api/account/microsoft", { method: "POST", body: JSON.stringify({ client_id: cid }) });
    toast("已发起登录，请打开「日志」页查看验证码", "ok");
    go("logs");
  } catch (e) { toast(e.message, "err"); }
});

$("#offlineBtn").addEventListener("click", () => { openAccountModal(); });

/* ---------------- 账号 ---------------- */

function renderAccount() {
  const acc = state.config.account || {};
  const name = acc.name || state.config.player_name || "Steve";
  $("#accountName").textContent = name;
  $("#accountType").textContent = acc.type === "microsoft" ? "微软正版" : "离线登录";
  const av = $("#avatar");
  av.textContent = name.slice(0, 1).toUpperCase();
  if (acc.type === "microsoft" && acc.uuid) {
    av.style.backgroundImage = `url(https://mc-heads.net/avatar/${acc.uuid}/64)`;
    av.textContent = "";
  } else {
    av.style.backgroundImage = "";
  }
}

function openAccountModal() {
  $("#modalName").value = state.config.player_name || "Steve";
  $("#accountModal").classList.add("show");
}
$("#accountBtn").addEventListener("click", openAccountModal);
$("#modalClose").addEventListener("click", () => $("#accountModal").classList.remove("show"));
$("#accountModal").addEventListener("click", (e) => {
  if (e.target.id === "accountModal") $("#accountModal").classList.remove("show");
});
$("#modalOffline").addEventListener("click", async () => {
  const name = $("#modalName").value.trim() || "Steve";
  try {
    const data = await api("/api/account/offline", { method: "POST", body: JSON.stringify({ name }) });
    state.config.account = data.account;
    state.config.player_name = name;
    renderAccount();
    $("#accountModal").classList.remove("show");
    toast("已切换为离线账号 " + name, "ok");
  } catch (e) { toast(e.message, "err"); }
});

/* ---------------- 桌面软件模式 ---------------- */

const inWindow = new URLSearchParams(location.search).get("window") === "1";

if (inWindow) {
  document.body.classList.add("in-window");
  $("#winPanel").hidden = false;
}

$("#quitAppBtn")?.addEventListener("click", async () => {
  try { await api("/api/app/quit", { method: "POST", body: "{}" }); }
  catch (_e) { /* 进程退出时连接会断，正常 */ }
});

$("#openDataDirBtn")?.addEventListener("click", async () => {
  try { await api("/api/open", { method: "POST", body: JSON.stringify({ target: "game" }) }); }
  catch (e) { toast(e.message, "err"); }
});

// 窗口模式下拦截 F5/Ctrl+R（软件没有"刷新网页"的概念）
window.addEventListener("keydown", (e) => {
  if (inWindow && (e.key === "F5" || (e.ctrlKey && e.key.toLowerCase() === "r"))) {
    e.preventDefault();
  }
});

const PAGE_TITLES = {
  home: "主页", download: "下载", versions: "版本", settings: "设置", logs: "日志",
};

/* ---------------- 启动 ---------------- */

window.addEventListener("hashchange", () => go(location.hash.replace("#", "")));

(async function boot() {
  try {
    await loadConfig();
    await Promise.all([loadVersions(false), loadLocal()]);
    go(location.hash.replace("#", "") || "home");
    poll();
    connectStream();
  } catch (e) {
    toast("初始化失败：" + e.message, "err");
  }
})();
