"use strict";

// The server owns jobs and drafts. The browser never approves a draft automatically.
const AIWorkflowUI = {
  labels: { queued: "等待整理", running: "正在整理", pending_review: "待我审核", approved: "已确认归档", failed: "整理失败", rejected: "已退回" },
  dirty: false,
  pollTimer: null,
  draftBuffers: new Map(),
  reviewSessions: new Map(),
  activeReview: null,
  reviewPageToken: 0,
};

function aiStatusPill(status) {
  return `<span class="ai-state ai-state-${esc(status || "queued")}">${esc(AIWorkflowUI.labels[status] || status || "等待整理")}</span>`;
}

function aiWarningText(value) {
  return typeof value === "string" ? value : value?.message || value?.text || JSON.stringify(value);
}

function aiArray(value) {
  return Array.isArray(value) ? value : [];
}

function aiPageText(page, fullText = "") {
  if (typeof page.text === "string") return page.text;
  if (Number.isInteger(page.start) && Number.isInteger(page.end)) return fullText.slice(page.start, page.end);
  return "";
}

function aiPageLabel(page, index) {
  return page.label || (page.kind === "document" ? "文档文本" : `第 ${page.page || page.page_number || index + 1} 页`);
}

function aiSourceAttachments(source) {
  return aiArray(source.attachments).map(attachment => `<details class="ai-extraction-page" data-ai-source-attachment="${esc(attachment.uuid)}"><summary>附件依据：${esc(attachment.filename || attachment.uuid)} · ${esc(attachment.status || "pending")}</summary><button type="button" class="small" data-ai-extraction="${esc(attachment.uuid)}">核对附件提取结果</button>${attachment.error ? `<p class="danger">${esc(attachment.error)}</p>` : ""}${attachment.truncated ? '<p class="muted">仅包含已提取的部分内容，请核对原附件。</p>' : ""}${aiArray(attachment.pages).length ? aiArray(attachment.pages).map((page, index) => `<details data-ai-source-page="${esc(page.page || page.page_number || index + 1)}"><summary>${esc(aiPageLabel(page, index))}</summary><pre class="ai-source-text">${esc(aiPageText(page, attachment.text || ""))}</pre></details>`).join("") : attachment.text ? `<pre class="ai-source-text">${esc(attachment.text)}</pre>` : '<p class="muted">该附件尚无可用提取文字，未作为文字依据。</p>'}</details>`).join("");
}

function aiRoute(pageName, values = {}) {
  const query = new URLSearchParams(Object.entries(values).filter(([, value]) => value !== "" && value !== null && value !== undefined));
  return pageName + (query.size ? "?" + query : "");
}

function aiDraftValue(value) {
  return value?.draft || value;
}

function aiStopPolling() {
  clearTimeout(AIWorkflowUI.pollTimer);
  AIWorkflowUI.pollTimer = null;
}

function aiStopReview() {
  AIWorkflowUI.activeReview?.stop();
  AIWorkflowUI.activeReview = null;
  AIWorkflowUI.reviewPageToken++;
}

window.addEventListener("hashchange", () => { aiStopPolling(); aiStopReview(); });

window.addEventListener("beforeunload", event => {
  if (AIWorkflowUI.dirty || AIWorkflowUI.draftBuffers.size) {
    event.preventDefault();
    event.returnValue = "";
  }
});

async function aiWorkflowPage(dateValue = null) {
  aiStopPolling();
  aiStopReview();
  AIWorkflowUI.dirty = false;
  const parameters = new URLSearchParams(location.hash.split("?")[1] || "");
  const selectedDate = parameters.get("all_dates") === "1" ? "" : dateValue || parameters.get("date") || today();
  const status = parameters.get("status") || "";
  const currentPage = Math.max(1, Number(parameters.get("page")) || 1);
  const request = new URLSearchParams({ page: currentPage });
  if (selectedDate) request.set("date", selectedDate);
  if (status) request.set("status", status);
  const result = await api("/api/ai/drafts?" + request);
  const rows = aiArray(result.items || result.drafts || (Array.isArray(result) ? result : []));
  const total = Number(result.total ?? rows.length);
  const size = Number(result.size || 20);
  const counts = result.counts || {};
  const pending = counts.pending_review ?? rows.filter(row => row.status === "pending_review").length;
  const active = (counts.queued ?? rows.filter(row => row.status === "queued").length) + (counts.running ?? rows.filter(row => row.status === "running").length);
  $("#main").innerHTML = heading("每日整理与审核", "原始记录先保存，AI 提供草稿，由你修改、确认后归档。", '<a class="ai-link-button" href="#ai-workflow?all_dates=1&status=pending_review">全部待审核 →</a><a class="ai-link-button" href="#ai-settings">AI 与模型设置 →</a>') +
    `<div class="ai-flow"><span>① 收集原文</span><span>② AI 整理</span><span class="ai-flow-current">③ 我的审核</span><span>④ 确认归档</span></div>` +
    `<div class="stats ai-metrics"><div class="stat"><label>当前筛选草稿</label><strong>${total}</strong><small>原文保持独立保存</small></div><div class="stat"><label>${counts.pending_review === undefined ? "本页" : ""}待审核</label><strong>${pending}</strong><small>审核后才形成归档资料</small></div><div class="stat"><label>${counts.queued === undefined ? "本页" : ""}等待 / 整理中</label><strong>${active}</strong><small>可以离开页面，任务仍由服务处理</small></div></div>` +
    `<section class="panel"><form id="ai-workflow-filter" class="ai-filter"><label>整理日期<input type="date" name="date" value="${esc(selectedDate)}"></label><label>草稿状态<select name="status"><option value="">全部状态</option>${Object.entries(AIWorkflowUI.labels).map(([key, label]) => `<option value="${key}" ${key === status ? "selected" : ""}>${label}</option>`).join("")}</select></label><button type="submit">查看</button><button type="button" id="ai-daily-start" class="primary" ${selectedDate ? "" : "disabled"}>整理这一天的记录</button><button type="button" id="ai-workflow-refresh">刷新状态</button></form><p class="muted ai-helper">任务不会代替你确认事实。你可以在设置中开启每天定时整理或保存后整理，生成结果都进入待审核。</p></section>` +
    `<section class="panel ai-draft-list">${rows.length ? rows.map(row => `<article class="ai-draft-card"><div><div class="meta">${aiStatusPill(row.status)}<span>${esc(row.date || selectedDate)}</span><span>${esc(row.provider || "")} ${esc(row.model || "")}</span></div><h2><a href="#ai-draft/${esc(row.uuid)}">${esc(row.title || "等待生成每日草稿")}</a></h2><p>${esc((row.summary || row.content || "整理完成后可以查看与修改草稿。").slice(0, 220))}</p>${row.error ? `<p class="danger ai-error">${esc(row.error)}</p>` : ""}<div class="meta"><span>待核实 ${aiArray(row.questions).length} 项</span>${row.archived_uuid ? `<a href="#item/${esc(row.archived_uuid)}">查看已归档资料 →</a>` : ""}</div></div><a class="ai-link-button" href="#ai-draft/${esc(row.uuid)}">${row.status === "pending_review" ? "开始审核" : "查看进度"} →</a></article>`).join("") : '<div class="empty"><span class="glyph">↻</span>当前筛选没有整理草稿。可查看全部待审核，或选择一天整理记录。</div>'}</section>` +
    `<div class="pagination"><button id="ai-drafts-prev" ${currentPage <= 1 ? "disabled" : ""}>上一页</button><span>第 ${currentPage} 页 · 共 ${total} 份草稿</span><button id="ai-drafts-next" ${currentPage * size >= total ? "disabled" : ""}>下一页</button></div>`;
  $("#ai-workflow-filter").onsubmit = event => {
    event.preventDefault();
    const values = Object.fromEntries(new FormData(event.currentTarget));
    if (!values.date) values.all_dates = "1";
    location.hash = aiRoute("ai-workflow", values);
  };
  $("#ai-workflow-filter").elements.date.oninput = event => { $("#ai-daily-start").disabled = !event.target.value; };
  $("#ai-workflow-refresh").onclick = busy(() => aiWorkflowPage(selectedDate));
  $("#ai-daily-start").onclick = busy(async () => {
    const chosen = $("#ai-workflow-filter").elements.date.value;
    if (!chosen) throw Error("请先选择需要整理的日期。");
    const queued = aiDraftValue(await api("/api/ai/daily", { method: "POST", body: { date: chosen } }));
    toast("整理任务已提交，完成后请审核草稿。");
    if (queued?.uuid || queued?.draft_uuid) location.hash = "ai-draft/" + (queued.uuid || queued.draft_uuid);
    else await aiWorkflowPage(chosen);
  });
  $("#ai-drafts-prev").onclick = () => location.hash = aiRoute("ai-workflow", { date: selectedDate, all_dates: selectedDate ? undefined : "1", status, page: currentPage - 1 });
  $("#ai-drafts-next").onclick = () => location.hash = aiRoute("ai-workflow", { date: selectedDate, all_dates: selectedDate ? undefined : "1", status, page: currentPage + 1 });
  if (location.hash.startsWith("#ai-workflow")) {
    function refreshWhenIdle() {
      if (!location.hash.startsWith("#ai-workflow")) return;
      if (document.activeElement?.closest("#ai-workflow-filter")) {
        AIWorkflowUI.pollTimer = setTimeout(refreshWhenIdle, 6000);
        return;
      }
      aiWorkflowPage(selectedDate).catch(error => toast(error.message));
    }
    AIWorkflowUI.pollTimer = setTimeout(refreshWhenIdle, 6000);
  }
}

async function draftDetailPage(uid) {
  aiStopPolling();
  aiStopReview();
  const pageToken = AIWorkflowUI.reviewPageToken;
  await AIWorkflowUI.reviewSessions.get(uid)?.settle();
  const draft = aiDraftValue(await api("/api/ai/drafts/" + encodeURIComponent(uid)));
  if (pageToken !== AIWorkflowUI.reviewPageToken || location.hash.split("?")[0] !== "#ai-draft/" + uid) return;
  const editable = draft.status === "pending_review";
  const sources = aiArray(draft.source_records);
  const questions = aiArray(draft.questions);
  const warnings = aiArray(draft.warnings);
  const uncertain = questions.length > 0 || warnings.length > 0;
  const periodDraft = /^(week|month):/.test(draft.scope_key || "");
  AIWorkflowUI.dirty = false;
  const disabled = editable ? "" : "disabled";
  $("#main").innerHTML = `<a href="#${periodDraft ? "period-review" : aiRoute("ai-workflow", { date: draft.date })}">← ${periodDraft ? "周月复盘" : "每日整理与审核"}</a>` +
    heading(draft.title || "每日整理草稿", `${draft.date || ""} · ${draft.provider || ""} ${draft.model || ""}`, aiStatusPill(draft.status)) +
    `<div class="notice">原始记录保留在左侧。右侧草稿可以修改；只有你点击“确认归档”后才会保存或更新对应归档资料。</div>` +
    (draft.error ? `<section class="panel"><h2>这次整理未完成</h2><p class="danger ai-error">${esc(draft.error)}</p><a href="#ai-settings">检查模型与服务设置 →</a></section>` : "") +
    `<div class="ai-review-grid"><section class="panel ai-source-panel"><div class="panel-top"><h2>原始依据 <span class="pill">${sources.length} 条</span></h2></div>${sources.length ? sources.map((source, index) => `<details data-ai-source="${esc(source.uuid)}" ${index === 0 ? "open" : ""}><summary>${esc(source.title || "原始记录")} <a href="#item/${esc(source.uuid)}" target="_blank" rel="noopener">单独打开原记录 ↗</a></summary><p class="ai-source-meta">UUID：${esc(source.uuid)}<br>最近更新：${esc(source.updated_at || "未知")}</p><pre class="ai-source-text">${esc(source.original_content || source.content || "无正文")}</pre>${source.original_content && source.content && source.original_content !== source.content ? `<details><summary>当前记录正文</summary><pre class="ai-source-text">${esc(source.content)}</pre></details>` : ""}${aiArray(source.pages).map((sourcePage, pageIndex) => `<details><summary>附件 / ${esc(aiPageLabel(sourcePage, pageIndex))}</summary><pre class="ai-source-text">${esc(aiPageText(sourcePage, source.text || source.content || ""))}</pre></details>`).join("")}${aiSourceAttachments(source)}</details>`).join("") : '<p class="muted">暂无原始依据。任务完成后显示来源记录及 UUID。</p>'}</section>` +
    `<section class="panel ai-draft-editor"><div class="panel-top"><h2>我的审核稿</h2><span class="muted" id="ai-draft-revision">版本 ${esc(draft.revision ?? 0)}</span></div>${aiArray(draft.evidence).length ? `<details class="ai-evidence-list"><summary>查看 AI 引用依据 · ${aiArray(draft.evidence).length} 处</summary>${aiArray(draft.evidence).map((citation, index) => `<div class="ai-evidence"><button type="button" class="small ai-locate-source" data-ai-locate="${index}">定位来源</button><span class="ai-source-meta"> 原文 ${esc(citation.source_uuid)}</span>${citation.attachment_uuid ? `<span class="ai-source-meta"> · 附件 ${esc(citation.attachment_uuid)}${citation.page ? " · 第 " + esc(citation.page) + " 页" : ""}</span>` : ""}<blockquote>${esc(citation.quote || "")}</blockquote></div>`).join("")}</details>` : ""}<form id="ai-draft-form"><div class="ai-review-recovery" id="ai-review-recovery" hidden></div><div class="form-grid"><label class="full">归档标题<input name="title" value="${esc(draft.title || "")}" maxlength="500" required ${disabled}></label><label>分类<input name="category" value="${esc(draft.category || "每日总结")}" ${disabled}></label><label>标签 · 逗号分隔<input name="tags" value="${esc(aiArray(draft.tags).map(tag => typeof tag === "string" ? tag : tag.name).join(", "))}" ${disabled}></label><label class="full">摘要<textarea name="summary" rows="3" ${disabled}>${esc(draft.summary || "")}</textarea></label><label class="full">整理后的正文 · 可以直接修改<textarea name="content" class="ai-draft-content" rows="20" ${disabled}>${esc(draft.content || "")}</textarea></label></div>` +
    `<div class="ai-review-toolbar"><button type="button" id="ai-review-compare">原文与审核稿对照</button>${editable ? '<button type="button" id="ai-review-rewrite">整理选中段落</button>' : ""}<span class="muted">文字差异帮助核对，仍需你确认事实。</span></div><details class="ai-review-difference" id="ai-review-difference"><summary>查看文字改写对照</summary><label>对照依据<select id="ai-review-diff-source"><option value="sources">全部原始记录正文</option>${sources.map((source, index) => `<option value="source:${index}">${esc(source.title || "原始记录 " + (index + 1))}</option>`).join("")}<option value="opened">本次打开时的审核稿</option></select></label><p class="muted ai-helper">多条原文按顺序拼接。标记只表示文字增删，不证明新增内容有事实依据；引用与附件请在左侧核对。</p><div id="ai-review-diff-output"></div></details>` +
    (editable ? '<section class="ai-rewrite-panel" id="ai-rewrite-panel" hidden><h3>整理选中段落</h3><p class="muted ai-helper">只提出替换建议。采用后仍可手动修改，确认归档由你操作。</p><h4>选中的原句</h4><pre class="ai-source-text" id="ai-rewrite-original"></pre><label>整理要求<input id="ai-rewrite-instruction" maxlength="500" value="理顺语句和结构，保留已有事实；不确定信息明确标注，不添加未经证实的细节。"></label><div class="actions"><button type="button" id="ai-rewrite-request" class="primary">生成替换建议</button><button type="button" id="ai-rewrite-cancel">收起 / 取消请求</button></div><p class="ai-rewrite-progress" id="ai-rewrite-state" role="status" aria-live="polite"></p><div id="ai-rewrite-result"></div></section>' : "") +
    (questions.length ? `<div class="ai-question-box"><h3>需要我核实与补充</h3>${questions.map((question, index) => `<label class="ai-question">${index + 1}. ${esc(question.question || question.text || "请核实这项信息")}<small>${esc(question.reason || "")}</small><textarea data-ai-answer="${esc(question.id ?? String(index + 1))}" rows="2" placeholder="补充事实、纠正判断，或说明目前无法确认" ${disabled}>${esc(draft.answers?.[question.id ?? String(index + 1)] || "")}</textarea></label>`).join("")}</div>` : '<p class="muted ai-helper">本次草稿没有列出待核实问题，仍请检查正文是否准确。</p>') +
    (warnings.length ? `<div class="ai-warning-box"><h3>整理说明</h3><ul>${warnings.map(warning => `<li>${esc(aiWarningText(warning))}</li>`).join("")}</ul></div>` : "") +
    (editable ? `<div class="ai-approval-box">${uncertain ? '<label class="ai-check"><input type="checkbox" name="acknowledge_uncertain"> 我已检查上述疑问和说明，确认当前审核稿可归档；尚未确认的信息已在正文注明。</label>' : '<p>点击确认后会保存或更新归档资料，保留原始记录和草稿依据。</p>'}<div class="ai-review-save" id="ai-review-save" data-state="saved"><p class="ai-helper" id="ai-draft-save-state" role="status" aria-live="polite">编辑后约 2 秒自动暂存，尚未归档。</p><button type="button" class="small" id="ai-draft-save-retry" hidden>重试暂存</button><button type="button" class="small" id="ai-draft-latest" hidden>加载服务器最新版本</button></div><div class="actions"><button type="submit" id="ai-draft-save">立即暂存修改</button><button type="button" id="ai-draft-approve" class="primary">确认归档</button><button type="button" id="ai-draft-reject" class="danger">退回这份草稿</button></div></div>` : draft.archived_uuid ? `<div class="notice"><a href="#item/${esc(draft.archived_uuid)}">已确认归档 · 查看正式资料 →</a></div>` : `<div class="actions"><button type="button" id="ai-draft-refresh">刷新进度</button>${["failed", "rejected"].includes(draft.status) ? '<button type="button" id="ai-draft-retry" class="primary">重新整理</button>' : ""}</div>`) +
    '</form></section></div>';
  const sourcePanel = $("#main .ai-source-panel");
  if (aiArray(draft.evidence).length) sourcePanel.insertAdjacentHTML("beforeend", `<details><summary>查看 AI 引用的原文 · ${draft.evidence.length} 项</summary><p class="muted">系统已检查引用是否存在于原文；引用存在不等于 AI 改写的事实判断正确，请对照审核。</p>${draft.evidence.map(item => `<blockquote class="ai-source-text">${esc(item.quote)}</blockquote><p class="ai-source-meta">来源 ${esc(item.source_uuid)}${item.attachment_uuid ? ' · 附件 ' + esc(item.attachment_uuid) : ''}${item.page !== null && item.page !== undefined ? ' · 第 ' + esc(item.page) + ' 页' : ''}</p>`).join("")}</details>`);
  const historyButton = document.createElement("button");
  historyButton.textContent = "查看审核修改历史";
  historyButton.className = "small";
  historyButton.onclick = busy(async () => {
    const result = await api(`/api/ai/drafts/${encodeURIComponent(uid)}/history`);
    const labels = { generated: "AI 生成", edited: "人工修改", approved: "确认归档", rejected: "退回" };
    modal("审核修改历史", `<p class="muted">显示最近 ${result.items.length} 个版本，共 ${result.total} 个。所有版本保存在完整数据库备份中。</p>${result.items.map(item => `<details><summary>版本 ${esc(item.revision)} · ${esc(labels[item.action] || item.action)} · ${esc(item.created_at)}</summary><h3>${esc(item.snapshot.title)}</h3><pre class="ai-source-text">${esc(item.snapshot.content)}</pre><h3>核实问题与回答</h3><pre class="ai-source-text">${esc(JSON.stringify({questions: item.snapshot.questions, answers: item.snapshot.answers}, null, 2))}</pre></details>`).join("") || '<p>任务完成后保存审核版本。</p>'}`);
  });
  sourcePanel.append(historyButton);
  const form = $("#ai-draft-form");
  document.querySelectorAll("[data-ai-extraction]").forEach(button => button.onclick = busy(() => attachmentExtractionUI(button.dataset.aiExtraction)));
  aiEnhanceReview({ uid, draft, sources, form, sourcePanel, editable, uncertain, pageToken });
  await aiFollowupsMount({ uid, draft, form, editable, pageToken });
  if ($("#ai-draft-refresh")) $("#ai-draft-refresh").onclick = busy(() => draftDetailPage(uid));
  if ($("#ai-draft-retry")) $("#ai-draft-retry").onclick = busy(async () => {
    await api("/api/ai/drafts/" + encodeURIComponent(uid) + "/retry", { method: "POST", body: { revision: draft.revision } });
    toast("重新整理任务已提交。");
    await draftDetailPage(uid);
  });
  if (["queued", "running"].includes(draft.status)) AIWorkflowUI.pollTimer = setTimeout(() => {
    if (location.hash.split("?")[0] === "#ai-draft/" + uid && !AIWorkflowUI.dirty) draftDetailPage(uid).catch(error => toast(error.message));
  }, 5000);
}

function aiEnhanceReview({ uid, draft, sources, form, sourcePanel, editable, uncertain, pageToken }) {
  const helpers = window.AIReviewTools;
  const isActive = () => form.isConnected && pageToken === AIWorkflowUI.reviewPageToken && location.hash.split("?")[0] === "#ai-draft/" + uid;
  const fieldNames = ["title", "content", "summary", "category", "tags"];
  const contentInput = form.elements.content;
  const diffDetails = form.querySelector("#ai-review-difference");
  const diffChoice = form.querySelector("#ai-review-diff-source");
  const diffOutput = form.querySelector("#ai-review-diff-output");
  let diffTimer = null, rewriteRequest = null, rewriteSerial = 0, selection = null, finalizing = false;

  function locateSource(citation) {
    if (!isActive()) return;
    const source = [...sourcePanel.querySelectorAll("[data-ai-source]")].find(node => node.dataset.aiSource === citation.source_uuid);
    if (!source) { toast("本页未包含这条来源，请检查草稿引用与修改历史。"); return; }
    sourcePanel.querySelectorAll(".ai-source-located").forEach(node => node.classList.remove("ai-source-located"));
    source.open = true;
    let target = source;
    if (citation.attachment_uuid) {
      const attachment = [...source.querySelectorAll("[data-ai-source-attachment]")].find(node => node.dataset.aiSourceAttachment === citation.attachment_uuid);
      if (attachment) {
        attachment.open = true;
        target = attachment;
        const page = [...attachment.querySelectorAll("[data-ai-source-page]")].find(node => node.dataset.aiSourcePage === String(citation.page));
        if (page) { page.open = true; target = page; }
      }
    }
    target.classList.add("ai-source-located");
    target.scrollIntoView({ block: "center", behavior: "auto" });
    target.querySelector("summary")?.focus({ preventScroll: true });
  }
  form.parentElement.querySelectorAll("[data-ai-locate]").forEach(button => button.onclick = () => locateSource(aiArray(draft.evidence)[Number(button.dataset.aiLocate)] || {}));

  function renderDifference() {
    if (!isActive() || !diffDetails.open) return;
    const sourceText = source => source.original_content || source.content || "";
    let original = sources.map(sourceText).join("\n\n");
    let label = "原始记录正文";
    if (diffChoice.value === "opened") { original = draft.content || ""; label = "本次打开时的审核稿"; }
    else if (diffChoice.value.startsWith("source:")) {
      const source = sources[Number(diffChoice.value.split(":")[1])];
      original = source ? sourceText(source) : "";
      label = source?.title || "原始记录正文";
    }
    const difference = helpers.textDifference(original, contentInput.value);
    function textFor(side) {
      return difference.parts.filter(part => part.kind === "same" || part.kind === side).map(part => part.kind === "same" ? esc(part.text) : `<mark class="ai-diff-${side}">${esc(part.text)}</mark>`).join("") || '<span class="muted">暂无文字</span>';
    }
    diffOutput.innerHTML = `${difference.truncated ? '<p class="muted ai-helper">长文仅预览双方各前 6,000 字；完整正文保留在原始依据和编辑框中。</p>' : ""}${difference.coarse ? '<p class="muted ai-helper">变化较多，本次按整段标记，以保持页面流畅。</p>' : ""}<div class="ai-diff-grid"><div><h3>${esc(label)} · 波浪下划线表示改动处</h3><pre class="ai-diff-text">${textFor("removed")}</pre></div><div><h3>当前审核稿 · 实线下划线表示新增或改写</h3><pre class="ai-diff-text">${textFor("added")}</pre></div></div>`;
  }
  form.querySelector("#ai-review-compare").onclick = () => { diffDetails.open = !diffDetails.open; renderDifference(); };
  diffDetails.addEventListener("toggle", renderDifference);
  diffChoice.onchange = renderDifference;
  contentInput.addEventListener("input", () => { clearTimeout(diffTimer); if (diffDetails.open) diffTimer = setTimeout(renderDifference, 250); });
  if (!editable) return;

  function readSnapshot() {
    const snapshot = Object.fromEntries(fieldNames.map(name => [name, form.elements[name].value]));
    snapshot.answers = {};
    form.querySelectorAll("[data-ai-answer]").forEach(input => snapshot.answers[input.dataset.aiAnswer] = input.value);
    return snapshot;
  }
  function restoreSnapshot(snapshot) {
    for (const name of fieldNames) form.elements[name].value = snapshot[name] ?? "";
    form.querySelectorAll("[data-ai-answer]").forEach(input => input.value = snapshot.answers?.[input.dataset.aiAnswer] || "");
    renderDifference();
  }
  const initial = readSnapshot();
  const owner = {};
  const stateNode = form.querySelector("#ai-draft-save-state");
  const retryButton = form.querySelector("#ai-draft-save-retry");
  const latestButton = form.querySelector("#ai-draft-latest");
  const revisionNode = form.parentElement.querySelector("#ai-draft-revision");
  function canWriteBuffer() { const current = AIWorkflowUI.draftBuffers.get(uid); return !current?.owner || current.owner === owner; }
  const controller = helpers.createAutosaver({
    revision: draft.revision,
    initial,
    isActive,
    read: readSnapshot,
    delay: 2000,
    onBuffer(value) { if (canWriteBuffer()) AIWorkflowUI.draftBuffers.set(uid, { ...value, owner }); if (isActive()) AIWorkflowUI.dirty = true; },
    onClear() { if (AIWorkflowUI.draftBuffers.get(uid)?.owner === owner) AIWorkflowUI.draftBuffers.delete(uid); if (isActive()) AIWorkflowUI.dirty = false; },
    onState(kind, { revision, error }) {
      revisionNode.textContent = "版本 " + revision;
      form.querySelector("#ai-review-save").dataset.state = kind;
      const conflict = error?.status === 409;
      stateNode.textContent = kind === "saving" ? "正在自动暂存，可以继续编辑；不会自动归档。" : kind === "waiting" ? "修改已留在本页，约 2 秒后自动暂存。" : kind === "failed" ? conflict ? "服务器版本已变化。当前修改仍保留，请加载最新版本对照后恢复修改。" : "暂存失败，当前修改仍保留。请重试暂存。" : "审核修改已暂存，尚未归档。";
      retryButton.hidden = kind !== "failed" || conflict;
      latestButton.hidden = !conflict;
      if (error && !conflict) stateNode.textContent += " " + error.message;
      const apply = form.querySelector("#ai-rewrite-apply");
      if (apply && (Number(apply.dataset.revision) !== revision || controller.dirty)) apply.disabled = true;
    },
    async persist(snapshot, revision) {
      if (!snapshot.title.trim()) throw Error("请填写归档标题后暂存。");
      const answers = Object.fromEntries(Object.entries(snapshot.answers).map(([key, value]) => [key, value.trim()]));
      const request = new AbortController();
      const timeout = setTimeout(() => request.abort(), 20000);
      try {
        return aiDraftValue(await api("/api/ai/drafts/" + encodeURIComponent(uid), { method: "PATCH", signal: request.signal, body: { ...snapshot, title: snapshot.title.trim(), tags: snapshot.tags.split(/[,，]/).map(value => value.trim()).filter(Boolean), answers, revision } }));
      } catch (error) {
        if (error.name === "AbortError") throw Error("暂存请求超时，请检查服务；可重试或加载最新版本。");
        throw error;
      } finally { clearTimeout(timeout); }
    },
  });
  const stopSaving = controller.stop;
  controller.stop = () => {
    stopSaving(); clearTimeout(diffTimer); rewriteSerial++; rewriteRequest?.abort();
    controller.settle().then(() => { if (AIWorkflowUI.reviewSessions.get(uid) === controller) AIWorkflowUI.reviewSessions.delete(uid); });
  };
  AIWorkflowUI.activeReview = controller;
  AIWorkflowUI.reviewSessions.set(uid, controller);

  const buffered = AIWorkflowUI.draftBuffers.get(uid);
  if (buffered) {
    const snapshot = buffered.snapshot || buffered;
    if (buffered.revision === draft.revision) {
      AIWorkflowUI.draftBuffers.delete(uid);
      restoreSnapshot(snapshot);
      controller.edit();
      stateNode.textContent = "已恢复尚未暂存的修改，将在约 2 秒后尝试暂存。";
    } else {
      const recovery = form.querySelector("#ai-review-recovery");
      recovery.hidden = false;
      recovery.innerHTML = '<p>服务器已有新版本。本页先显示服务器稿，你上次未保存的修改仍在浏览器缓冲中。请对照后决定是否恢复；恢复后将作为本次版本的修改暂存。</p><div class="actions"><button type="button" data-ai-recovery-view>查看未保存内容</button><button type="button" data-ai-recovery-restore>恢复我的修改到编辑框</button><button type="button" data-ai-recovery-discard>保留服务器稿，放弃缓冲修改</button></div>';
      const recoveryFields = [...fieldNames.map(name => form.elements[name]), ...form.querySelectorAll("[data-ai-answer]")];
      const recoveryActions = [form.querySelector("#ai-draft-approve"), form.querySelector("#ai-draft-reject"), form.querySelector("#ai-review-rewrite")];
      recoveryFields.forEach(input => input.readOnly = true);
      recoveryActions.forEach(button => button.disabled = true);
      function finishRecovery() {
        AIWorkflowUI.draftBuffers.delete(uid);
        recovery.hidden = true;
        recoveryFields.forEach(input => input.readOnly = false);
        recoveryActions.forEach(button => button.disabled = false);
      }
      recovery.querySelector("[data-ai-recovery-view]").onclick = () => modal("尚未暂存的审核修改", `<h3>${esc(snapshot.title || "审核稿")}</h3><pre class="ai-source-text">${esc(snapshot.content || "")}</pre><h3>其他字段与核实回答</h3><pre class="ai-source-text">${esc(JSON.stringify({ summary: snapshot.summary, category: snapshot.category, tags: snapshot.tags, answers: snapshot.answers }, null, 2))}</pre>`);
      recovery.querySelector("[data-ai-recovery-restore]").onclick = () => {
        if (!isActive()) return;
        finishRecovery();
        restoreSnapshot(snapshot);
        controller.edit();
      };
      recovery.querySelector("[data-ai-recovery-discard]").onclick = () => { if (isActive()) finishRecovery(); };
    }
  }
  form.addEventListener("input", event => {
    if (fieldNames.some(name => event.target === form.elements[name]) || event.target.matches("[data-ai-answer]")) controller.edit();
    if (event.target === contentInput) {
      const apply = form.querySelector("#ai-rewrite-apply");
      if (apply) apply.disabled = true;
    }
  });
  retryButton.onclick = busy(() => controller.flush());
  latestButton.onclick = busy(() => draftDetailPage(uid));
  form.onsubmit = busy(async event => { event.preventDefault(); await controller.flush(); if (isActive()) toast("审核修改已暂存，尚未归档。"); });

  async function finalize(action) {
    if (finalizing) return;
    finalizing = true;
    const buttons = [...form.querySelectorAll(".ai-approval-box button")];
    buttons.forEach(button => button.disabled = true);
    let locked = [];
    try {
      const saved = await controller.flush();
      if (!isActive()) return;
      locked = [...form.querySelectorAll("input, textarea, select")];
      locked.forEach(input => input.disabled = true);
      rewriteSerial++; rewriteRequest?.abort();
      await action(saved.revision);
    } finally {
      finalizing = false;
      if (isActive()) { buttons.forEach(button => button.disabled = false); locked.forEach(input => input.disabled = false); }
    }
  }
  form.querySelector("#ai-draft-approve").onclick = busy(async () => {
    if (!form.reportValidity()) return;
    const acknowledged = Boolean(form.elements.acknowledge_uncertain?.checked);
    if (uncertain && !acknowledged) throw Error("请先检查待核实问题，并勾选审核确认。可以把未知事项注明在正文。");
    await finalize(async revision => {
      const currentAcknowledgement = Boolean(form.elements.acknowledge_uncertain?.checked);
      if (uncertain && !currentAcknowledgement) throw Error("请先勾选审核确认，再确认归档。");
      const approved = aiDraftValue(await api("/api/ai/drafts/" + encodeURIComponent(uid) + "/approve", { method: "POST", body: { revision, confirmation: "确认归档", acknowledge_uncertain: currentAcknowledgement } }));
      AIWorkflowUI.draftBuffers.delete(uid);
      AIWorkflowUI.dirty = false;
      if (!isActive()) return;
      toast("已按你的审核稿确认归档，原始记录保留。");
      if (approved.archived_uuid) location.hash = "item/" + approved.archived_uuid;
      else await draftDetailPage(uid);
    });
  });
  form.querySelector("#ai-draft-reject").onclick = busy(() => finalize(async revision => {
    await api("/api/ai/drafts/" + encodeURIComponent(uid) + "/reject", { method: "POST", body: { revision } });
    AIWorkflowUI.draftBuffers.delete(uid);
    AIWorkflowUI.dirty = false;
    if (isActive()) { toast("草稿已退回，原始记录保留。"); await draftDetailPage(uid); }
  }));

  const rewritePanel = form.querySelector("#ai-rewrite-panel");
  const rewriteState = form.querySelector("#ai-rewrite-state");
  const rewriteResult = form.querySelector("#ai-rewrite-result");
  form.querySelector("#ai-review-rewrite").onclick = busy(async () => {
    const start = contentInput.selectionStart, end = contentInput.selectionEnd;
    const text = contentInput.value.slice(start, end);
    if (!text.trim()) throw Error("请先在正文编辑框中选中需要整理的段落。");
    if (Array.from(text).length > 4000) throw Error("一次最多整理 4,000 字，请缩小选中范围。");
    rewriteRequest?.abort(); rewriteSerial++;
    selection = { start, end, text, fullContent: contentInput.value };
    rewritePanel.hidden = false;
    form.querySelector("#ai-rewrite-original").textContent = text;
    rewriteResult.replaceChildren();
    rewriteState.textContent = "已选中段落。填写整理要求后生成替换建议。";
    rewritePanel.scrollIntoView({ block: "nearest", behavior: "auto" });
  });
  form.querySelector("#ai-rewrite-cancel").onclick = () => { rewriteSerial++; rewriteRequest?.abort(); rewritePanel.hidden = true; rewriteResult.replaceChildren(); };
  form.querySelector("#ai-rewrite-request").onclick = busy(async () => {
    if (!selection || !isActive()) return;
    const serial = ++rewriteSerial;
    rewriteRequest?.abort();
    rewriteRequest = new AbortController();
    const signal = rewriteRequest.signal;
    rewriteResult.replaceChildren();
    rewriteState.textContent = "先暂存当前审核稿，再准备模型并整理所选段落…";
    try {
      const saved = await controller.flush();
      if (!isActive() || serial !== rewriteSerial) return;
      if (contentInput.value !== selection.fullContent) throw Error("正文已经变化，请重新选中需要整理的段落。");
      const requested = { ...selection, revision: saved.revision };
      rewriteState.textContent = "模型正在整理，首次运行可能需要准备时间。你可以继续编辑或取消请求。";
      const result = await api(`/api/ai/drafts/${encodeURIComponent(uid)}/rewrite`, { method: "POST", signal, body: { revision: requested.revision, selection: requested.text, instruction: form.querySelector("#ai-rewrite-instruction").value.trim() } });
      if (!isActive() || serial !== rewriteSerial) return;
      if (typeof result.proposal !== "string" || result.original !== requested.text || result.revision !== requested.revision) throw Error("替换建议与所选版本不一致，请重新生成。");
      const current = contentInput.value === requested.fullContent && controller.revision === result.revision && !controller.dirty;
      rewriteState.textContent = current ? "替换建议已准备。请核对来源，再决定是否采用。" : "审核稿已变化，建议仅供查看。请重新选段生成后再采用。";
      rewriteResult.innerHTML = `<div class="ai-rewrite-result"><h4>建议替换为</h4><pre class="ai-source-text">${esc(result.proposal)}</pre>${aiArray(result.warnings).map(warning => `<p class="muted ai-helper">${esc(aiWarningText(warning))}</p>`).join("")}${aiArray(result.source_evidence).length ? `<details><summary>核对本次整理引用 · ${result.source_evidence.length} 处</summary>${result.source_evidence.map((citation, index) => `<div class="ai-evidence"><button type="button" class="small" data-ai-rewrite-locate="${index}">定位来源</button><blockquote>${esc(citation.quote || "")}</blockquote><span class="ai-source-meta">${esc(citation.source_uuid || "")}${citation.attachment_uuid ? " · 附件 " + esc(citation.attachment_uuid) : ""}${citation.page ? " · 第 " + esc(citation.page) + " 页" : ""}</span></div>`).join("")}</details>` : ""}<div class="actions"><button type="button" id="ai-rewrite-apply" data-revision="${esc(result.revision)}" class="primary" ${current ? "" : "disabled"}>采用这段建议</button><button type="button" id="ai-rewrite-discard">保留原段落</button></div></div>`;
      rewriteResult.querySelectorAll("[data-ai-rewrite-locate]").forEach(button => button.onclick = () => locateSource(result.source_evidence[Number(button.dataset.aiRewriteLocate)] || {}));
      rewriteResult.querySelector("#ai-rewrite-discard").onclick = () => { rewritePanel.hidden = true; rewriteResult.replaceChildren(); };
      rewriteResult.querySelector("#ai-rewrite-apply").onclick = busy(async () => {
        if (!isActive()) return;
        if (controller.dirty || controller.revision !== result.revision || contentInput.value !== requested.fullContent) throw Error("审核稿已变化，请重新生成这段建议。");
        const replaced = helpers.replaceSelection(contentInput.value, requested.start, requested.end, requested.text, result.proposal);
        if (replaced === null) throw Error("原选段已经变化，请重新选中段落。");
        contentInput.value = replaced;
        contentInput.dispatchEvent(new Event("input", { bubbles: true }));
        rewritePanel.hidden = true;
        contentInput.focus();
        contentInput.setSelectionRange(requested.start, requested.start + result.proposal.length);
        toast("已采用段落建议，可继续修改；尚未归档。");
      });
    } catch (error) {
      if (!isActive() || serial !== rewriteSerial || error.name === "AbortError") return;
      rewriteState.textContent = "段落整理未完成，审核稿保持原样。" + error.message;
    }
  });
}

async function AISettingsPage() {
  aiStopPolling();
  aiStopReview();
  AIWorkflowUI.dirty = false;
  $("#main").innerHTML = heading("AI 与模型设置", "选择本机或云端模型。所有自动整理结果都先进入待审核。", '<a class="ai-link-button" href="#ai-workflow">返回每日审核 →</a>') + '<div id="ai-settings-panel"></div>';
  return aiSettingsPanel($("#ai-settings-panel"));
}

async function aiSettingsPanel(container = null) {
  if (typeof container === "string") container = document.querySelector(container);
  if (!container) {
    container = document.createElement("div");
    container.id = "ai-settings-panel";
    $("#main").appendChild(container);
  }
  const settings = await api("/api/ai/settings");
  const provider = settings.provider || "ollama";
  const providers = [["ollama", "本机 Ollama"], ["openai_compatible", "兼容 OpenAI 的云端服务"], ["disabled", "暂不开启"]];
  if (!providers.some(([value]) => value === provider)) providers.push([provider, provider]);
  const hasKey = Boolean(settings.api_key_set || settings.api_key_configured || settings.has_api_key);
  container.innerHTML = `<div class="ai-settings-grid"><section class="panel"><h2>整理模型与每日安排</h2><form id="ai-settings-form"><div class="form-grid"><label>使用方式<select name="provider">${providers.map(([value, name]) => `<option value="${value}" ${value === provider ? "selected" : ""}>${name}</option>`).join("")}</select></label><label>模型名称<input name="model" value="${esc(settings.model || "qwen2.5:1.5b")}" list="ai-model-choices" placeholder="选择已安装模型或填写模型名称"><datalist id="ai-model-choices"></datalist></label><label class="full">服务地址<input name="base_url" value="${esc(settings.base_url || "http://127.0.0.1:11435")}" placeholder="本机或所选服务的 API 地址"></label><label class="full">云端 API Key（本机模型通常不需要）<input name="api_key" type="password" autocomplete="new-password" value="" placeholder="${hasKey ? "已保存密钥；留空保留，填写后替换" : "按服务要求填写，保存后不回显"}"></label><label class="ai-check full" id="ai-cloud-consent"><input type="checkbox" name="cloud_consent" ${settings.cloud_consent ? "checked" : ""}> 我同意将所选范围内的资料发送给此云端服务进行整理</label><label class="ai-check full"><input type="checkbox" name="schedule_enabled" ${settings.schedule_enabled ? "checked" : ""}> 每天定时生成整理草稿</label><label>每日整理时间<input type="time" name="daily_time" value="${esc(settings.daily_time || "21:00")}" required></label><p class="muted ai-helper">使用北京时间（UTC+8）。服务运行时执行，草稿等待你审核。</p><label class="ai-check full"><input type="checkbox" name="auto_after_save" ${settings.auto_after_save ? "checked" : ""}> 保存记录后自动安排整理（合并短时间内连续保存）</label><label class="ai-check full"><input type="checkbox" name="include_attachments" ${settings.include_attachments === false ? "" : "checked"}> 使用已识别的附件文字作为整理依据（原始附件保留）</label><label class="ai-check full"><input type="checkbox" name="sensitivityExclude" ${settings.sensitivityExclude === false ? "" : "checked"}> 整理时排除隐私等级 3 的敏感资料</label></div><div class="notice ai-setting-scope" id="ai-provider-note"></div><div class="actions"><button type="submit" class="primary">保存 AI 设置</button><button type="button" id="ai-model-list">刷新可用模型</button></div><p class="muted ai-helper" id="ai-settings-feedback">密钥不会回显；未修改密钥时可以留空。</p></form></section><div><section class="panel"><h2>本机模型</h2><p class="muted">已安装模型可从名称建议中选择。下载只在你点击下面按钮后开始。</p><label>准备下载的 Ollama 模型<input id="ai-pull-model" placeholder="填写准确模型名称" value="${esc(settings.model || "qwen2.5:1.5b")}"></label><div class="actions"><button id="ai-model-pull">下载到本机</button><button id="ai-jobs-refresh">查看下载进度</button></div><div id="ai-model-jobs" class="ai-job-list"></div></section><section class="panel"><h2>语义搜索模型</h2><p class="muted">首次准备需下载约 90 MB 本地模型权重。下载不上传你的记录；索引保存在本机，准备完成后可重建。</p><div class="actions"><button id="ai-semantic-prepare">准备本地语义模型</button><button id="ai-semantic-status">查看状态</button></div><div id="ai-semantic-feedback" class="ai-helper"></div><a href="#semantic-search">前往语义搜索 →</a></section></div></div>`;
  const form = container.querySelector("#ai-settings-form");
  function providerNote() {
    container.querySelector("#ai-cloud-consent").style.display = form.elements.provider.value === "openai_compatible" ? "flex" : "none";
    container.querySelector("#ai-provider-note").textContent = form.elements.provider.value === "ollama" ? "本机模型在你的电脑上整理资料。原文保持独立保存，每份草稿都需要你审核。" : form.elements.provider.value === "disabled" ? "暂停模型整理，核心记录与搜索功能继续可用。" : "使用云端服务时，所选范围内的资料会发送给该服务进行整理。隐私等级 2 的私人资料仍可能包含在范围内，请核对设置。";
  }
  form.elements.provider.onchange = providerNote;
  providerNote();
  form.onsubmit = busy(async event => {
    event.preventDefault();
    const fields = form.elements;
    const body = { provider: fields.provider.value, model: fields.model.value.trim(), base_url: fields.base_url.value.trim(), schedule_enabled: fields.schedule_enabled.checked, daily_time: fields.daily_time.value, auto_after_save: fields.auto_after_save.checked, sensitivityExclude: fields.sensitivityExclude.checked, include_attachments: fields.include_attachments.checked, cloud_consent: fields.cloud_consent.checked };
    if (fields.api_key.value.trim()) body.api_key = fields.api_key.value.trim();
    await api("/api/ai/settings", { method: "PUT", body });
    fields.api_key.value = "";
    container.querySelector("#ai-settings-feedback").textContent = "设置已保存。自动任务只生成草稿，不会自动确认归档。";
    toast("AI 设置已保存。");
  });
  container.querySelector("#ai-model-list").onclick = busy(async () => {
    const response = await api("/api/ai/models");
    const models = aiArray(response.models);
    container.querySelector("#ai-model-choices").innerHTML = models.map(model => `<option value="${esc(typeof model === "string" ? model : model.name)}"></option>`).join("");
    container.querySelector("#ai-settings-feedback").textContent = models.length ? `已找到 ${models.length} 个模型，可在模型名称中选择。` : "没有找到已安装模型。检查本机服务，或填写模型名称后下载。";
  });
  async function jobs() {
    const response = await api("/api/ai/jobs");
    const rows = aiArray(response.items || response.jobs || (Array.isArray(response) ? response : []));
    if (!container.isConnected) return false;
    container.querySelector("#ai-model-jobs").innerHTML = rows.slice(0, 10).map(job => `<div class="ai-job"><strong>${esc(job.model || job.title || job.kind || "模型任务")}</strong><span>${esc(AIWorkflowUI.labels[job.status] || job.status || "已提交")}</span>${job.error ? `<p class="danger">${esc(job.error)}</p>` : ""}${job.message ? `<p>${esc(job.message)}</p>` : ""}${job.progress ? `<p>${esc(typeof job.progress === "string" ? job.progress : JSON.stringify(job.progress))}</p>` : ""}</div>`).join("") || '<p class="muted">暂无模型任务。</p>';
    return rows.some(job => ["queued", "running"].includes(job.status));
  }
  let jobsTimer, preparationTimer;
  async function pollJobs() {
    clearTimeout(jobsTimer);
    if (container.isConnected && await jobs()) jobsTimer = setTimeout(() => pollJobs().catch(error => toast(error.message)), 3000);
  }
  container.querySelector("#ai-jobs-refresh").onclick = busy(jobs);
  container.querySelector("#ai-model-pull").onclick = busy(async () => {
    const model = container.querySelector("#ai-pull-model").value.trim();
    if (!model) throw Error("请填写准备下载的模型名称。");
    await api("/api/ai/model-pull", { method: "POST", body: { model } });
    toast("本机模型下载任务已提交，可查看进度。");
    await pollJobs();
  });
  async function semanticStatus() {
    const status = await api("/api/semantic/status");
    if (!container.isConnected) return false;
    const active = ["loading", "downloading"].includes(status.state) || aiArray(status.jobs).some(job => ["queued", "running"].includes(job.state || job.status));
    container.querySelector("#ai-semantic-feedback").textContent = `${active ? "正在准备模型 / 更新索引" : status.model_ready ? "模型已准备" : "模型尚未准备"} · 已索引 ${status.indexed_entries || 0} / ${status.total_entries || 0} 项。${status.last_error || ""}`;
    return active;
  }
  async function pollPreparation() {
    clearTimeout(preparationTimer);
    if (container.isConnected && await semanticStatus()) preparationTimer = setTimeout(() => pollPreparation().catch(error => toast(error.message)), 3000);
  }
  container.querySelector("#ai-semantic-status").onclick = busy(semanticStatus);
  container.querySelector("#ai-semantic-prepare").onclick = busy(async () => {
    await api("/api/semantic/prepare", { method: "POST", body: {} });
    toast("本地语义模型准备任务已提交。");
    await pollPreparation();
  });
  const initialStates = await Promise.allSettled([pollJobs(), pollPreparation()]);
  initialStates.filter(state => state.status === "rejected").forEach(state => toast(state.reason.message));
  return settings;
}

async function semanticSearchPage(query = null) {
  aiStopPolling();
  aiStopReview();
  AIWorkflowUI.dirty = false;
  const parameters = new URLSearchParams(location.hash.split("?")[1] || "");
  const searchQuery = query ?? parameters.get("q") ?? "";
  const includeSensitive = parameters.get("include_sensitive") === "true";
  const status = await api("/api/semantic/status");
  const result = searchQuery.trim() ? await api("/api/semantic/search?" + new URLSearchParams({ q: searchQuery, include_sensitive: includeSensitive, limit: 20 })) : { items: [], warnings: [], total: 0 };
  const semantic = result.retrieval_method === "semantic";
  $("#main").innerHTML = heading("语义搜索", "用你的问题找回相关记录、旧案例与附件内容。", '<a class="ai-link-button" href="#ai-settings">模型设置 →</a>') +
    `<section class="panel"><form id="ai-semantic-search-form" class="ai-semantic-form"><label class="full">我想找…<input name="q" value="${esc(searchQuery)}" placeholder="例如：我以前遇到过类似问题吗？当时怎样处理？" required></label><label class="ai-check"><input type="checkbox" name="include_sensitive" ${includeSensitive ? "checked" : ""}> 包含隐私等级 3 的敏感资料</label><button class="primary">搜索</button></form><div class="ai-index-strip"><span>${status.model_ready ? "本地语义模型已准备" : "本地语义模型尚未准备"} · 已索引 ${Number(status.indexed_entries || 0)} / ${Number(status.total_entries || 0)} 项</span><button id="ai-semantic-index" class="small">重建本地索引</button><button id="ai-semantic-refresh" class="small">刷新状态</button></div>${status.last_error ? `<p class="danger ai-error">${esc(status.last_error)}</p>` : ""}</section>` +
    aiArray(result.warnings).map(warning => `<div class="notice">${esc(aiWarningText(warning))}</div>`).join("") +
    (searchQuery.trim() ? `<section class="panel"><div class="panel-top"><h2>${semantic ? "语义相关资料" : "关键词匹配资料"} <span class="pill">${aiArray(result.items).length} 项</span></h2><span class="muted">${semantic ? "本地语义检索" : "当前采用关键词检索"}</span></div>${aiArray(result.items).length ? result.items.map(item => `<article class="ai-semantic-result"><div class="meta"><span class="tag">${esc(kinds[item.item_type] || item.item_type || "资料")}</span><span>${esc(item.date || "")}</span>${semantic && Number.isFinite(Number(item.score)) ? `<span>相似度 ${Number(item.score).toFixed(3)}</span>` : ""}${item.page ? `<span>附件第 ${esc(item.page)} 页</span>` : ""}</div><h3><a href="#item/${esc(item.uuid)}">${esc(item.title)}</a></h3><p>${esc(item.excerpt || "")}</p><div class="meta"><span>依据字段：${esc(item.field || "正文")}</span><span>UUID：${esc(item.uuid)}</span>${item.attachment_uuid ? `<button class="small" data-ai-extraction="${esc(item.attachment_uuid)}">查看附件提取内容</button>` : ""}</div></article>`).join("") : '<div class="empty">没有找到相关资料。可以换一种表达，或先准备模型并重建索引。</div>'}<p class="muted ai-helper">最多显示 20 项候选。相似度帮助定位资料，结论仍请回到原文核实。</p></section>` : '<div class="empty"><span class="glyph">⌕</span>输入一段问题或场景，查找与你想表达的意思相关的资料。</div>');
  $("#ai-semantic-search-form").onsubmit = event => {
    event.preventDefault();
    const form = event.currentTarget;
    location.hash = aiRoute("semantic-search", { q: form.elements.q.value, include_sensitive: form.elements.include_sensitive.checked });
  };
  $("#ai-semantic-index").onclick = busy(async () => { await api("/api/semantic/index", { method: "POST", body: { scope: "all" } }); toast("本地索引任务已提交。"); await semanticSearchPage(searchQuery); });
  $("#ai-semantic-refresh").onclick = busy(() => semanticSearchPage(searchQuery));
  $("#main").querySelectorAll("[data-ai-extraction]").forEach(button => button.onclick = busy(() => attachmentExtractionUI(button.dataset.aiExtraction)));
  if (["loading", "downloading"].includes(status.state) || aiArray(status.jobs).some(job => ["queued", "running"].includes(job.state || job.status))) {
    AIWorkflowUI.pollTimer = setTimeout(() => {
      if (location.hash.startsWith("#semantic-search")) semanticSearchPage(searchQuery).catch(error => toast(error.message));
    }, 4000);
  }
}

async function attachmentExtractionUI(attachmentUid, existingContainer = null) {
  let container = typeof existingContainer === "string" ? document.querySelector(existingContainer) : existingContainer;
  let inDialog = false;
  if (!container) {
    modal("附件文字提取 / OCR", '<div id="ai-extraction-content"></div>');
    container = $("#ai-extraction-content");
    inDialog = true;
  }
  let timer;
  const endpoint = "/api/attachments/" + encodeURIComponent(attachmentUid) + "/extraction";
  async function render() {
    clearTimeout(timer);
    const result = await api(endpoint);
    if (!container.isConnected) return;
    const status = result.status || "not_started";
    const active = ["queued", "running", "processing"].includes(status);
    const labels = { not_started: "尚未提取", none: "尚未提取", queued: "等待提取", running: "正在提取", pending: "尚未提取", processing: "正在提取", completed: "提取完成", partial: "已提取部分内容", done: "提取完成", ready: "提取完成", failed: "提取失败", timeout: "提取超时", stale: "原文件已变化", unsupported: "格式暂不支持" };
    container.innerHTML = `<div class="ai-extraction-head"><span class="pill">${esc(labels[status] || status)}</span><button data-ai-extract class="small" ${active ? "disabled" : ""}>${result.text ? "重新提取文字" : "提取文字 / OCR"}</button><button data-ai-extract-refresh class="small">刷新</button><a href="/api/attachments/${encodeURIComponent(attachmentUid)}">下载原始附件</a></div><p class="muted ai-helper">提取文字是可重新生成的资料，原始附件保留。OCR 可能识别有误，请对照原文件与页码核实。</p>${result.error ? `<p class="danger ai-error">${esc(result.error)}</p>` : ""}${result.truncated ? '<div class="notice">该附件只提取了部分内容，未包含的部分请查看原文件。</div>' : ""}${aiArray(result.warnings).map(warning => `<div class="notice">${esc(aiWarningText(warning))}</div>`).join("")}${aiArray(result.pages).length ? aiArray(result.pages).map((page, index) => `<details class="ai-extraction-page" ${index === 0 ? "open" : ""}><summary>${esc(aiPageLabel(page, index))}${page.tool || page.method ? " · " + esc(page.tool || page.method) : ""}</summary><pre class="ai-source-text">${esc(aiPageText(page, result.text || ""))}</pre></details>`).join("") : result.text ? `<pre class="ai-source-text">${esc(result.text)}</pre>` : `<div class="empty">${active ? "正在处理附件，可稍后查看。" : "点击提取文字，处理结果将保存为可检索资料。"}</div>`}`;
    container.querySelector("[data-ai-extract]").onclick = busy(async () => {
      container.querySelector("[data-ai-extract]").textContent = "正在提取…";
      const processed = await api(endpoint, { method: "POST", body: { force: true } });
      toast(["completed", "partial"].includes(processed.status) ? "附件文字已提取，原始文件保留。" : "附件提取状态已更新。");
      await render();
    });
    container.querySelector("[data-ai-extract-refresh]").onclick = busy(render);
    if (active) timer = setTimeout(() => { if (container.isConnected && (!inDialog || $("#dialog").open)) render().catch(error => toast(error.message)); }, 3000);
  }
  await render();
  return container;
}
