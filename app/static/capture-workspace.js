"use strict";

// No private text is written to browser storage. Drafts live in the local database.
(function (root) {
  const templates = {
    quick: { label: "随手记", text: "" },
    work: { label: "工作跟进", text: "事情：\n进展：\n遇到的问题：\n下一步：" },
    equipment: { label: "设备检查 / 异常", text: "设备或位置：\n发现的现象：\n检查与处理：\n实际结果：\n待核实 / 下一步：" },
    learning: { label: "学习记录", text: "学习主题：\n关键要点：\n我的理解：\n疑问：\n准备如何使用：" },
    life: { label: "生活记录", text: "发生的事情：\n我的感受：\n值得记住的细节：\n后续安排：" },
  };
  const snapshot = draft => ({ date: draft.date, title: draft.title, content: draft.content,
    template_key: draft.template_key, privacy_level: Number(draft.privacy_level), project_ids: draft.project_ids || [] });
  const equal = (a, b) => JSON.stringify(a) === JSON.stringify(b);
  function insertTemplate(content, key) {
    const text = templates[key]?.text || "";
    return text ? content.trim() ? content + "\n\n" + text : text : content;
  }
  function createSaver(options) {
    let revision = options.revision || 0, saved = options.initial, dirty = false;
    let pending = null, timer = null, error = null, stopped = false;
    const status = () => options.onState({ revision, dirty, saving: Boolean(pending), error });
    const cancel = () => { if (timer !== null) clearTimeout(timer); timer = null; };
    const schedule = () => {
      cancel();
      if (!stopped && dirty && !error) timer = setTimeout(() => { timer = null; persist().catch(() => {}); }, options.delay ?? 1500);
    };
    function edit() {
      if (stopped) return;
      dirty = !equal(options.read(), saved) || Boolean(pending);
      status(); schedule();
    }
    function persist() {
      if (pending) return pending;
      if (stopped || !dirty) return Promise.resolve({ revision });
      const sent = options.read(), expected = revision;
      pending = Promise.resolve().then(() => options.persist(sent, expected)).then(result => {
        revision = result.revision; saved = sent; dirty = !equal(options.read(), sent); error = null;
        return result;
      }).catch(failure => { error = failure; dirty = true; throw failure; })
        .finally(() => { pending = null; status(); schedule(); });
      status();
      return pending;
    }
    async function flush(force = false) {
      cancel(); error = null;
      if (force && !revision) dirty = true;
      while (!stopped && (pending || dirty)) { await (pending || persist()); cancel(); }
      if (stopped) throw Error("草稿已经切换，请重新打开后继续。");
      return revision;
    }
    return { edit, flush, stop() { stopped = true; cancel(); },
      get revision() { return revision; }, get dirty() { return dirty; }, get pending() { return pending; } };
  }
  root.CaptureTools = { templates, snapshot, insertTemplate, createSaver };
  if (typeof module !== "undefined" && module.exports) module.exports = root.CaptureTools;
})(typeof globalThis !== "undefined" ? globalThis : window);

let CaptureWorkspace = null;
let CaptureRenderSerial = 0;
function captureMessage(text, isError = false) {
  const node = document.querySelector("#capture-message");
  if (node) { node.textContent = text; node.classList.toggle("warning", isError); }
}
async function captureBeforeLeave() {
  const state = CaptureWorkspace;
  if (!state || !state.form?.isConnected || location.hash.split("?")[0] === state.route) return true;
  try {
    if (state.busy) throw Error("正在保存，请稍候再切换页面。");
    if (state.submitAttempted && !state.submitted) throw Error("请先点击“核实保存结果并重试”，确认这条记录的保存状态。");
    if (state.files.length) throw Error("还有待上传的附件。请先保存记录，或点击“移除待上传附件”再切换。");
    if (!state.submitted) await state.saver.flush();
    if (state.compareOpen && $("#dialog").open) $("#dialog").close();
    state.saver.stop(); CaptureWorkspace = null;
    return true;
  } catch (error) {
    captureMessage(error.message + " 当前文字保留在编辑框中。", true);
    location.hash = state.route; toast(error.message); return false;
  }
}
if (typeof window !== "undefined") window.addEventListener("beforeunload", event => {
  const state = CaptureWorkspace;
  if (state?.form?.isConnected && (state.saver.dirty || state.saver.pending || state.files.length || state.busy || (state.submitAttempted && !state.submitted))) {
    event.preventDefault(); event.returnValue = "";
  }
});

async function captureWorkspacePage(uid) {
  if (CaptureWorkspace?.form?.isConnected && CaptureWorkspace.route === location.hash.split("?")[0]) return;
  const requestedRoute = location.hash.split("?")[0], renderSerial = ++CaptureRenderSerial;
  const draft = uid ? await api(`/api/capture-drafts/${encodeURIComponent(uid)}`) : {
    uuid: crypto.randomUUID(), date: today(), title: "", content: "", template_key: "quick",
    privacy_level: 1, project_ids: [], revision: 0, status: "open", submitted_entry_uuid: null,
  };
  if (renderSerial !== CaptureRenderSerial || location.hash.split("?")[0] !== requestedRoute || !requestedRoute.startsWith("#quick")) return;
  CaptureWorkspace?.saver?.stop();
  const writable = draft.status === "open";
  const tools = window.CaptureTools;
  $("#main").innerHTML = heading("随手记与草稿", "先留下事实，稍后再整理。文字自动暂存，确认保存后才进入收集箱。") +
    `<div class="capture-layout"><section class="panel capture-editor"><form id="capture-form">
      <div class="capture-status" id="capture-save-state" role="status"></div>
      <div class="form-grid"><label>记录日期<input name="date" type="date" required value="${esc(draft.date)}"></label>
      <label>记录场景<select name="template_key">${Object.entries(tools.templates).map(([key, value]) => `<option value="${key}" ${key === draft.template_key ? "selected" : ""}>${esc(value.label)}</option>`).join("")}</select></label></div>
      <div class="actions"><button id="capture-template" type="button">插入场景提纲</button><small class="muted">留空也可以，只填写实际发生的事情。</small></div>
      <label>标题（可选）<input name="title" maxlength="500" value="${esc(draft.title)}" placeholder="用一句话说明这条记录"></label>
      <label>原始记录<textarea name="content" rows="12" maxlength="2000000" placeholder="文字、链接、观察结果……不确定的细节可标注“待核实”。">${esc(draft.content)}</textarea></label>
      <div class="form-grid"><label>隐私等级<select name="privacy_level">${[1,2,3].map(level => `<option value="${level}" ${level === draft.privacy_level ? "selected" : ""}>${level === 1 ? "1 · 普通" : level === 2 ? "2 · 私人" : "3 · 敏感（不参与 AI 整理）"}</option>`).join("")}</select></label>
      <label>关联项目（可选）<select name="project_ids" multiple data-reference-kind="projects"></select></label></div>
      <div class="capture-attachments"><label>附件（可选）<input id="capture-files" type="file" multiple></label><p class="muted">只自动暂存文字。附件在点击“确认保存到收集箱”后上传，关闭页面前请完成保存。</p>
      <ul id="capture-file-list"></ul><button id="capture-remove-files" type="button" hidden>移除待上传附件</button></div>
      <p id="capture-message" role="status"></p>
      <div class="actions capture-actions"><button id="capture-submit" class="primary" type="submit">确认保存到收集箱</button><button id="capture-save" type="button">立即暂存文字</button><button id="capture-latest" type="button" hidden>对照服务器版本</button></div>
      <p class="muted">暂存草稿不参加搜索或 AI 整理；保存到收集箱后的 AI 稿仍需你审核归档。Ctrl + Enter 保存记录。</p>
      </form></section><section class="panel capture-drafts"><div class="capture-list-heading"><h2>我的草稿</h2><button id="capture-new" type="button">＋ 新草稿</button></div>
      <div class="actions"><button id="capture-list-open" type="button">待继续</button><button id="capture-list-discarded" type="button">已收起</button><button id="capture-list-refresh" type="button">刷新</button></div>
      <div id="capture-draft-list" aria-live="polite"></div><div id="capture-list-pages" class="actions"></div></section></div>`;
  const form = $("#capture-form");
  const state = { draft, form, route: location.hash.split("?")[0], files: [], busy: false, submitAttempted: false, submitted: draft.submitted_entry_uuid,
    listStatus: "open", listPage: 1, listSerial: 0, listSavedRevision: draft.revision };
  CaptureWorkspace = state;
  const active = () => CaptureWorkspace === state && form.isConnected;
  const read = () => ({ date: form.elements.date.value, title: form.elements.title.value, content: form.elements.content.value,
    template_key: form.elements.template_key.value, privacy_level: Number(form.elements.privacy_level.value),
    project_ids: [...form.elements.project_ids.selectedOptions].map(option => option.value) });
  // Seed selected projects before lookup hydration so an early edit cannot drop them.
  for (const id of draft.project_ids || []) form.elements.project_ids.add(new Option(refs.projects?.find(x => x.uuid === id)?.name || "关联项目", id, true, true));
  for (const project of refs.projects || []) if (!(draft.project_ids || []).includes(project.uuid)) form.elements.project_ids.add(new Option(project.name, project.uuid));
  const lock = disabled => [...form.querySelectorAll("input, textarea, select, button")].forEach(node => node.disabled = disabled);
  state.saver = tools.createSaver({ revision: draft.revision, initial: tools.snapshot(draft), read,
    persist: async (value, revision) => {
      try { return await api(`/api/capture-drafts/${draft.uuid}`, { method: "PUT", body: { ...value, revision } }); }
      catch (error) {
        if (error.status === 409) {
          const latest = await api(`/api/capture-drafts/${draft.uuid}`);
          if (latest.status === "open" && tools.snapshot(latest) && JSON.stringify(tools.snapshot(latest)) === JSON.stringify(value)) return latest;
        }
        throw error;
      }
    },
    onState: info => {
      if (!active()) return;
      $("#capture-save-state").textContent = info.error ? "暂存失败 · 当前输入仍保留" : info.saving ? "正在暂存文字…" : info.dirty ? "编辑中 · 停止输入后自动暂存" : info.revision ? "文字已暂存到本地数据库 · 尚未提交" : "新草稿 · 输入后自动暂存";
      $("#capture-latest").hidden = info.error?.status !== 409;
      if (info.error) captureMessage(info.error.message, true);
      if (!info.error && !info.dirty && !info.saving && info.revision > state.listSavedRevision) {
        state.listSavedRevision = info.revision; listDrafts();
      }
    },
  });
  $("#capture-save-state").textContent = writable ? draft.revision ? "文字已暂存到本地数据库 · 尚未提交" : "新草稿 · 输入后自动暂存" : draft.status === "submitted" ? "已保存到收集箱" : "草稿已收起 · 在右侧恢复后继续";
  form.oninput = event => { if (writable && event.target.id !== "capture-files") state.saver.edit(); };
  form.onchange = event => { if (writable && event.target.id !== "capture-files") state.saver.edit(); };
  $("#capture-template").onclick = () => {
    const field = form.elements.content;
    field.value = tools.insertTemplate(field.value, form.elements.template_key.value);
    state.saver.edit(); field.focus();
  };
  function renderFiles() {
    $("#capture-file-list").innerHTML = state.files.map(file => `<li>${esc(file.name)} <small>${(file.size / 1024).toFixed(0)} KB</small></li>`).join("");
    $("#capture-remove-files").hidden = !state.files.length;
  }
  $("#capture-files").onchange = event => {
    const added = [...event.target.files];
    if (added.some(file => file.size > 100 * 1024 * 1024) || state.files.length + added.length > 20) {
      captureMessage("一次最多 20 个附件，单个附件最大 100 MB。", true); event.target.value = ""; return;
    }
    state.files.push(...added); event.target.value = ""; renderFiles();
  };
  $("#capture-remove-files").onclick = () => { state.files = []; renderFiles(); captureMessage("已移除待上传附件，文字草稿保留。"); };
  $("#capture-save").onclick = busy(async () => { await state.saver.flush(true); captureMessage("文字已暂存。可以稍后在“我的草稿”继续。"); await listDrafts(); });
  $("#capture-latest").onclick = busy(async () => {
    if (!active() || state.busy || state.submitAttempted) throw Error("请在当前草稿完成保存状态核实后，再对照服务器版本。");
    const latest = await api(`/api/capture-drafts/${draft.uuid}`);
    if (!active()) throw Error("草稿已切换，请在当前草稿重新打开对照。");
    state.compareOpen = true;
    modal("对照服务器草稿", `<p>当前编辑框里的文字保留。下面是服务器保存的版本 ${esc(latest.revision)}；请复制需要保留的文字后再加载。</p><h3>${esc(latest.title)}</h3><pre class="capture-compare">${esc(latest.content)}</pre><div class="actions"><button id="capture-use-server" class="primary">加载服务器版本</button><button id="capture-copy-new">将当前文字另存为新草稿</button></div>`);
    $("#capture-use-server").onclick = busy(async () => {
      if (!active() || state.busy || state.submitAttempted) throw Error("草稿已切换或正在保存，请重新打开当前草稿的对照。");
      if (state.files.length) throw Error("请先移除待上传附件，再加载服务器版本。");
      state.saver.stop(); CaptureWorkspace = null; $("#dialog").close(); await captureWorkspacePage(draft.uuid);
    });
    $("#capture-copy-new").onclick = busy(async () => {
      if (!active() || state.busy || state.submitAttempted) throw Error("草稿已切换或正在保存，请重新打开当前草稿的对照。");
      if (state.files.length) throw Error("请先移除待上传附件，再将文字另存为新草稿。");
      const copy = await api(`/api/capture-drafts/${crypto.randomUUID()}`, { method: "PUT", body: { ...read(), revision: 0 } });
      if (!active()) { toast("已将文字另存为新草稿，可在“我的草稿”继续。"); return; }
      state.saver.stop(); state.files = []; CaptureWorkspace = null; $("#dialog").close(); location.hash = `quick/${copy.uuid}`;
      toast("当前文字已另存为新草稿；附件需重新选择。");
    });
  });
  form.onsubmit = async event => {
    event.preventDefault();
    if (state.busy || !writable || !active()) return;
    if (!read().content.trim()) { captureMessage("请先填写实际记录正文，再确认保存。", true); return; }
    state.busy = true; lock(true);
    try {
      if (!state.submitted) {
        // If a submit response was lost, resolve its durable status before PUT.
        if (state.submitAttempted) {
          const latest = await api(`/api/capture-drafts/${draft.uuid}`);
          if (latest.status === "submitted") state.submitted = latest.submitted_entry_uuid;
        }
        if (!state.submitted) {
          const revision = await state.saver.flush(true);
          state.submitAttempted = true;
          const result = await api(`/api/capture-drafts/${draft.uuid}/submit`, { method: "POST", body: { revision } });
          state.submitted = result.entry.uuid;
        }
        state.saver.stop();
      }
      while (state.files.length) {
        const file = state.files[0];
        const hash = [...new Uint8Array(await crypto.subtle.digest("SHA-256", await file.arrayBuffer()))].map(value => value.toString(16).padStart(2,"0")).join("");
        const entry = await api(`/api/entries/${state.submitted}`);
        if (!entry.attachments?.some(item => item.sha256 === hash && item.file_size === file.size && item.original_filename === file.name)) {
          const data = new FormData(); data.append("file", file, file.name);
          await api(`/api/entries/${state.submitted}/attachments`, { method: "POST", body: data });
        }
        state.files.shift(); renderFiles();
      }
      const saved = state.submitted; state.files = []; CaptureWorkspace = null;
      toast("已保存到收集箱，可以稍后整理审核。"); location.hash = `item/${saved}`;
    } catch (error) {
      if (state.submitAttempted && !state.submitted) {
        try {
          const latest = await api(`/api/capture-drafts/${draft.uuid}`);
          if (latest.status === "submitted") { state.submitted = latest.submitted_entry_uuid; state.saver.stop(); }
          else if (latest.status === "open") state.submitAttempted = false;
        } catch { /* Keep prose locked until the durable submit result is known. */ }
      }
      if (error.status === 409) $("#capture-latest").hidden = false;
      captureMessage(state.submitted ? `文字已保存，剩余附件尚未上传：${error.message} 点击“继续上传附件”重试，不会重复创建记录。` :
        state.submitAttempted ? "暂时无法核实保存结果。文字已锁定保留，请点击“核实保存结果并重试”，避免覆盖已经保存的内容。" : error.message, true);
    } finally {
      state.busy = false;
      if (active()) {
        lock(false);
        if (state.submitted || state.submitAttempted) {
          [...form.querySelectorAll("input, textarea, select")].forEach(node => node.disabled = true);
          $("#capture-save").disabled = true; $("#capture-template").disabled = true;
          $("#capture-submit").textContent = state.submitted ? "继续上传附件" : "核实保存结果并重试";
        }
      }
    }
  };
  form.onkeydown = event => { if (event.ctrlKey && event.key === "Enter") { event.preventDefault(); form.requestSubmit(); } };
  async function go(target) {
    if (state.busy || state.files.length) throw Error("请先完成附件保存，或移除待上传附件。");
    if (state.submitAttempted && !state.submitted) throw Error("请先核实记录保存结果，再切换草稿。");
    if (!state.submitted) await state.saver.flush(); state.saver.stop(); CaptureWorkspace = null;
    if (location.hash === target) await captureWorkspacePage(target.split("/")[1]); else location.hash = target;
  }
  $("#capture-new").onclick = busy(() => go("#quick"));
  $("#capture-list-open").onclick = busy(async () => { state.listStatus = "open"; state.listPage = 1; await listDrafts(); });
  $("#capture-list-discarded").onclick = busy(async () => { state.listStatus = "discarded"; state.listPage = 1; await listDrafts(); });
  $("#capture-list-refresh").onclick = busy(listDrafts);
  async function listDrafts() {
    const serial = ++state.listSerial;
    let result;
    try { result = await api(`/api/capture-drafts?status=${state.listStatus}&page=${state.listPage}&size=10`); }
    catch (error) {
      if (active() && serial === state.listSerial) {
        $("#capture-draft-list").textContent = "草稿列表暂时未加载。点击上方“刷新”重试，当前输入保留。";
        $("#capture-list-pages").textContent = "";
      }
      return;
    }
    if (!active() || serial !== state.listSerial) return;
    $("#capture-list-open").textContent = `待继续 ${result.counts.open}`;
    $("#capture-list-discarded").textContent = `已收起 ${result.counts.discarded}`;
    $("#capture-draft-list").innerHTML = result.items.map(item => `<article class="capture-draft-row"><h3>${esc(item.title || item.content_preview.slice(0, 24) || "未填写的草稿")}</h3><small>${esc(item.date)} · 隐私 ${esc(item.privacy_level)} · ${esc(fmtDate(item.updated_at))}</small><p>${esc(item.content_preview || "空白草稿")}</p><div class="actions">${state.listStatus === "open" ? `<button type="button" data-capture-open="${esc(item.uuid)}">继续编辑</button><button type="button" data-capture-discard="${esc(item.uuid)}" data-revision="${item.revision}">收起</button>` : `<button type="button" data-capture-restore="${esc(item.uuid)}" data-revision="${item.revision}">恢复草稿</button>`}</div></article>`).join("") || `<p class="muted">${state.listStatus === "open" ? "没有待继续的草稿。输入文字后会自动暂存。" : "没有收起的草稿。收起后仍可在这里恢复。"}</p>`;
    $("#capture-list-pages").innerHTML = `<button id="capture-prev" ${state.listPage <= 1 ? "disabled" : ""}>上一页</button><small>第 ${state.listPage} 页 · 共 ${result.total} 条</small><button id="capture-next" ${state.listPage * 10 >= result.total ? "disabled" : ""}>下一页</button>`;
    $("#capture-prev").onclick = busy(async () => { state.listPage--; await listDrafts(); });
    $("#capture-next").onclick = busy(async () => { state.listPage++; await listDrafts(); });
    $$('[data-capture-open]').forEach(button => button.onclick = busy(() => go(`#quick/${button.dataset.captureOpen}`)));
    $$('[data-capture-discard]').forEach(button => button.onclick = busy(async () => {
      const uuid = button.dataset.captureDiscard;
      if (uuid === draft.uuid) {
        if (state.files.length || state.busy) throw Error("请先处理待上传附件。");
        const revision = await state.saver.flush();
        await api(`/api/capture-drafts/${uuid}/discard`, { method: "POST", body: { revision } });
        state.saver.stop(); CaptureWorkspace = null; toast("草稿已收起，可以恢复。");
        if (location.hash === "#quick") await captureWorkspacePage(); else location.hash = "quick";
      } else { await api(`/api/capture-drafts/${uuid}/discard`, { method: "POST", body: { revision: Number(button.dataset.revision) } }); await listDrafts(); }
    }));
    $$('[data-capture-restore]').forEach(button => button.onclick = busy(async () => {
      const uuid = button.dataset.captureRestore;
      await api(`/api/capture-drafts/${uuid}/restore`, { method: "POST", body: { revision: Number(button.dataset.revision) } });
      toast("草稿已恢复。"); await go(`#quick/${uuid}`);
    }));
  }
  if (!writable) {
    lock(true);
    if (draft.submitted_entry_uuid) captureMessage("这份草稿已提交。请在收集箱记录详情中继续修改文字。");
  }
  if (draft.submitted_entry_uuid) $("#capture-form").insertAdjacentHTML("beforeend", `<a href="#item/${esc(draft.submitted_entry_uuid)}">打开已保存的记录 →</a>`);
  await listDrafts();
}
