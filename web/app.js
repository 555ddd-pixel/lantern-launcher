/* 老张的minecraft启动器 前端逻辑：无框架，直接操作 DOM。 */

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
  currentVersion: "",
  modsVersion: "",
  modsArea: "mods",
  modsData: null,
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
  if (page === "modpack") ensurePacks();
  if (page === "mods") loadMods();
  if (page === "link") loadLink();
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
    b.addEventListener("click", () => openInstall(b.dataset.install));
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
        <span class="card-title">${esc(v.pack || v.id)}</span>
        <span class="pill ${v.has_jar ? "local" : ""}">${v.has_jar ? "可启动" : "文件不完整"}</span>
      </div>
      <div class="card-sub">${v.pack ? esc(v.id) + " · " : ""}${esc(v.loader || "原版")} · Java ${v.java} · ${fmtBytes(v.size)}</div>
      ${v.pack ? `<div class="card-sub">${esc(v.source || "")} 整合包${v.pack_version ? " · " + esc(v.pack_version) : ""}</div>` : ""}
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

/* ---------------- 模组 / 光影 ---------------- */

const MOD_AREAS = { mods: "模组", shaderpacks: "光影", resourcepacks: "资源包" };
const MOD_EMPTY = {
  mods: "这个版本还没有模组。点右上「导入模组」，或把 .jar 直接拖进窗口。",
  shaderpacks: "还没有光影包。切到「光影」后导入 .zip（Iris / OptiFine 会读取它）。",
  resourcepacks: "还没有资源包。切到「资源包」后导入 .zip。",
};

function modVersion() {
  return state.modsVersion || state.currentVersion || "";
}

async function loadMods() {
  if (!state.local.length) {
    try { await loadLocal(); } catch (e) { /* 忽略 */ }
  }
  const ids = state.local.map((v) => v.id);
  if (!state.modsVersion || !ids.includes(state.modsVersion)) {
    state.modsVersion = (state.currentVersion && ids.includes(state.currentVersion))
      ? state.currentVersion : (ids[0] || "");
  }
  const sel = $("#modVersion");
  sel.innerHTML = ids.length
    ? state.local.map((v) => `<option value="${esc(v.id)}">${esc(v.id)} · ${esc(v.loader_label || v.loader || "原版")}${v.mods ? " · " + v.mods + " 模组" : ""}</option>`).join("")
    : '<option value="">还没有安装版本</option>';
  if (state.modsVersion) sel.value = state.modsVersion;
  if (!state.modsVersion) {
    $("#modMeta").hidden = true;
    $("#modBulk").hidden = true;
    $("#modList").innerHTML = '<div class="empty">还没有已安装的版本。先去「下载」页装一个，推荐选 Fabric 或 NeoForge。</div>';
    return;
  }
  await refreshMods();
}

async function refreshMods() {
  const vid = modVersion();
  if (!vid) return;
  $("#modList").innerHTML = '<div class="empty">读取模组列表…</div>';
  try {
    const data = await api("/api/content?version=" + encodeURIComponent(vid));
    state.modsData = data;
    renderModMeta(data);
    renderModList(data);
  } catch (e) {
    $("#modMeta").hidden = true;
    $("#modList").innerHTML = `<div class="empty">读取失败：${esc(e.message)}</div>`;
  }
}

function renderModMeta(d) {
  const meta = $("#modMeta");
  meta.hidden = false;
  const loader = d.loader || "vanilla";
  const label = d.loader_label || LOADER_LABEL[loader] || loader;
  meta.innerHTML = `
    <span class="loader-badge ${loader === "vanilla" ? "plain" : ""}">${esc(label)}</span>
    <span>${d.isolated ? "独立游戏目录" : "共享游戏目录"}</span>
    <span class="mono path" title="${esc(d.game_dir)}">${esc(d.game_dir)}</span>
    ${d.isolated ? "" : '<span class="badge-warn small">未隔离：所有没独立目录的版本共用这里</span>'}
    <span class="mod-meta-actions">
      ${d.isolated ? "" : '<button class="btn btn-ghost btn-mini" id="modIsolate">设为独立目录</button>'}
      <button class="btn btn-ghost btn-mini" id="modOpen">打开目录</button>
    </span>`;
  const openBtn = $("#modOpen");
  if (openBtn) openBtn.addEventListener("click", () => openContentDir(state.modsArea));
  const iso = $("#modIsolate");
  if (iso) iso.addEventListener("click", async () => {
    const vid = modVersion();
    if (!confirm(`把 ${vid} 切到独立游戏目录？\n\n之后它的模组、光影、存档都只属于它自己；原来的存档和设置不会自动搬过去（仍留在原目录）。`)) return;
    try {
      const r = await api("/api/content/isolate", { method: "POST", body: JSON.stringify({ version: vid }) });
      state.modsData = r.state;
      renderModMeta(r.state);
      renderModList(r.state);
      toast("已创建独立目录：" + r.game_dir, "ok");
      loadLocal();
    } catch (e) { toast(e.message, "err"); }
  });
}

function renderModList(d) {
  if (!d) return;
  const area = state.modsArea;
  const box = d.areas[area];
  if (!box) return;
  const items = box.items || [];
  $("#modBulk").hidden = !items.length;
  $("#modCount").textContent = `共 ${box.total} 个${MOD_AREAS[area]} · 已启用 ${box.enabled} 个`;
  const on = items.filter((i) => i.enabled).length;
  $("#modEnableAll").disabled = !items.length || on === items.length;
  $("#modDisableAll").disabled = !on;
  if (!items.length) {
    $("#modList").innerHTML = `<div class="empty">${MOD_EMPTY[area]}</div>`;
    return;
  }
  const instLoader = d.loader || "vanilla";
  $("#modList").innerHTML = items.map((it) => {
    const ml = it.mod_loader || "";
    let badge = "";
    if (ml && area === "mods") {
      const okPair = ml === instLoader || (instLoader === "quilt" && ml === "fabric");
      const label = LOADER_LABEL[ml] || ml;
      badge = instLoader === "vanilla"
        ? `<span class="loader-badge badge-warn">${esc(label)} 模组 · 当前版本没装加载器</span>`
        : okPair ? `<span class="loader-badge plain">${esc(label)}</span>`
        : `<span class="loader-badge badge-warn">${esc(label)} · 与本版本加载器不同</span>`;
    }
    const desc = (it.description || "").replace(/\s+/g, " ").slice(0, 80);
    return `<div class="mod-row ${it.enabled ? "" : "off"}" data-file="${esc(it.file)}">
      <div class="mod-info">
        <div class="mod-title"><span>${esc(it.title || it.name)}</span>${it.version ? `<span class="mod-ver">${esc(it.version)}</span>` : ""}${badge}</div>
        <div class="mod-sub"><span class="mono">${esc(it.name)}</span><span>·</span><span>${fmtBytes(it.size)}</span>${desc ? `<span>·</span><span>${esc(desc)}</span>` : ""}</div>
      </div>
      <button class="switch ${it.enabled ? "on" : ""}" data-toggle="${esc(it.file)}" title="${it.enabled ? "点击停用" : "点击启用"}"></button>
      <button class="mod-del" data-del="${esc(it.file)}" title="删除（移到回收站）">
        <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.7"><path d="M4 7h16"/><path d="M9 7V5h6v2"/><path d="M6 7l1 13h10l1-13"/></svg>
      </button>
    </div>`;
  }).join("");

  $$("#modList [data-toggle]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const file = btn.dataset.toggle;
      const enabled = !btn.classList.contains("on");
      btn.disabled = true;
      try {
        const r = await api("/api/content/toggle", {
          method: "POST",
          body: JSON.stringify({ version: modVersion(), area: state.modsArea, file, enabled }),
        });
        state.modsData = r.state;
        renderModList(r.state);
        toast(enabled ? "已启用 " + file.replace(/\.disabled$/, "") : "已停用 " + file, "ok");
      } catch (e) {
        toast(e.message, "err");
        btn.disabled = false;
      }
    });
  });

  $$("#modList [data-del]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const file = btn.dataset.del;
      if (!confirm(`把 ${file} 移到回收站？\n（会放进游戏目录的 .lantern-trash，可手动找回）`)) return;
      try {
        const r = await api("/api/content/delete", {
          method: "POST",
          body: JSON.stringify({ version: modVersion(), area: state.modsArea, file }),
        });
        state.modsData = r.state;
        renderModList(r.state);
        toast("已移到回收站", "ok");
        loadLocal();
      } catch (e) { toast(e.message, "err"); }
    });
  });
}

function openContentDir(area) {
  const vid = modVersion();
  if (!vid) { toast("先选一个版本", "err"); return; }
  api("/api/open", { method: "POST", body: JSON.stringify({ target: area || "instance", version: vid }) })
    .catch((e) => toast(e.message, "err"));
}

async function bulkMods(enabled) {
  const vid = modVersion();
  if (!vid) return;
  try {
    const r = await api("/api/content/toggle-all", {
      method: "POST",
      body: JSON.stringify({ version: vid, area: state.modsArea, enabled }),
    });
    state.modsData = r.state;
    renderModList(r.state);
    toast(enabled ? `已启用 ${r.changed} 个${MOD_AREAS[state.modsArea]}` : `已停用 ${r.changed} 个${MOD_AREAS[state.modsArea]}`, "ok");
    if (r.errors && r.errors.length) toast("部分文件失败：" + r.errors[0], "err");
  } catch (e) { toast(e.message, "err"); }
}

async function importFiles(files, kind) {
  const vid = modVersion();
  if (!vid) { toast("先在「模组」页选一个已安装版本", "err"); return; }
  const list = Array.from(files || []);
  if (!list.length) return;
  $("#taskbar").classList.add("show");
  let last = null;
  let asPack = false;
  try {
    for (let i = 0; i < list.length; i++) {
      const f = list[i];
      $("#taskTitle").textContent = "导入 " + f.name;
      $("#taskSub").textContent = `${i + 1}/${list.length} · ${fmtBytes(f.size)}`;
      $("#taskFill").style.width = Math.round((i / list.length) * 100) + "%";
      $("#taskPercent").textContent = `${i + 1}/${list.length}`;
      const qs = new URLSearchParams({ version: vid, kind: kind || "auto" });
      const res = await fetch("/api/content/upload?" + qs.toString(), {
        method: "POST",
        headers: { "Content-Type": "application/octet-stream", "X-Filename": encodeURIComponent(f.name) },
        body: f,
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || data.ok === false) throw new Error(data.error || `上传失败 (${res.status})`);
      if (data.area === "modpack") {
        asPack = true;
        toast(data.message || "识别为整合包，开始安装", "ok");
        break;
      }
      last = data;
    }
    if (asPack) { go("logs"); return; }
    if (last && last.state) {
      state.modsData = last.state;
      if (last.area && last.area !== state.modsArea) {
        state.modsArea = last.area;
        $$("#modTabs .tab").forEach((t) => t.classList.toggle("active", t.dataset.area === last.area));
      }
      renderModMeta(last.state);
      renderModList(last.state);
    } else {
      await refreshMods();
    }
    toast(`已导入 ${list.length} 个文件`, "ok");
    loadLocal();
  } catch (e) {
    toast("导入失败：" + e.message, "err");
  } finally {
    $("#taskbar").classList.remove("show");
    $("#modFile").value = "";
    $("#shaderFile").value = "";
  }
}

$("#modVersion").addEventListener("change", (e) => {
  state.modsVersion = e.target.value;
  refreshMods().then(() => {
    if (modShopOpen()) {
      modShop.vid = modVersion();
      renderModShopHead();
      searchModShop(true);
    }
  });
});

$("#modTabs").addEventListener("click", (e) => {
  const tab = e.target.closest(".tab");
  if (!tab) return;
  state.modsArea = tab.dataset.area;
  $$("#modTabs .tab").forEach((t) => t.classList.toggle("active", t === tab));
  renderModList(state.modsData);
  if (modShopOpen()) {
    modShop.area = state.modsArea;
    $("#modShopArea").value = modShop.area;
    renderModShopHead();
    searchModShop(true);
  }
});

$("#modRefresh").addEventListener("click", () => {
  loadLocal().catch(() => {});
  refreshMods();
});

$("#modOpenDir").addEventListener("click", () => openContentDir(state.modsArea));

$("#modEnableAll").addEventListener("click", () => bulkMods(true));
$("#modDisableAll").addEventListener("click", () => bulkMods(false));

$("#modImport").addEventListener("click", () => $("#modFile").click());
$("#shaderImport").addEventListener("click", () => $("#shaderFile").click());
$("#modFile").addEventListener("change", (e) => importFiles(e.target.files, "mods"));
$("#shaderFile").addEventListener("change", (e) => importFiles(e.target.files, "auto"));

/* ---------------- 在线搜索下载（模组 / 光影 / 资源包） ---------------- */

const MOD_SHOP_AREAS = { mods: "模组", shaderpacks: "光影", resourcepacks: "资源包" };
const modShop = {
  vid: "", q: "", area: "mods", offset: 0, hits: [], total: 0,
  loading: false, timer: null,
};

function modShopOpen() {
  return $("#modShopModal").classList.contains("show");
}

function renderModShopHead() {
  const d = state.modsData || {};
  const mc = d.mc_version || "";
  const loader = d.loader || "vanilla";
  const label = d.loader_label || LOADER_LABEL[loader] || loader;
  $("#modShopFor").textContent = modShop.vid
    ? `· ${modShop.vid}${mc ? "（MC " + mc + "）" : ""}`
    : "";
  const area = modShop.area;
  const hint = $("#modShopHint");
  if (area === "mods" && loader === "vanilla") {
    hint.textContent = "这个版本没装加载器：模组装上也加载不了。先去「下载」页给它装 Fabric / Forge / NeoForge。";
    hint.style.color = "var(--err)";
  } else if (area === "shaderpacks") {
    hint.textContent = `按 MC ${mc || "任意版本"} 筛选光影包；游戏内需要 Iris（Fabric/Quilt）或 OptiFine（Forge）。`;
    hint.style.color = "";
  } else if (area === "mods") {
    hint.textContent = `只显示兼容 MC ${mc || "任意版本"} + ${label} 的模组。`;
    hint.style.color = "";
  } else {
    hint.textContent = `按 MC ${mc || "任意版本"} 筛选资源包。`;
    hint.style.color = "";
  }
}

async function openModShop() {
  const vid = modVersion();
  if (!vid) { toast("先选一个已安装的版本", "err"); return; }
  if (!state.modsData) await refreshMods();
  modShop.vid = vid;
  modShop.area = state.modsArea;
  modShop.q = $("#modShopQuery").value.trim();
  $("#modShopArea").value = modShop.area;
  $("#modShopSource").value = (state.config && state.config.pack_source) || "auto";
  renderModShopHead();
  $("#modShopModal").classList.add("show");
  searchModShop(true);
}

async function searchModShop(reset) {
  if (modShop.loading) return;
  if (!modShop.vid) return;
  modShop.loading = true;
  if (reset) { modShop.offset = 0; modShop.hits = []; }
  const box = $("#modShopList");
  if (reset) box.innerHTML = '<div class="empty">正在搜索…</div>';
  try {
    const qs = new URLSearchParams({
      version: modShop.vid, area: modShop.area, q: modShop.q,
      offset: String(modShop.offset), limit: "20",
    });
    const data = await api("/api/mods/search?" + qs);
    modShop.total = data.total || 0;
    modShop.hits = reset ? (data.hits || []) : modShop.hits.concat(data.hits || []);
    renderModShop();
  } catch (e) {
    box.innerHTML = `<div class="empty">搜索失败：${esc(e.message)}<br><span class="small">国内网络连不上 Modrinth？把上面切到「国内镜像」再试，或在设置里填加速代理。</span></div>`;
    $("#modShopCount").textContent = "";
    $("#modShopMore").hidden = true;
  } finally {
    modShop.loading = false;
  }
}

function renderModShop() {
  const box = $("#modShopList");
  if (!modShop.hits.length) {
    box.innerHTML = `<div class="empty">没有找到匹配的${MOD_SHOP_AREAS[modShop.area]}，换个关键词试试。</div>`;
    $("#modShopCount").textContent = "0 个结果";
    $("#modShopMore").hidden = true;
    return;
  }
  box.innerHTML = modShop.hits.map((h, i) => `
    <div class="shop-row">
      <div class="pack-icon">${h.icon ? `<img src="${esc(h.icon)}" data-alt="${esc(h.icon_mirror || "")}" onerror="if(this.dataset.alt){this.onerror=null;this.src=this.dataset.alt}" alt="" loading="lazy" referrerpolicy="no-referrer">` : "📦"}</div>
      <div class="shop-info">
        <div class="shop-name">
          <strong title="${esc(h.title)}">${esc(h.title)}</strong>
          ${h.author ? `<span class="mod-ver">${esc(h.author)}</span>` : ""}
        </div>
        <div class="shop-desc" title="${esc(h.description || "")}">${esc(h.description || "")}</div>
        <div class="mod-sub">
          <span>↓ ${fmtNum(h.downloads)}</span>
          ${(h.categories || []).slice(0, 3).map((c) => `<span class="pill">${esc(c)}</span>`).join("")}
        </div>
      </div>
      <button class="btn btn-primary btn-mini" data-buy="${i}">下载</button>
    </div>`).join("");
  box.querySelectorAll("[data-buy]").forEach((b) => {
    b.addEventListener("click", () => openModVersions(modShop.hits[Number(b.dataset.buy)]));
  });
  const shown = modShop.hits.length;
  $("#modShopCount").textContent = modShop.total > shown
    ? `共 ${modShop.total} 个，已显示 ${shown} 个`
    : `共 ${shown} 个结果`;
  $("#modShopMore").hidden = shown >= modShop.total;
}

function openModVersions(hit) {
  if (!hit) return;
  const area = modShop.area;
  const d = state.modsData || {};
  const mc = d.mc_version || "";
  const only = area === "mods" ? `MC ${mc || "任意版本"} + ${d.loader_label || d.loader || "原版"}` : `MC ${mc || "任意版本"}`;
  openVerPicker({
    title: hit.title,
    sub: `下载为${MOD_SHOP_AREAS[area]}，只列出兼容 ${only} 的文件。`,
    empty: area === "mods"
      ? `这个项目没有兼容 ${only} 的文件。可以换一个投影，或先给该版本装对应加载器。`
      : `这个项目没有兼容 MC ${mc || "当前版本"} 的文件。`,
    load: async () => {
      const qs = new URLSearchParams({ project: hit.slug || hit.id, area, version: modShop.vid });
      const data = await api("/api/mods/versions?" + qs);
      return data.versions || [];
    },
    onPick: (v) => installMod(hit, v, area),
  });
}

async function installMod(hit, ver, area) {
  const vid = modShop.vid || modVersion();
  if (!vid) { toast("先选一个已安装的版本", "err"); return; }
  try {
    $("#packModal").classList.remove("show");
    $("#taskbar").classList.add("show");
    $("#taskTitle").textContent = "下载 " + hit.title;
    $("#taskSub").textContent = ver.number || ver.name || "";
    $("#taskFill").style.width = "2%";
    $("#taskPercent").textContent = "0%";
    await api("/api/mods/install", {
      method: "POST",
      body: JSON.stringify({ version: vid, file_version: ver.id, area, title: hit.title }),
    });
    toast(`开始下载《${hit.title}》，装好后会自动出现在列表里`, "ok");
  } catch (e) {
    toast(e.message, "err");
    $("#taskbar").classList.remove("show");
  }
}

$("#modOnline").addEventListener("click", () => openModShop().catch((e) => toast(e.message, "err")));
$("#modShopClose").addEventListener("click", () => $("#modShopModal").classList.remove("show"));
$("#modShopModal").addEventListener("click", (e) => {
  if (e.target === $("#modShopModal")) $("#modShopModal").classList.remove("show");
});

$("#modShopQuery").addEventListener("input", (e) => {
  modShop.q = e.target.value.trim();
  if (modShop.timer) clearTimeout(modShop.timer);
  modShop.timer = setTimeout(() => searchModShop(true), 400);
});

$("#modShopArea").addEventListener("change", (e) => {
  modShop.area = e.target.value;
  renderModShopHead();
  searchModShop(true);
});

$("#modShopSource").addEventListener("change", (e) => setPackSource(e.target.value));

$("#modShopMore").addEventListener("click", () => {
  modShop.offset = modShop.hits.length;
  searchModShop(false);
});

/* ---------------- 安装 / 启动 ---------------- */

const LOADER_LABEL = {
  vanilla: "原版", fabric: "Fabric", forge: "Forge",
  neoforge: "NeoForge", quilt: "Quilt",
};

const installState = { id: "", loader: "vanilla", loading: false, cache: {} };

function openInstall(id) {
  installState.id = id;
  installState.loader = "vanilla";
  $("#installTitle").textContent = "安装 " + id;
  $("#installHint").textContent = "原版会装到共享游戏目录；选加载器则会创建独立目录，模组 / 光影 / 存档互不影响。";
  $("#loaderVerBox").hidden = true;
  $("#loaderVersion").innerHTML = "";
  $("#installOk").disabled = false;
  $$("#loaderRow .loader-chip").forEach((c) => {
    c.classList.toggle("active", c.dataset.loader === "vanilla");
    c.removeAttribute("data-unavailable");
    c.title = "";
  });
  $("#installModal").classList.add("show");
  probeLoaders(id);
}

function selectLoader(loader) {
  installState.loader = loader;
  $$("#loaderRow .loader-chip").forEach((c) => c.classList.toggle("active", c.dataset.loader === loader));
  if (loader === "vanilla") {
    $("#loaderVerBox").hidden = true;
    $("#installHint").textContent = "原版会装到共享游戏目录；想装模组的话建议选一个加载器。";
    $("#installOk").disabled = false;
    return;
  }
  $("#loaderVerBox").hidden = false;
  loadLoaderVersions(loader);
}

async function probeLoaders(mc) {
  const hit = installState.cache[mc];
  if (hit) return applyLoaderSummary(hit);
  try {
    const data = await api("/api/loaders/summary?mc=" + encodeURIComponent(mc));
    installState.cache[mc] = data.loaders || {};
    applyLoaderSummary(installState.cache[mc]);
  } catch (e) { /* 探测失败不影响手动点选 */ }
}

function applyLoaderSummary(summary) {
  if (!summary) return;
  $$("#loaderRow .loader-chip").forEach((c) => {
    const key = c.dataset.loader;
    if (key === "vanilla") return;
    const item = summary[key];
    if (!item) return;
    if (item.ok) {
      c.removeAttribute("data-unavailable");
      c.title = `最新 ${item.recommend}` + (item.count ? ` · 共 ${item.count} 个版本` : "");
    } else {
      c.dataset.unavailable = "1";
      c.title = item.error || `${installState.id} 没有 ${LOADER_LABEL[key]} 可用版本`;
    }
  });
}

async function loadLoaderVersions(loader) {
  const mc = installState.id;
  const box = $("#loaderVersion");
  const label = LOADER_LABEL[loader] || loader;
  $("#loaderVerLabel").textContent = `${label} 版本`;
  $("#loaderVerNote").textContent = "";
  box.innerHTML = '<option value="">读取中…</option>';
  $("#installOk").disabled = true;
  $("#installHint").textContent = `正在读取 ${mc} 可用的 ${label} 版本…`;
  try {
    const data = await api(`/api/loaders?mc=${encodeURIComponent(mc)}&loader=${encodeURIComponent(loader)}`);
    if (!data.versions.length) throw new Error(`${mc} 没有可用的 ${label} 版本`);
    box.innerHTML = data.versions.map((v, i) => {
      const tags = [];
      if (i === 0) tags.push("推荐");
      if (v.stable === false) tags.push("测试版");
      return `<option value="${esc(v.version)}">${esc(v.version)}${tags.length ? "  ·  " + tags.join(" / ") : ""}</option>`;
    }).join("");
    $("#loaderVerNote").textContent = data.cached ? "（缓存）" : "";
    $("#installHint").textContent = `将为 ${mc} 安装 ${label}，并创建独立的游戏目录。`;
    $("#installOk").disabled = false;
  } catch (e) {
    box.innerHTML = '<option value="">没有可用版本</option>';
    $("#installHint").textContent = `${label}：${e.message}。可以换一个加载器，或先装原版。`;
    $("#installOk").disabled = true;
  }
}

$$("#loaderRow .loader-chip").forEach((chip) => {
  chip.addEventListener("click", () => selectLoader(chip.dataset.loader));
});

$("#loaderVerRefresh").addEventListener("click", () => {
  if (installState.loader !== "vanilla") loadLoaderVersions(installState.loader);
});

$("#installCancel").addEventListener("click", () => $("#installModal").classList.remove("show"));

$("#installOk").addEventListener("click", async () => {
  const loader = installState.loader;
  const lv = loader === "vanilla" ? "" : ($("#loaderVersion").value || "");
  if (loader !== "vanilla" && !lv) { toast("先选一个加载器版本", "err"); return; }
  $("#installModal").classList.remove("show");
  await installVersion(installState.id, loader, lv);
});

async function installVersion(id, loader, loaderVersion) {
  loader = loader || "vanilla";
  try {
    $("#taskbar").classList.add("show");
    $("#taskTitle").textContent = "准备下载 " + id;
    $("#taskSub").textContent = loader !== "vanilla"
      ? `${LOADER_LABEL[loader]} ${loaderVersion || ""}`.trim() : "原版";
    $("#taskFill").style.width = "2%";
    $("#taskPercent").textContent = "0%";
    await api("/api/install", {
      method: "POST",
      body: JSON.stringify({ id, loader, loader_version: loaderVersion || "" }),
    });
    toast(loader === "vanilla" ? "开始下载 " + id : `开始安装 ${id} + ${LOADER_LABEL[loader]}`);
  } catch (e) {
    toast(e.message, "err");
    $("#taskbar").classList.remove("show");
  }
}

async function launchVersion(id) {
  try {
    state.currentVersion = id;
    state.modsVersion = id;
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
    $("#taskTitle").textContent = t.kind === "java" ? "下载 Java 运行时"
      : t.kind === "modpack" ? `安装整合包 ${t.version || ""}`
        : t.kind === "mod" ? `下载 ${t.version || "文件"}`
          : t.kind === "link" ? `下载 ${t.version || "组件"}`
            : t.kind === "login" ? "微软账号登录" : `下载 ${t.version}`;
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
    else if (t.phase === "完成") {
      const done = (t.result && t.result.version) || t.version || "";
      const loader = (t.result && t.result.loader) || "vanilla";
      toast(t.kind === "modpack" ? "整合包安装完成"
        : t.kind === "mod" ? `已装好 ${(t.result && (t.result.title || t.result.file)) || t.version || "文件"}`
        : loader !== "vanilla" ? `已安装 ${done}（${LOADER_LABEL[loader] || loader}）`
        : "下载完成", "ok");
      if (t.result && t.result.note) toast(t.result.note, "err");
      if (t.kind === "modpack") go("versions");
      if (t.kind === "link") loadLink();
      if (t.kind === "install" && done) {
        state.currentVersion = done;
        if (loader !== "vanilla") {
          state.modsVersion = done;
          toast("这个版本已有独立游戏目录，可以去「模组」页导入模组和光影", "");
        }
      }
      if (isPage("mods")) loadMods();
    }
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
      <strong>正在更新 老张的minecraft启动器</strong>
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
  $("#setUpdateUrl").value = c.update_url || "";
  $("#setAutoCheck").checked = c.auto_check_update !== false;
  $("#setAutoDownload").checked = !!c.auto_download_update;
  $("#setUpdateInterval").value = c.update_interval_min || 30;
  $("#setLiveRefresh").checked = c.live_refresh !== false;
  $("#setCloseToTray").checked = !!c.close_to_tray;
  $("#setPackSource").value = c.pack_source || "auto";
  $("#setNetProxy").value = c.net_proxy || "";
  $("#packSource").value = c.pack_source || "auto";
  $("#modShopSource").value = c.pack_source || "auto";
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
    update_url: $("#setUpdateUrl").value.trim(),
    auto_check_update: $("#setAutoCheck").checked,
    auto_download_update: $("#setAutoDownload").checked,
    update_interval_min: parseInt($("#setUpdateInterval").value, 10) || 30,
    live_refresh: $("#setLiveRefresh").checked,
    close_to_tray: $("#setCloseToTray").checked,
    pack_source: $("#setPackSource").value,
    net_proxy: $("#setNetProxy").value.trim(),
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

/* 整合包网络：源切换 + 连通性测试 */

async function setPackSource(value, opts = {}) {
  try {
    const { config } = await api("/api/config", { method: "POST", body: JSON.stringify({ config: { pack_source: value } }) });
    state.config = config;
    $("#packSource").value = config.pack_source || "auto";
    $("#setPackSource").value = config.pack_source || "auto";
    $("#modShopSource").value = config.pack_source || "auto";
    if (!opts.silent) {
      toast("整合包 / 模组源：" + ({ auto: "自动", official: "官方", mirror: "国内镜像" }[value] || value), "ok");
    }
    packState.loaded = false;
    // 只有在整合包页（或已经搜过）时才重搜整合包，避免在模组页白跑一次请求
    if (isPage("modpack") || packState.hits.length) searchPacks(true);
    if (modShopOpen()) searchModShop(true);
  } catch (e) { toast(e.message, "err"); }
}

$("#packSource").addEventListener("change", (e) => setPackSource(e.target.value));

$("#probeNet").addEventListener("click", async () => {
  const btn = $("#probeNet");
  const out = $("#probeResult");
  btn.disabled = true;
  out.className = "muted small";
  out.textContent = "正在测试…";
  try {
    const d = await api("/api/net/probe");
    const r = d.results || {};
    const line = (name, label) => {
      const x = r[name] || {};
      return x.ok ? `${label} ${x.ms} ms` : `${label} 不通`;
    };
    const okCount = ["official", "mirror"].filter((k) => (r[k] || {}).ok).length;
    out.className = okCount ? "small ok" : "small err";
    out.textContent = [
      line("official", "官方"), line("mirror", "镜像"),
      d.proxy ? `代理 ${d.proxy}` : "未用代理",
      okCount ? `建议：${d.best === "mirror" ? "国内镜像" : "官方"}`
              : "两条线路都不通，试试填加速代理",
    ].join(" · ");
  } catch (e) {
    out.className = "small err";
    out.textContent = e.message;
  } finally {
    btn.disabled = false;
  }
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

let loginBranch = "offline";
let loginTimer = null;
let loginActive = false;   // 本次弹层里是否真的发起过登录（决定成功后要不要自动关弹层）
let deviceInfo = {};

function openAccountModal(branch) {
  const current = (state.config.account || {}).type;
  loginBranch = branch || (current === "microsoft" ? "ms" : "offline");
  $("#modalName").value = state.config.player_name || "Steve";
  selectBranch(loginBranch);
  $("#accountModal").classList.add("show");
  if (loginBranch === "ms") { pollDevice(); startLoginPolling(); }
}

function closeAccountModal() {
  $("#accountModal").classList.remove("show");
  if (loginTimer) { clearInterval(loginTimer); loginTimer = null; }
}

function selectBranch(branch) {
  loginBranch = branch;
  $$("#loginBranches .branch").forEach((b) => b.classList.toggle("active", b.dataset.branch === branch));
  $("#pane-offline").hidden = branch !== "offline";
  $("#pane-ms").hidden = branch !== "ms";
}

$("#loginBranches").addEventListener("click", (e) => {
  const b = e.target.closest(".branch");
  if (!b) return;
  selectBranch(b.dataset.branch);
  if (b.dataset.branch === "ms") { pollDevice(); startLoginPolling(); }
});

function renderDevice(d) {
  deviceInfo = d || {};
  const box = $("#deviceBox");
  const btn = $("#modalMsLogin");
  if (!d || !d.status || d.status === "idle") {
    box.hidden = true;
    btn.disabled = false;
    btn.textContent = "开始登录";
    return;
  }
  box.hidden = false;
  const stage = $("#deviceStage");
  const waiting = d.status === "waiting";
  stage.className = "device-stage small " + (d.status === "ok" ? "ok" : d.status === "error" ? "err" : "muted");
  if (d.status === "ok") stage.textContent = `登录成功：${d.name}`;
  else if (d.status === "error") stage.textContent = d.error || "登录失败";
  else stage.textContent = d.stage || "等待你在微软页面完成授权…";
  // 只有退回默认浏览器时才需要「在浏览器中打开」这条后路
  $("#deviceOpen").hidden = d.mode !== "browser";
  btn.disabled = waiting;
  btn.textContent = waiting ? "等待授权…" : "重新登录";
}

$("#deviceOpen").addEventListener("click", () => {
  const url = deviceInfo.uri;
  if (url) window.open(url, "_blank");
  else toast("还没有拿到登录地址，先点「开始登录」", "err");
});

async function pollDevice() {
  try {
    const { account } = await api("/api/login/state");
    renderDevice(account);
    if (account && account.status === "ok") {
      const { config } = await api("/api/config");
      state.config = config;
      fillSettings();
      renderAccount();
      if (loginActive) {
        loginActive = false;
        toast("已登录微软账号 " + account.name, "ok");
        if (loginTimer) { clearInterval(loginTimer); loginTimer = null; }
        setTimeout(closeAccountModal, 900);
      }
    } else if (account && account.status === "error") {
      loginActive = false;
      if (loginTimer) { clearInterval(loginTimer); loginTimer = null; }
    }
  } catch (e) { /* 服务忙时忽略 */ }
}

function startLoginPolling() {
  if (loginTimer) return;
  loginTimer = setInterval(pollDevice, 1600);
}

$("#modalMsLogin").addEventListener("click", async () => {
  const btn = $("#modalMsLogin");
  btn.disabled = true;
  loginActive = true;
  try {
    const res = await api("/api/account/microsoft", { method: "POST", body: JSON.stringify({}) });
    toast(res.mode === "window" ? "已打开微软登录页，登录后会自动完成" : "已在浏览器打开微软登录页", "ok");
    startLoginPolling();
    setTimeout(pollDevice, 500);
  } catch (e) {
    loginActive = false;
    toast(e.message, "err");
    btn.disabled = false;
  }
});

$("#msSubmit").addEventListener("click", async () => {
  const text = $("#msCallback").value.trim();
  if (!text) { toast("先把浏览器地址栏里的链接粘贴进来", "err"); return; }
  loginActive = true;
  try {
    await api("/api/account/ms/code", { method: "POST", body: JSON.stringify({ text }) });
    $("#msCallback").value = "";
    startLoginPolling();
    setTimeout(pollDevice, 400);
  } catch (e) { loginActive = false; toast(e.message, "err"); }
});

$("#accountBtn").addEventListener("click", () => openAccountModal());
$("#modalClose").addEventListener("click", closeAccountModal);
$("#accountModal").addEventListener("click", (e) => {
  if (e.target.id === "accountModal") closeAccountModal();
});
$("#modalOffline").addEventListener("click", async () => {
  const name = $("#modalName").value.trim() || "Steve";
  try {
    const data = await api("/api/account/offline", { method: "POST", body: JSON.stringify({ name }) });
    state.config.account = data.account;
    state.config.player_name = name;
    renderAccount();
    closeAccountModal();
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
  home: "主页", download: "下载", modpack: "整合包", mods: "模组", link: "联机",
  versions: "版本", settings: "设置", logs: "日志",
};


/* ---------------- 整合包：实时搜索 + 一键安装 ---------------- */

const packState = {
  hits: [], total: 0, offset: 0, q: "", loader: "", loading: false,
  timer: null, loaded: false, project: null,
};

function fmtNum(n) {
  n = Number(n) || 0;
  if (n >= 100000000) return (n / 100000000).toFixed(1) + " 亿";
  if (n >= 10000) return (n / 10000).toFixed(1) + " 万";
  return String(n);
}

function ensurePacks() {
  if (!packState.loaded) searchPacks(true);
}

async function searchPacks(reset) {
  if (packState.loading) return;
  packState.loading = true;
  if (reset) { packState.offset = 0; packState.hits = []; }
  const box = $("#packList");
  box.innerHTML = '<div class="empty">正在搜索…</div>';
  try {
    const qs = new URLSearchParams({
      q: packState.q, loader: packState.loader,
      offset: String(packState.offset), limit: "24", index: packState.q ? "relevance" : "downloads",
    });
    const data = await api("/api/modpack/search?" + qs);
    packState.hits = data.hits || [];
    packState.total = data.total || 0;
    packState.loaded = true;
    renderPacks();
  } catch (e) {
    box.innerHTML = `<div class="empty">搜索失败：${esc(e.message)}<br><span class="small">国内网络连不上 Modrinth？把右上角切成「国内镜像」再试，或在设置里填加速代理。</span></div>`;
  } finally {
    packState.loading = false;
  }
}

function renderPacks() {
  const box = $("#packList");
  if (!packState.hits.length) {
    box.innerHTML = '<div class="empty">没有匹配的整合包，换个关键词试试。</div>';
    $("#packCount").textContent = "";
    return;
  }
  box.innerHTML = packState.hits.map((h, i) => `
    <article class="pack-card">
      <div class="pack-top">
        <div class="pack-icon">${h.icon ? `<img src="${esc(h.icon)}" data-alt="${esc(h.icon_mirror || "")}" onerror="if(this.dataset.alt){this.onerror=null;this.src=this.dataset.alt}" alt="" loading="lazy" referrerpolicy="no-referrer">` : "📦"}</div>
        <div class="pack-title">
          <strong title="${esc(h.title)}">${esc(h.title)}</strong>
          <span class="muted small">${esc(h.author || "")}${h.updated ? " · " + esc(h.updated) : ""}</span>
        </div>
      </div>
      <p class="pack-desc">${esc(h.description || "")}</p>
      <div class="pack-tags">${(h.categories || []).slice(0, 4).map((c) => `<span class="pill">${esc(c)}</span>`).join("")}</div>
      <div class="pack-foot">
        <span class="muted small">↓ ${fmtNum(h.downloads)} · ★ ${fmtNum(h.follows)}</span>
        <button class="btn btn-primary btn-mini" data-pick="${i}">安装</button>
      </div>
    </article>`).join("");
  box.querySelectorAll("[data-pick]").forEach((b) => {
    b.addEventListener("click", () => openPackVersions(packState.hits[Number(b.dataset.pick)]));
  });
  $("#packCount").textContent = packState.total > packState.hits.length
    ? `共 ${packState.total} 个结果，显示前 ${packState.hits.length} 个（输入关键词缩小范围）`
    : `共 ${packState.total} 个结果`;
}

let verPick = null;

/** 通用「选文件版本」弹层：整合包 / 模组 / 光影 都复用这一个。 */
function openVerPicker({ title, sub, empty, load, onPick }) {
  verPick = { onPick };
  $("#packModalTitle").textContent = title || "选择版本";
  $("#packModalSub").textContent = sub || "";
  $("#packVerList").innerHTML = '<div class="empty">正在读取版本列表…</div>';
  $("#packModal").classList.add("show");
  load().then((rows) => {
    if (!rows || !rows.length) {
      $("#packVerList").innerHTML = `<div class="empty">${esc(empty || "没有可用版本，换个筛选试试。")}</div>`;
      return;
    }
    $("#packVerList").innerHTML = rows.slice(0, 60).map((v, i) => `
      <div class="ver-pick-row">
        <div class="ver-pick-meta">
          <strong>${esc(v.name || v.number)}</strong>
          <span class="muted small">MC ${esc((v.game_versions || []).join(" / "))} · ${esc((v.loaders || []).join(" / "))} · ${esc(v.date || "")}</span>
        </div>
        <button class="btn btn-primary btn-mini" data-ver="${i}">下载</button>
      </div>`).join("");
    $("#packVerList").querySelectorAll("[data-ver]").forEach((b) => {
      b.addEventListener("click", () => {
        const row = rows[Number(b.dataset.ver)];
        if (row && verPick && verPick.onPick) verPick.onPick(row);
      });
    });
  }).catch((e) => {
    $("#packVerList").innerHTML = `<div class="empty">读取失败：${esc(e.message)}</div>`;
  });
}

async function openPackVersions(hit) {
  if (!hit) return;
  packState.project = hit;
  openVerPicker({
    title: hit.title,
    sub: hit.description || "",
    empty: "该筛选下没有可用版本，换个 loader 试试。",
    load: async () => {
      const qs = new URLSearchParams({ project: hit.slug || hit.id, loader: packState.loader });
      const data = await api("/api/modpack/versions?" + qs);
      return data.versions || [];
    },
    onPick: (v) => installPack(hit, v.id),
  });
}

async function installPack(hit, versionId) {
  try {
    $("#taskbar").classList.add("show");
    $("#taskTitle").textContent = "安装整合包 " + hit.title;
    $("#taskSub").textContent = "准备下载…";
    $("#taskFill").style.width = "2%";
    $("#taskPercent").textContent = "0%";
    await api("/api/modpack/install", {
      method: "POST",
      body: JSON.stringify({ project: hit.slug || hit.id, version: versionId, title: hit.title }),
    });
    $("#packModal").classList.remove("show");
    toast("开始安装《" + hit.title + "》，进度见「日志」页", "ok");
    go("logs");
  } catch (e) {
    toast(e.message, "err");
    $("#taskbar").classList.remove("show");
  }
}

$("#packSearch").addEventListener("input", (e) => {
  packState.q = e.target.value;
  if (packState.timer) clearTimeout(packState.timer);
  packState.timer = setTimeout(() => searchPacks(true), 380);
});

$("#packLoaders").addEventListener("click", (e) => {
  const tab = e.target.closest(".tab");
  if (!tab) return;
  $$("#packLoaders .tab").forEach((x) => x.classList.toggle("active", x === tab));
  packState.loader = tab.dataset.loader || "";
  searchPacks(true);
});

$("#packRefresh").addEventListener("click", () => searchPacks(true));
$("#packModalClose").addEventListener("click", () => $("#packModal").classList.remove("show"));
$("#packModal").addEventListener("click", (e) => {
  if (e.target.id === "packModal") $("#packModal").classList.remove("show");
});

/* ---------------- 拖拽整合包导入 ---------------- */

let dragDepth = 0;

function hasFiles(e) {
  const dt = e.dataTransfer;
  if (!dt) return false;
  const types = Array.from(dt.types || []);
  return types.includes("Files");
}

function showVeil(on) {
  const v = $("#dropVeil");
  if (!v) return;
  v.classList.toggle("show", !!on);
  if (!on) dragDepth = 0;
}

window.addEventListener("dragenter", (e) => {
  if (!hasFiles(e)) return;
  e.preventDefault();
  dragDepth++;
  showVeil(true);
});

window.addEventListener("dragover", (e) => {
  if (!hasFiles(e)) return;
  e.preventDefault();
  if (e.dataTransfer) e.dataTransfer.dropEffect = "copy";
  showVeil(true);
});

window.addEventListener("dragleave", (e) => {
  if (!hasFiles(e)) return;
  dragDepth = Math.max(0, dragDepth - 1);
  if (!dragDepth) showVeil(false);
});

window.addEventListener("drop", async (e) => {
  if (!hasFiles(e)) return;
  e.preventDefault();
  e.stopPropagation();
  showVeil(false);
  const files = Array.from((e.dataTransfer && e.dataTransfer.files) || []);
  if (!files.length) return;
  const jars = files.filter((f) => f.name.toLowerCase().endsWith(".jar"));
  if (jars.length) {
    if (jars.length !== files.length) toast("一次只处理同类型文件，先导入模组", "err");
    if (!modVersion()) { go("mods"); toast("先在「模组」页选一个版本，再拖入 .jar", "err"); return; }
    await importFiles(jars, "mods");
    return;
  }
  const file = files[0];
  const lower = file.name.toLowerCase();
  if (!lower.endsWith(".mrpack") && !lower.endsWith(".zip")) {
    toast("支持 .jar 模组、.zip 光影 / 资源包、.mrpack / .zip 整合包", "err");
    return;
  }
  // 在「模组」页拖 .zip → 当成光影 / 资源包导入（辨识为整合包时后端会自动改为安装）
  if (isPage("mods") && /\.zip$/.test(lower)) {
    await importFiles([file], "auto");
    return;
  }
  await uploadPack(file);
});

function isPage(name) {
  const el = document.getElementById("page-" + name);
  return !!el && el.classList.contains("active");
}

async function uploadPack(file) {
  $("#taskbar").classList.add("show");
  $("#taskTitle").textContent = "导入整合包 " + file.name;
  $("#taskSub").textContent = `上传中 · ${fmtBytes(file.size)}`;
  $("#taskFill").style.width = "30%";
  $("#taskPercent").textContent = "…";
  try {
    const res = await fetch("/api/modpack/upload", {
      method: "POST",
      headers: {
        "Content-Type": "application/octet-stream",
        "X-Filename": encodeURIComponent(file.name),
      },
      body: file,
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || data.ok === false) throw new Error(data.error || `上传失败 (${res.status})`);
    toast(`已接收 ${file.name}（${fmtBytes(data.size)}），开始安装`, "ok");
    go("logs");
  } catch (e) {
    $("#taskbar").classList.remove("show");
    toast("导入失败：" + e.message, "err");
  }
}


/* ---------------- 联机 ---------------- */

const linkState = { info: null, room: null, loaded: false };

async function copyText(text, okMsg) {
  if (!text) { toast("没有可复制的内容", "err"); return; }
  try {
    await navigator.clipboard.writeText(text);
    toast(okMsg || "已复制", "ok");
  } catch (e) {
    // 老内核 / 非安全上下文兜底
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    try { document.execCommand("copy"); toast(okMsg || "已复制", "ok"); }
    catch (_) { toast("复制失败，请手动选中", "err"); }
    ta.remove();
  }
}

async function loadLink() {
  const lanBox = $("#linkLanList");
  lanBox.innerHTML = '<span class="muted small">检测中…</span>';
  try {
    const data = await api("/api/link/state");
    linkState.info = data;
    linkState.loaded = true;
    renderLink(data);
  } catch (e) {
    lanBox.innerHTML = `<span class="muted small">检测失败：${esc(e.message)}</span>`;
  }
}

function renderLink(d) {
  const port = ($("#linkPort").value || d.default_port || 25565).trim();
  // --- 局域网
  const ips = d.lan_ips || [];
  if (!ips.length) {
    $("#linkLanList").innerHTML = '<span class="muted small">没检测到局域网地址（可能没连 WiFi / 网线）</span>';
  } else {
    $("#linkLanList").innerHTML = ips.map((i) => `
      <div class="kv-row">
        <div>
          <strong class="mono">${esc(i.ip)}:${esc(port)}</strong>
          <span class="muted small">${esc(i.name || "")}</span>
        </div>
        <button class="btn btn-ghost btn-mini" data-copy="${esc(i.ip + ":" + port)}">复制</button>
      </div>`).join("");
    $("#linkLanList").querySelectorAll("[data-copy]").forEach((b) => {
      b.addEventListener("click", () => copyText(b.dataset.copy, "已复制 " + b.dataset.copy));
    });
  }

  // --- 网关状态
  const gw = d.gateway || {};
  const box = $("#linkGwStatus");
  if (gw.found) {
    box.className = "up-status ok";
    box.innerHTML = gw.cg_nat
      ? `<span class="small">路由器支持 UPnP，但公网地址是 <span class="mono">${esc(gw.external_ip)}</span>（运营商内网），映射了外面也进不来。改用下面的组网工具。</span>`
      : `<span class="small">路由器支持 UPnP · 公网地址 <span class="mono">${esc(gw.external_ip || "读取失败")}</span></span>`;
  } else {
    box.className = "up-status err";
    box.innerHTML = `<span class="small">${esc(gw.error || "没找到支持 UPnP 的路由器")}</span>`;
  }

  // --- 已有映射
  const maps = gw.mappings || [];
  if (maps.length) {
    $("#linkPublicText").textContent = maps.map((m) => `${gw.external_ip}:${m.port}`).join("  ");
    $("#linkPublicAddr").hidden = false;
  }

  // --- 组网工具
  const et = d.easytier || {};
  $("#linkEtOpen").hidden = !et.installed;
  $("#linkEtState").textContent = et.installed
    ? `已就绪：${et.gui || et.dir}`
    : "还没下载。点「下载 EasyTier 客户端」会自动放到数据目录的 runtime/easytier 下。";

  // 端口默认值：优先用游戏日志里抓到的
  if (d.suggest_port && d.suggest_port.port && !linkState.portTouched) {
    $("#linkPort").value = d.suggest_port.port;
  } else if (!linkState.portTouched && !$("#linkPort").value) {
    $("#linkPort").value = d.default_port || 25565;
  }
}

$("#linkRefresh").addEventListener("click", () => loadLink());

$("#linkMapBtn").addEventListener("click", async () => {
  const port = parseInt($("#linkPort").value, 10);
  if (!port) { toast("先填端口", "err"); return; }
  const btn = $("#linkMapBtn");
  btn.disabled = true;
  btn.textContent = "处理中…";
  try {
    const r = await api("/api/link/map", { method: "POST", body: JSON.stringify({ port }) });
    if (r.address) {
      $("#linkPublicText").textContent = r.address;
      $("#linkPublicAddr").hidden = false;
      copyText(r.address, "已开放并复制地址：" + r.address);
    } else {
      toast("映射成功，但没读到公网地址", "err");
    }
    if (r.warning) toast(r.warning, "err");
    loadLink();
  } catch (e) {
    toast(e.message, "err");
  } finally {
    btn.disabled = false;
    btn.textContent = "开放到公网";
  }
});

$("#linkUnmapBtn").addEventListener("click", async () => {
  const port = parseInt($("#linkPort").value, 10);
  if (!port) return;
  try {
    await api("/api/link/unmap", { method: "POST", body: JSON.stringify({ port }) });
    toast(`已关闭 ${port} 的公网映射`, "ok");
    $("#linkPublicAddr").hidden = true;
    loadLink();
  } catch (e) { toast(e.message, "err"); }
});

$("#linkCopyLan").addEventListener("click", () => {
  const d = linkState.info;
  if (!d || !(d.lan_ips || []).length) { toast("没有可复制的地址", "err"); return; }
  const port = $("#linkPort").value || d.default_port;
  copyText(d.lan_ips.map((i) => `${i.ip}:${port}`).join("\n"), "已复制局域网地址");
});

$("#linkCopyPublic").addEventListener("click", () => copyText($("#linkPublicText").textContent, "已复制公网地址"));

$("#linkPort").addEventListener("input", () => { linkState.portTouched = true; });

$("#linkRoomBtn").addEventListener("click", async () => {
  try {
    const { room } = await api("/api/link/room", { method: "POST", body: JSON.stringify({}) });
    linkState.room = room;
    $("#linkInvite").textContent = room.invite;
    $("#linkInvite").hidden = false;
    $("#linkInviteRow").hidden = false;
    toast("已生成房间 " + room.code, "ok");
  } catch (e) { toast(e.message, "err"); }
});

$("#linkCopyInvite").addEventListener("click", () => copyText($("#linkInvite").textContent, "邀请已复制，发给朋友"));

$("#linkEtDownload").addEventListener("click", async () => {
  const btn = $("#linkEtDownload");
  btn.disabled = true;
  try {
    await api("/api/link/easytier/download", { method: "POST", body: JSON.stringify({}) });
    $("#taskbar").classList.add("show");
    toast("开始下载 EasyTier，进度见底部任务条", "ok");
  } catch (e) {
    toast(e.message, "err");
  } finally {
    btn.disabled = false;
  }
});

$("#linkEtOpen").addEventListener("click", async () => {
  try {
    await api("/api/link/easytier/open", { method: "POST", body: JSON.stringify({}) });
  } catch (e) { toast(e.message, "err"); }
});

/* ---------------- 启动 ---------------- */

window.addEventListener("hashchange", () => go(location.hash.replace("#", "")));

(async function boot() {
  try {
    await loadConfig();
    // 先切到目标页面再拉数据：版本清单在慢网络下可能要几十秒，不能挡住导航
    go(location.hash.replace("#", "") || "home");
    poll();
    connectStream();
    await Promise.all([loadVersions(false), loadLocal()]);
  } catch (e) {
    toast("初始化失败：" + e.message, "err");
  }
})();
