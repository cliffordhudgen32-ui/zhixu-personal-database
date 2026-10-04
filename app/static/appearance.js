/* Personal appearance preferences are separate from record editing dialogs. */
"use strict";
(() => {
  const storageKey = "knowledge-appearance-v1";
  const defaults = Object.freeze({ skin: "classic", illustrations: false, motion: false });
  const skins = [
    { id: "maple-light", title: "枫笺 · 晨光", caption: "暖纸、朱砂与秋日陪伴", image: "/static/skins/maple-placeholder.svg" },
    { id: "maple-dark", title: "枫影 · 暮色", caption: "黑棕、柔金与灯火枫林", image: "/static/skins/maple-placeholder.svg" },
    { id: "classic", title: "知序 · 素白", caption: "清爽留白，专注每一份记录", image: null },
  ];
  const leaf = '<svg viewBox="0 0 64 64" aria-hidden="true"><path fill="currentColor" d="m32 3 6 17 9-8-1 15 13-1-10 12 6 6-18 4-4 13-2-13-18-4 6-6L5 26l13 1-1-15 9 8z"/></svg>';
  let current = { ...defaults }, saved = { ...defaults }, dirty = false, saving = false;
  let loadPromise = null, lastLoad = 0, interaction = 0, opener = null, loadError = false;
  const panel = () => document.getElementById("appearance-panel");
  const valid = (data) => {
    if (!data || !skins.some((skin) => skin.id === data.skin)) return null;
    return { skin: data.skin, illustrations: typeof data.illustrations === "boolean" ? data.illustrations : true, motion: typeof data.motion === "boolean" ? data.motion : false };
  };
  const remember = (preferences) => {
    try { localStorage.setItem(storageKey, JSON.stringify(preferences)); } catch (_) { /* Private browsing can disable storage. */ }
  };
  const same = (a, b) => a.skin === b.skin && a.illustrations === b.illustrations && a.motion === b.motion;
  function apply(preferences = current, options = {}) {
    const normalized = valid(preferences);
    if (!normalized) return;
    current = normalized;
    document.documentElement.dataset.skin = current.skin;
    document.documentElement.dataset.illustrations = String(current.illustrations);
    document.documentElement.dataset.mapleMotion = String(current.motion);
    document.documentElement.style.colorScheme = current.skin === "maple-dark" ? "dark" : "light";
    document.body.classList.toggle("dark", current.skin === "maple-dark");
    const companion = document.getElementById("maple-companion");
    if (companion) {
      companion.hidden = current.skin === "classic" || !current.illustrations;
      if (!companion.dataset.built) {
        companion.innerHTML = `<img class="maple-companion-image" src="/static/skins/maple-placeholder.svg" alt="通用枫叶图案" loading="lazy"><div class="maple-companion-caption"><span>${leaf} 枫叶与你</span><small>把今天，写成明天的答案。</small></div>`;
        companion.dataset.built = "true";
      }
      companion.querySelector("img").src = current.skin === "maple-dark" ? "/static/skins/maple-placeholder.svg" : "/static/skins/maple-placeholder.svg";
      companion.querySelector("img").alt = current.skin === "maple-dark" ? "通用枫叶图案" : "通用枫叶图案";
    }
    const atmosphere = document.getElementById("maple-atmosphere");
    if (atmosphere && !atmosphere.dataset.built) {
      atmosphere.innerHTML = [0, 1, 2, 3].map((n) => `<i class="maple-floating-leaf leaf-${n}">${leaf}</i>`).join("");
      atmosphere.dataset.built = "true";
    }
    const button = document.getElementById("theme-button");
    if (button) {
      button.innerHTML = `${leaf}<span class="appearance-trigger-text">皮肤</span>`;
      button.title = `界面皮肤：${skins.find((skin) => skin.id === current.skin).title}`;
      button.setAttribute("aria-label", "切换界面皮肤");
    }
    if (options.remember) remember(current);
    syncPanel();
    window.dispatchEvent(new CustomEvent("appearance:change", { detail: { ...current } }));
  }
  function message(text, error = false) {
    const output = document.getElementById("appearance-status");
    if (output) { output.textContent = text; output.classList.toggle("appearance-error", error); }
  }
  function syncPanel() {
    const container = panel();
    if (!container || !container.dataset.built) return;
    container.querySelectorAll("[data-appearance-skin]").forEach((button) => {
      const selected = button.dataset.appearanceSkin === current.skin;
      button.setAttribute("aria-pressed", String(selected));
      button.classList.toggle("selected", selected);
      button.disabled = saving;
    });
    container.querySelector("#appearance-illustrations").checked = current.illustrations;
    container.querySelector("#appearance-motion").checked = current.motion;
    container.querySelectorAll("input").forEach((input) => { input.disabled = saving; });
    const save = container.querySelector("#appearance-save");
    save.disabled = saving || (!dirty && !loadError);
    save.textContent = saving ? "正在保存…" : dirty ? "重试保存" : loadError ? "重试连接" : "已保存";
    container.querySelector("#appearance-close").disabled = saving;
    const classicHint = container.querySelector(".appearance-classic-hint");
    classicHint.hidden = current.skin !== "classic";
  }
  function preview(update) {
    if (saving) return;
    interaction += 1;
    const next = { ...current, ...update };
    dirty = !same(next, saved);
    apply(next);
    if (dirty) void save();
    else message("当前皮肤已保存。");
  }
  function buildPanel() {
    let container = panel();
    if (!container) {
      container = document.createElement("div");
      container.id = "appearance-panel";
      container.className = "appearance-panel";
      container.hidden = true;
      container.setAttribute("role", "dialog");
      container.setAttribute("aria-modal", "false");
      container.setAttribute("aria-labelledby", "appearance-title");
      document.body.append(container);
    }
    if (container.dataset.built) return;
    container.innerHTML = `<div class="appearance-panel-heading"><div><p class="appearance-kicker">为你的记忆，换一身秋色</p><h2 id="appearance-title">界面皮肤</h2></div><button id="appearance-close" class="appearance-close" type="button" aria-label="关闭皮肤面板">×</button></div><div class="appearance-skins" role="group" aria-label="选择界面皮肤">${skins.map((skin) => `<button class="appearance-skin appearance-preview-${skin.id}" data-appearance-skin="${skin.id}" type="button" aria-pressed="false"><span class="appearance-preview">${skin.image ? `<img src="${skin.image}" alt="" loading="lazy">` : ""}<span class="appearance-preview-ui"><i></i><i></i><i></i></span><span class="appearance-check" aria-hidden="true">✓</span></span><strong>${skin.title}</strong><small>${skin.caption}</small></button>`).join("")}</div><div class="appearance-options"><label class="appearance-option"><span><strong>角色插画</strong><small>显示侧栏角色与首页插画</small></span><input id="appearance-illustrations" type="checkbox"></label><label class="appearance-option"><span><strong>枫叶轻动</strong><small>让边缘的枫叶缓缓飘落，默认关闭</small></span><input id="appearance-motion" type="checkbox"></label><p class="appearance-classic-hint" hidden>素白皮肤保留简洁布局，不显示角色与飘落枫叶。</p></div><div class="appearance-panel-footer"><p id="appearance-status" role="status" aria-live="polite"></p><button id="appearance-save" class="primary" type="button">已保存</button></div>`;
    container.dataset.built = "true";
    container.querySelectorAll("[data-appearance-skin]").forEach((button) => {
      button.addEventListener("click", () => preview({ skin: button.dataset.appearanceSkin }));
    });
    container.querySelector("#appearance-illustrations").addEventListener("change", (event) => preview({ illustrations: event.target.checked }));
    container.querySelector("#appearance-motion").addEventListener("change", (event) => preview({ motion: event.target.checked }));
    container.querySelector("#appearance-close").addEventListener("click", close);
    container.querySelector("#appearance-save").addEventListener("click", () => dirty ? save() : load({ force: true }));
  }
  function open() {
    buildPanel();
    opener = document.activeElement;
    const container = panel();
    container.hidden = false;
    document.getElementById("theme-button")?.setAttribute("aria-expanded", "true");
    syncPanel();
    message(loadError ? "无法连接本地空间；点击重试连接后再切换皮肤。" : "点击皮肤即可切换并自动保存。", loadError);
    container.querySelector(`[data-appearance-skin="${current.skin}"]`).focus();
    void load();
  }
  function close() {
    if (saving) return;
    const container = panel();
    if (!container || container.hidden) return;
    if (dirty) { dirty = false; interaction += 1; apply(saved); }
    container.hidden = true;
    document.getElementById("theme-button")?.setAttribute("aria-expanded", "false");
    if (opener?.isConnected) opener.focus();
  }
  async function load(options = {}) {
    if (saving || dirty) return { ...current };
    if (loadPromise) return loadPromise;
    if (!options.force && Date.now() - lastLoad < 15000) return { ...current };
    const version = interaction;
    loadPromise = (async () => {
      try {
        const response = await fetch("/api/appearance", { cache: "no-store", credentials: "same-origin", signal: AbortSignal.timeout(8000) });
        if (!response.ok) throw new Error("无法读取已保存的皮肤");
        const preferences = valid(await response.json());
        if (!preferences) throw new Error("皮肤设置格式无效");
        lastLoad = Date.now();
        loadError = false;
        if (interaction === version && !dirty && !saving) {
          saved = preferences;
          apply(preferences, { remember: true });
          if (panel() && !panel().hidden) message("点击皮肤即可切换并自动保存。");
        }
      } catch (_) {
        loadError = true;
        syncPanel();
        if (panel() && !panel().hidden) message("无法连接本地空间；点击重试连接后再切换皮肤。", true);
      }
      return { ...current };
    })();
    try { return await loadPromise; } finally { loadPromise = null; }
  }
  async function save() {
    if (saving || !dirty) return;
    saving = true;
    interaction += 1;
    syncPanel();
    message("正在将皮肤保存到你的本地空间…");
    try {
      let response;
      for (let attempt = 0; attempt < 2; attempt += 1) {
        const sessionResponse = await fetch("/api/session", { cache: "no-store", credentials: "same-origin", signal: AbortSignal.timeout(8000) });
        if (!sessionResponse.ok) throw new Error("无法连接本地数据库，请确认程序正在运行。");
        const session = await sessionResponse.json();
        if (!session.token) throw new Error("本地会话已失效，请刷新页面后重试。");
        response = await fetch("/api/appearance", { method: "PUT", credentials: "same-origin", signal: AbortSignal.timeout(8000), headers: { "Content-Type": "application/json", "x-local-token": session.token }, body: JSON.stringify(current) });
        if (response.status !== 403) break;
      }
      if (!response.ok) throw new Error("皮肤未保存，请稍后重试。");
      const preferences = valid(await response.json());
      if (!preferences) throw new Error("皮肤设置格式无效，请刷新后检查。");
      saved = preferences;
      dirty = false;
      loadError = false;
      lastLoad = Date.now();
      apply(saved, { remember: true });
      message("已保存 · 桌面与网页使用同一套皮肤。");
    } catch (error) {
      message(`未保存：${error.message === "Failed to fetch" || error.name === "TimeoutError" ? "本地服务未连接，请重试保存。" : error.message}`, true);
    } finally {
      saving = false;
      syncPanel();
    }
  }
  try { saved = valid(JSON.parse(localStorage.getItem(storageKey))) || { ...defaults }; } catch (_) { saved = { ...defaults }; }
  apply(saved);
  window.KnowledgeAppearance = { apply, load, open, close, getPreferences: () => ({ ...current }) };
  const button = document.getElementById("theme-button");
  if (button) {
    button.setAttribute("aria-haspopup", "dialog");
    button.setAttribute("aria-controls", "appearance-panel");
    button.setAttribute("aria-expanded", "false");
    button.addEventListener("click", () => panel() && !panel().hidden ? close() : open());
  }
  document.addEventListener("pointerdown", (event) => {
    const container = panel();
    if (container && !container.hidden && !container.contains(event.target) && !button?.contains(event.target)) close();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && panel() && !panel().hidden) { event.preventDefault(); event.stopPropagation(); close(); }
  }, true);
  window.addEventListener("storage", (event) => {
    if (event.key !== storageKey || dirty || saving) return;
    let preferences;
    try { preferences = valid(JSON.parse(event.newValue)); } catch (_) { return; }
    if (preferences) { saved = preferences; interaction += 1; apply(preferences); }
  });
  window.addEventListener("focus", () => { if (document.visibilityState === "visible") void load({ force: true }); });
  document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible") void load({ force: true }); });
  void load({ force: true });
})();
