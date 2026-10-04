"use strict";

function aiFollowupProvenance(task) {
  const source = task?.metadata_json?.ai_followup;
  if (!source?.draft_uuid) return "";
  return `<section class="panel ai-followups"><h2>待办来源</h2><p><a href="#ai-draft/${esc(source.draft_uuid)}">查看来源审核稿 →</a> · 来源版本 ${esc(source.draft_revision)}</p>${source.source_uuid ? `<p><a href="#item/${esc(source.source_uuid)}">查看手动关联的原记录 →</a></p>` : ""}${source.archived_uuid ? `<p><a href="#item/${esc(source.archived_uuid)}">查看当时对应的归档资料 →</a></p>` : ""}<p class="muted">${source.draft_status_at_creation === "pending_review" ? "创建时审核稿尚未确认归档。这条计划需自行核对，创建待办不代表已完成或已证实。" : "这条待办由你从原有计划确认创建，完成状态由你维护。"}</p><details><summary>创建时的计划原句</summary><pre class="ai-source-text">${esc(source.candidate_text || "")}</pre></details></section>`;
}

// A user chooses and confirms each task. No background task creation occurs.
async function aiFollowupsMount({ uid, draft, form, editable, pageToken }) {
  if (!["pending_review", "approved"].includes(draft.status)) return;
  const panel = document.createElement("section");
  panel.className = "ai-followups";
  panel.innerHTML = `<details><summary>后续事项转为待办</summary><p class="muted">从明确的“后续计划 / 下一步”等段落提取候选。请核对原句，按需要修改标题、选择日期与关联项，再逐条确认创建。</p><div class="actions"><button type="button" data-followup-refresh>读取 / 刷新后续事项</button><a href="#tasks">查看全部待办 →</a></div><p data-followup-state role="status" aria-live="polite"></p><div data-followup-warning></div><div data-followup-items></div></details>`;
  form.insertAdjacentElement("afterend", panel);
  const active = () => panel.isConnected && pageToken === AIWorkflowUI.reviewPageToken && location.hash.split("?")[0] === "#ai-draft/" + uid;
  const state = panel.querySelector("[data-followup-state]");
  const list = panel.querySelector("[data-followup-items]");
  const refresh = panel.querySelector("[data-followup-refresh]");
  const savedFields = new Map();
  const retries = new Map();
  let loaded = null, loading = false, creating = 0;

  async function settleDraft() {
    if (!active()) throw Error("页面已离开，请重新打开这份草稿。");
    if (editable) {
      const controller = AIWorkflowUI.activeReview;
      if (!controller) throw Error("审核稿暂存尚未准备，请刷新页面。");
      await controller.flush();
      if (!active()) throw Error("页面已离开，请重新打开这份草稿。");
    }
  }
  function remember() {
    list.querySelectorAll("form[data-followup-id]").forEach(card => savedFields.set(card.dataset.followupId, {
      title: card.elements.title.value, due_date: card.elements.due_date.value,
      source_uuid: card.elements.source_uuid.value, project_uuid: card.elements.project_uuid.value,
      project_name: card.elements.project_uuid.selectedOptions[0]?.textContent || "",
    }));
  }
  function lockCard(card, locked) {
    card.querySelectorAll("input, select, button").forEach(field => field.disabled = locked);
  }
  function showCreated(card, task, created) {
    const target = card.querySelector("[data-followup-result]");
    target.classList.remove("danger");
    card.elements.title.value = task.title;
    target.innerHTML = `<a href="#entity/tasks/${esc(task.uuid)}">${created ? "已创建待办" : "已存在，未重复创建"}：${esc(task.title)} →</a><span class="pill">${esc(task.status)}</span>`;
    lockCard(card, true);
    const button = card.querySelector("[data-followup-create]");
    if (button) button.textContent = "已创建，可在待办中继续修改";
    retries.delete(card.dataset.followupId);
  }
  async function create(card, candidate) {
    const result = card.querySelector("[data-followup-result]");
    const submit = card.querySelector("[data-followup-create]");
    let request = retries.get(candidate.id);
    lockCard(card, true);
    creating++;
    refresh.disabled = true;
    result.textContent = "正在核对并保存待办…";
    try {
      if (!request) {
        await settleDraft();
        if (editable && (AIWorkflowUI.activeReview.dirty || AIWorkflowUI.activeReview.revision !== loaded.revision)) {
          throw Error("审核稿已变化，请刷新后续事项后再创建。下方填写内容会保留。");
        }
        const title = card.elements.title.value.trim();
        if (!title) throw Error("请填写待办标题。");
        request = { revision: loaded.revision, content_hash: loaded.content_hash, title,
          due_date: card.elements.due_date.value || null,
          source_uuid: card.elements.source_uuid.value || null,
          project_uuid: card.elements.project_uuid.value || null, confirmation: "创建待办" };
        // Keep the exact attempted request on uncertain network failure. Retrying
        // must find the existing task rather than overwrite its current values.
        retries.set(candidate.id, request);
      }
      const response = await api(`/api/ai/drafts/${encodeURIComponent(uid)}/followups/${candidate.id}/task`, { method: "POST", body: request });
      if (active()) {
        showCreated(card, response.task, response.created);
        toast(response.created ? "已创建待办，审核稿与归档状态未改变。" : "这条事项已有待办，未重复创建。");
      }
    } catch (error) {
      // A definite client error cannot have committed a task. The next explicit
      // click uses the user's corrected fields; transport failures retry exactly.
      if (error.status >= 400 && error.status < 500) retries.delete(candidate.id);
      if (active()) {
        lockCard(card, false);
        submit.textContent = retries.has(candidate.id) ? "重试创建（自动防止重复）" : "确认创建待办";
        result.textContent = error.message + (retries.has(candidate.id) ? " 请求结果尚未确认；重试只核对首次提交，已有待办不会重复创建。" : "");
        if (retries.has(candidate.id)) card.querySelectorAll("input, select").forEach(field => field.disabled = true);
        result.classList.add("danger");
      }
    } finally {
      creating--;
      if (active()) refresh.disabled = creating > 0 || loading;
    }
  }
  async function load() {
    if (loading || creating || !active()) return;
    loading = true;
    refresh.disabled = true;
    remember();
    state.textContent = "正在读取当前已暂存的后续计划…";
    try {
      await settleDraft();
      const data = await api(`/api/ai/drafts/${encodeURIComponent(uid)}/followups`);
      if (!active()) return;
      if (editable && (AIWorkflowUI.activeReview.dirty || AIWorkflowUI.activeReview.revision !== data.revision)) {
        throw Error("读取时审核稿又有修改，请暂存后重新读取。已填写的待办信息保留。");
      }
      loaded = data;
      panel.querySelector("[data-followup-warning]").innerHTML = (data.warnings || []).map(text => `<p class="muted">${esc(text)}</p>`).join("");
      state.textContent = `来自版本 ${data.revision} · ${data.items.length} 条候选。${data.omitted ? "部分过长事项未显示。" : ""}`;
      list.innerHTML = data.items.length ? data.items.map((candidate, index) => {
        const fields = savedFields.get(candidate.id) || { title: candidate.suggested_title, due_date: "", source_uuid: "", project_uuid: "" };
        const selectedProject = fields.project_uuid && !data.projects.some(project => project.uuid === fields.project_uuid) ? `<option value="${esc(fields.project_uuid)}" selected>${esc(fields.project_name || "之前选择的项目")}</option>` : "";
        return `<form class="ai-followup-card" data-followup-id="${candidate.id}"><h3>${index + 1}. ${esc(candidate.section)}</h3><details><summary>核对草稿原句</summary><pre class="ai-source-text">${esc(candidate.source_text)}</pre></details>${candidate.title_truncated ? '<p class="muted">原句较长，标题仅取前 500 字，请检查并缩短。</p>' : ""}<div class="form-grid"><label class="full">待办标题<input name="title" maxlength="500" required value="${esc(fields.title)}"></label><label>到期日期 · 可留空<input name="due_date" type="date" value="${esc(fields.due_date)}"></label><label>关联项目 · 可留空<select name="project_uuid" data-reference-kind="projects"><option value="">不关联项目</option>${selectedProject}${data.projects.map(project => `<option value="${esc(project.uuid)}" ${fields.project_uuid === project.uuid ? "selected" : ""}>${esc(project.name)}</option>`).join("")}</select></label><label class="full">关联原记录 · 可留空<select name="source_uuid"><option value="">仅保留草稿来源</option>${data.sources.map(source => `<option value="${esc(source.uuid)}" ${fields.source_uuid === source.uuid ? "selected" : ""}>${esc(source.date)} · ${esc(source.title)}</option>`).join("")}</select></label></div><div class="actions"><button type="button" class="primary" data-followup-create>确认创建待办</button></div><p data-followup-result role="status" aria-live="polite"></p></form>`;
      }).join("") : '<p class="muted">未找到明确的后续计划段落。可以先在审核稿中写下“## 后续计划”和逐条事项，暂存后再读取，也可直接到待办页面手动新建。</p>';
      if (typeof attachLookups === "function") attachLookups(list);
      list.querySelectorAll("form[data-followup-id]").forEach((card, index) => {
        const candidate = data.items[index];
        // Enter in a title or project-search input must not create a task.
        // Only deliberate activation of the named confirmation button writes.
        card.onsubmit = event => event.preventDefault();
        card.querySelector("[data-followup-create]").onclick = () => {
          if (card.reportValidity()) create(card, candidate);
        };
        if (candidate.task) showCreated(card, candidate.task, false);
      });
    } catch (error) {
      if (active()) state.textContent = error.message;
    } finally {
      loading = false;
      if (active()) refresh.disabled = creating > 0;
    }
  }
  refresh.onclick = load;
  let opened = false;
  panel.querySelector("details").addEventListener("toggle", event => {
    if (event.target.open && !opened) { opened = true; load(); }
  });
}
