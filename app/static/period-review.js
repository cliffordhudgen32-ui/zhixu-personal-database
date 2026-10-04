"use strict";

(function () {
  let serial = 0;
  const periodNames = { week: "周复盘", month: "月复盘" };
  function dateString(value) { return [value.getFullYear(), String(value.getMonth() + 1).padStart(2, "0"), String(value.getDate()).padStart(2, "0")].join("-"); }
  function rangeLabel(kind, selectedDate) {
    const selected = new Date(selectedDate + "T12:00:00");
    if (!Number.isFinite(selected.getTime())) return "请先选择有效日期";
    let start, end;
    if (kind === "month") {
      start = new Date(selected.getFullYear(), selected.getMonth(), 1, 12);
      end = new Date(selected.getFullYear(), selected.getMonth() + 1, 0, 12);
    } else {
      start = new Date(selected);
      start.setDate(start.getDate() - (start.getDay() + 6) % 7);
      end = new Date(start);
      end.setDate(end.getDate() + 6);
    }
    return `${dateString(start)} 至 ${dateString(end)}`;
  }
  window.addEventListener("hashchange", () => { serial++; });
  window.periodReviewPage = async function () {
    aiStopPolling();
    aiStopReview();
    const ownSerial = ++serial;
    const params = new URLSearchParams(location.hash.split("?")[1] || "");
    const kind = params.get("kind") === "month" ? "month" : "week";
    const status = params.get("status") || "";
    const page = Math.max(1, Number(params.get("page")) || 1);
    const chosenDate = /^\d{4}-\d{2}-\d{2}$/.test(params.get("date") || "") ? params.get("date") : today();
    const request = new URLSearchParams({ scope_kind: kind, page, size: 20 });
    if (status) request.set("status", status);
    const [settings, result] = await Promise.all([api("/api/ai/settings"), api("/api/ai/drafts?" + request)]);
    if (ownSerial !== serial || !location.hash.startsWith("#period-review")) return;
    const rows = aiArray(result.items), total = Number(result.total || 0), size = Number(result.size || 20);
    $("#main").innerHTML = heading("周月复盘", "把已保存的记录汇总成复盘草稿，核对后再确认归档。", '<a class="ai-link-button" href="#ai-workflow">每日整理与审核 →</a>') +
      `<div class="notice">从完成事项、未解决问题、重复困难与可复用经验中提炼复盘。AI 可能提出后续行动建议，由你判断和修改；本页不会自动创建待办。</div><div class="period-review-grid"><section class="panel"><h2>生成一份复盘草稿</h2><form id="period-generate-form"><div class="form-grid"><label>复盘类型<select name="kind"><option value="week" ${kind === "week" ? "selected" : ""}>周复盘 · 周一至周日</option><option value="month" ${kind === "month" ? "selected" : ""}>月复盘 · 自然月</option></select></label><label>选择周期内任意一天<input type="date" name="date" value="${esc(chosenDate)}" required></label></div><p class="period-range" id="period-range">${esc(rangeLabel(kind, chosenDate))}</p><p class="muted ai-helper">汇总所选日期所在的完整自然周或月。当前周期尚未结束时，只能整理目前已经保存的记录。草稿会列出来源和待核实问题。</p><button type="submit" class="primary" id="period-generate-button">生成复盘草稿</button><p class="muted ai-helper" id="period-generate-state" role="status" aria-live="polite">生成后进入人工审核，不会自动归档。</p></form></section><section class="panel"><h2>自动安排复盘</h2><form id="period-schedule-form"><label class="ai-check"><input type="checkbox" name="weekly_enabled" ${settings.weekly_enabled ? "checked" : ""}> 自动生成上一完整周的复盘草稿</label><label class="ai-check"><input type="checkbox" name="monthly_enabled" ${settings.monthly_enabled ? "checked" : ""}> 自动生成上一完整月的复盘草稿</label><p class="muted ai-helper">服务运行时，在每日设定的整理时间 ${esc(settings.daily_time || "21:00")}（北京时间）后检查上一完整周期。同一周期的已有草稿不会重复生成。开启后的首次检查可能补生成上一周期。</p><p class="muted ai-helper">两项默认关闭。自动生成的结果也需要你核对并确认归档。</p><div class="actions"><button type="submit" id="period-schedule-save">保存复盘安排</button><a href="#ai-settings">模型与整理时间设置 →</a></div><p class="muted ai-helper" id="period-schedule-state" role="status" aria-live="polite">修改后点击保存，其他 AI 设置继续保留。</p></form></section></div>` +
      `<section class="panel"><div class="panel-top"><h2>已有复盘草稿 <span class="pill">${total} 份</span></h2></div><form id="period-filter-form" class="ai-filter"><label>范围类型<select name="kind"><option value="week" ${kind === "week" ? "selected" : ""}>周复盘</option><option value="month" ${kind === "month" ? "selected" : ""}>月复盘</option></select></label><label>状态<select name="status"><option value="">全部状态</option>${Object.entries(AIWorkflowUI.labels).map(([value, name]) => `<option value="${esc(value)}" ${value === status ? "selected" : ""}>${esc(name)}</option>`).join("")}</select></label><button type="submit">查看</button><button type="button" id="period-refresh">刷新状态</button></form><div class="ai-draft-list period-draft-list">${rows.length ? rows.map(row => `<article class="ai-draft-card"><div><div class="meta">${aiStatusPill(row.status)}<span>${esc(periodNames[kind])}</span><span>周期末 ${esc(row.date || "")}</span></div><h2><a href="#ai-draft/${esc(row.uuid)}">${esc(row.title || periodNames[kind] + "草稿")}</a></h2><p>${esc((row.summary || row.content || "整理完成后可查看与修改复盘草稿。").slice(0, 220))}</p>${row.error ? `<p class="danger ai-error">${esc(row.error)}</p>` : ""}${row.archived_uuid ? `<a href="#item/${esc(row.archived_uuid)}">查看已归档复盘 →</a>` : ""}</div><a class="ai-link-button" href="#ai-draft/${esc(row.uuid)}">${row.status === "pending_review" ? "开始审核" : "查看草稿"} →</a></article>`).join("") : `<div class="empty">暂无${esc(periodNames[kind])}草稿。可以选一个周期生成，再核对来源与内容。</div>`}</div><div class="pagination"><button id="period-previous" ${page <= 1 ? "disabled" : ""}>上一页</button><span>第 ${page} 页 · 共 ${total} 份</span><button id="period-next" ${page * size >= total ? "disabled" : ""}>下一页</button></div></section>`;
    const main = $("#main"), generate = main.querySelector("#period-generate-form"), schedule = main.querySelector("#period-schedule-form"), filter = main.querySelector("#period-filter-form");
    const isActive = () => ownSerial === serial && generate.isConnected && location.hash.startsWith("#period-review");
    const dirtyControls = { generate: false, schedule: false, filter: false };
    let requestActive = false, savingSchedule = false;
    generate.addEventListener("input", () => {
      dirtyControls.generate = true;
      main.querySelector("#period-range").textContent = rangeLabel(generate.elements.kind.value, generate.elements.date.value);
    });
    schedule.addEventListener("input", () => { dirtyControls.schedule = true; main.querySelector("#period-schedule-state").textContent = "安排尚未保存，请点击保存。"; });
    filter.addEventListener("input", () => { dirtyControls.filter = true; });
    generate.onsubmit = busy(async event => {
      event.preventDefault();
      if (requestActive || !generate.reportValidity()) return;
      requestActive = true;
      const button = main.querySelector("#period-generate-button");
      button.disabled = true;
      main.querySelector("#period-generate-state").textContent = "正在提交复盘任务…";
      try {
        const draft = aiDraftValue(await api("/api/ai/period", { method: "POST", body: { kind: generate.elements.kind.value, date: generate.elements.date.value } }));
        if (!isActive()) return;
        toast("复盘任务已提交，结果等待你审核。");
        if (draft.uuid || draft.draft_uuid) location.hash = "ai-draft/" + (draft.uuid || draft.draft_uuid);
        else await window.periodReviewPage();
      } catch (error) {
        if (isActive()) main.querySelector("#period-generate-state").textContent = "任务未提交成功：" + error.message;
        throw error;
      } finally { requestActive = false; if (isActive()) button.disabled = false; }
    });
    schedule.onsubmit = busy(async event => {
      event.preventDefault();
      if (savingSchedule) return;
      savingSchedule = true;
      const button = main.querySelector("#period-schedule-save");
      const sent = { weekly_enabled: schedule.elements.weekly_enabled.checked, monthly_enabled: schedule.elements.monthly_enabled.checked };
      button.disabled = true;
      main.querySelector("#period-schedule-state").textContent = "正在保存复盘安排…";
      try {
        await api("/api/ai/settings", { method: "PUT", body: sent });
        if (!isActive()) return;
        const changedAgain = sent.weekly_enabled !== schedule.elements.weekly_enabled.checked || sent.monthly_enabled !== schedule.elements.monthly_enabled.checked;
        dirtyControls.schedule = changedAgain;
        main.querySelector("#period-schedule-state").textContent = changedAgain ? "刚才的安排已保存；你又修改了选项，请再次保存。" : "复盘安排已保存。所有草稿仍需人工审核。";
      } catch (error) {
        if (isActive()) main.querySelector("#period-schedule-state").textContent = "安排未保存：" + error.message;
        throw error;
      } finally { savingSchedule = false; if (isActive()) button.disabled = false; }
    });
    function listRoute(targetPage) { return aiRoute("period-review", { kind: filter.elements.kind.value, status: filter.elements.status.value, page: targetPage, date: generate.elements.date.value }); }
    filter.onsubmit = event => { event.preventDefault(); location.hash = listRoute(1); };
    main.querySelector("#period-previous").onclick = () => location.hash = listRoute(page - 1);
    main.querySelector("#period-next").onclick = () => location.hash = listRoute(page + 1);
    main.querySelector("#period-refresh").onclick = busy(() => window.periodReviewPage());
    if (rows.some(row => ["queued", "running"].includes(row.status))) {
      function poll() {
        if (!isActive()) return;
        if (Object.values(dirtyControls).some(Boolean) || requestActive || savingSchedule || document.activeElement?.closest("#period-generate-form, #period-schedule-form, #period-filter-form")) {
          AIWorkflowUI.pollTimer = setTimeout(poll, 6000);
          return;
        }
        window.periodReviewPage().catch(error => toast(error.message));
      }
      AIWorkflowUI.pollTimer = setTimeout(poll, 6000);
    }
  };
})();
