"use strict";

(() => {
  const state = { question: "", start: "", end: "", project: "", dispose: null };
  const uuidPattern = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
  const list = value => Array.isArray(value) ? value : value ? [value] : [];
  const sourcesOf = data => Array.isArray(data?.sources) ? data.sources : [];

  function sourceCard(source, index) {
    const identifier = String(source.id ?? index + 1);
    const entryLink = uuidPattern.test(String(source.uuid || ""))
      ? `<a href="#item/${encodeURIComponent(source.uuid)}">查看原始记录 →</a>` : "";
    const attachmentLink = uuidPattern.test(String(source.attachment_uuid || ""))
      ? `<a href="/api/attachments/${encodeURIComponent(source.attachment_uuid)}" target="_blank" rel="noopener">打开原始附件 ↗</a>` : "";
    const pageLabel = Number.isInteger(source.page) && source.page > 0 ? ` · 第 ${source.page} 页` : "";
    return `<article class="knowledge-source-card" data-source-card="${index}" tabindex="-1" aria-labelledby="knowledge-source-title-${index}">
      <div class="knowledge-source-title"><span class="knowledge-source-badge">${esc(identifier)}</span><h3 id="knowledge-source-title-${index}">${esc(source.title || "未命名记录")}</h3></div>
      <p class="knowledge-source-meta">${esc(source.date || "")} ${esc(source.source_label || "记录片段")}${esc(pageLabel)}</p>
      <details><summary>查看检索原文片段</summary><div class="knowledge-source-text">${esc(source.text || "该片段没有可显示的正文。")}</div></details>
      <div class="knowledge-source-links">${entryLink}${attachmentLink}</div>
    </article>`;
  }

  function resultHTML(data) {
    const sources = sourcesOf(data);
    const sourceIndices = new Map(sources.map((source, index) => [String(source.id), index]));
    const statements = Array.isArray(data?.statements) ? data.statements : [];
    const answerable = data?.answerable === true;
    const warnings = list(data?.warnings).filter(Boolean);
    const missing = list(data?.missing_information).filter(Boolean);
    const method = ({ semantic: "语义检索", keyword: "关键词检索", hybrid: "语义与关键词检索" })[data?.retrieval_method] || "本地资料检索";
    const statementHTML = statements.map((statement, index) => {
      const citations = Array.isArray(statement.citations) ? statement.citations : [];
      const evidence = citations.map(citation => {
        const sourceIndex = sourceIndices.get(String(citation.source_id));
        const source = sources[sourceIndex];
        const reference = source
          ? `<button type="button" class="small knowledge-citation" data-source-index="${sourceIndex}" aria-label="定位来源 ${esc(source.id)}：${esc(source.title || "未命名记录")}">${esc(source.id)} · ${esc(source.title || "查看来源")}</button>`
          : '<span class="muted">引用来源当前不可用</span>';
        return `<div class="knowledge-evidence">${reference}<blockquote>${esc(citation.quote || "")}</blockquote></div>`;
      }).join("");
      return `<article class="knowledge-statement"><div class="knowledge-statement-heading"><span>${index + 1}</span><p>${esc(statement.text || "")}</p></div>${evidence}</article>`;
    }).join("");
    const answer = answerable && statements.length
      ? statementHTML
      : `<div class="knowledge-answer-text">${esc(data?.answer || "资料不足，暂时无法根据现有记录回答。")}</div>`;
    const missingHTML = missing.length
      ? `<div class="knowledge-missing"><strong>还需要补充或核实</strong><ul>${missing.map(item => `<li>${esc(item)}</li>`).join("")}</ul></div>` : "";
    const warningHTML = warnings.length
      ? `<details class="knowledge-answer-scope"><summary>本次回答的资料范围与提示</summary><ul>${warnings.map(item => `<li>${esc(item)}</li>`).join("")}</ul></details>` : "";
    return {
      answer: `<section class="panel knowledge-answer-result"><div class="knowledge-result-heading"><h2>${answerable ? "根据资料得到的回答" : "资料还不足以回答"}</h2><span class="pill">${esc(method)}</span></div>${answer}${missingHTML}${warningHTML}<p class="knowledge-answer-footnote">点击引用可核对原文。回答仅展示在本页。</p></section>`,
      sources: `<section class="panel knowledge-answer-sources"><h2>参考资料 <span class="muted">${sources.length}</span></h2>${sources.length ? sources.map(sourceCard).join("") : '<p class="muted">没有检索到可引用的相关片段。可以调整问法、日期范围，或先补充原始记录。</p>'}</section>`,
    };
  }

  window.knowledgeAnswerPage = function knowledgeAnswerPage() {
    state.dispose?.();
    const serial = typeof routeSerial === "number" ? routeSerial : null;
    const host = document.querySelector("#main");
    const projects = typeof refs === "object" && Array.isArray(refs.projects) ? refs.projects : [];
    host.innerHTML = heading("问我的资料", "向自己的记录提问，由本机模型回答，并逐条附上原文依据。", '<a class="ai-link-button" href="#ai-settings">本机模型设置 →</a>') +
      `<div class="knowledge-answer-layout" id="knowledge-answer-page">
        <div class="knowledge-answer-primary">
          <section class="panel knowledge-question-panel"><h2>这次想找回什么经验？</h2>
            <form id="knowledge-question-form">
              <label>你的问题<textarea name="question" maxlength="1000" rows="4" required placeholder="例如：之前类似的设备故障是怎么处理的？">${esc(state.question)}</textarea></label>
              <div class="knowledge-question-examples"><button type="button" class="small" data-question-example="之前类似的设备故障是怎么处理的？">找处理经验</button><button type="button" class="small" data-question-example="这个项目有哪些还没有解决的问题？">找未解决问题</button></div>
              <details class="knowledge-question-filters"><summary>缩小资料范围（可选）</summary><div class="knowledge-filter-fields">
                <label>起始日期<input type="date" name="start" value="${esc(state.start)}"></label>
                <label>结束日期<input type="date" name="end" value="${esc(state.end)}"></label>
                <label>项目<select name="project"><option value="">全部项目</option>${projects.map(project => `<option value="${esc(project.uuid)}"${project.uuid === state.project ? " selected" : ""}>${esc(project.name || project.title || "未命名项目")}</option>`).join("")}</select></label>
              </div></details>
              <div class="knowledge-question-actions"><button type="submit" class="primary" id="knowledge-ask">根据资料回答</button><span class="muted">Ctrl + Enter 提问</span></div>
              <p id="knowledge-answer-status" class="knowledge-answer-status" role="status" aria-live="polite">先找相关资料，再生成带引用的回答。</p>
            </form>
          </section>
          <div id="knowledge-answer-result"><section class="panel knowledge-answer-intro"><h2>让已有记录帮你少走弯路</h2><p>可以找处理方法、回顾项目经过，或比较过去的做法。问题越具体，越容易找到相关原文。</p><p class="muted">资料不足时会明确说明。生成的回答供你核对与参考。</p></section></div>
        </div>
        <div id="knowledge-answer-sources"><section class="panel"><h2>回答依据</h2><p class="muted">提问后，这里会列出引用记录和附件片段。每条引用都可以跳到对应来源。</p></section></div>
      </div>`;
    const root = host.querySelector("#knowledge-answer-page");
    const form = root.querySelector("#knowledge-question-form");
    const status = root.querySelector("#knowledge-answer-status");
    const button = root.querySelector("#knowledge-ask");
    const resultNode = root.querySelector("#knowledge-answer-result");
    const sourcesNode = root.querySelector("#knowledge-answer-sources");
    let request = null;
    let timedOut = false;
    let disposed = false;
    let timeout = null;
    const isActive = () => !disposed && root.isConnected && host.contains(root)
      && (serial === null || serial === routeSerial)
      && (location.hash.slice(1).split(/[/?]/)[0] || "dashboard") === "knowledge-answer";

    function remember() {
      for (const key of ["question", "start", "end", "project"]) state[key] = form.elements[key].value;
    }
    function dispose() {
      if (disposed) return;
      remember();
      disposed = true;
      request?.abort();
      clearTimeout(timeout);
      window.removeEventListener("hashchange", onHashChange);
      if (state.dispose === dispose) state.dispose = null;
    }
    function onHashChange() {
      if (!isActive()) dispose();
    }
    state.dispose = dispose;
    window.addEventListener("hashchange", onHashChange);
    form.addEventListener("input", remember);
    form.addEventListener("change", remember);
    form.elements.question.addEventListener("keydown", event => {
      if (event.ctrlKey && event.key === "Enter") {
        event.preventDefault();
        form.requestSubmit();
      }
    });
    root.querySelectorAll("[data-question-example]").forEach(example => {
      example.onclick = () => {
        form.elements.question.value = example.dataset.questionExample;
        remember();
        form.elements.question.focus();
      };
    });

    function errorMessage(message) {
      status.classList.add("knowledge-answer-error");
      status.textContent = message + " 问题与筛选条件已保留，可以重试。";
    }

    form.onsubmit = async event => {
      event.preventDefault();
      if (!isActive() || request) return;
      remember();
      const question = state.question.trim();
      if (!question || question.length > 1000) {
        errorMessage("请输入 1 至 1000 字的问题。");
        form.elements.question.focus();
        return;
      }
      if (state.start && state.end && state.start > state.end) {
        errorMessage("起始日期不能晚于结束日期。");
        form.elements.start.focus();
        return;
      }
      const payload = { question };
      for (const key of ["start", "end", "project"]) if (state[key]) payload[key] = state[key];
      const submittedQuestion = question;
      request = new AbortController();
      timedOut = false;
      timeout = setTimeout(() => { timedOut = true; request?.abort(); }, 180000);
      button.disabled = true;
      root.querySelectorAll("[data-question-example]").forEach(example => example.disabled = true);
      status.classList.remove("knowledge-answer-error");
      status.textContent = "正在查找相关资料并由本机模型回答…";
      resultNode.innerHTML = '<section class="panel knowledge-answer-waiting" aria-busy="true"><p>正在核对相关原文与引用依据，请稍候。</p></section>';
      sourcesNode.innerHTML = '<section class="panel"><h2>回答依据</h2><p class="muted">检索与回答完成后显示来源。</p></section>';
      try {
        const data = await api("/api/knowledge/answer", { method: "POST", body: payload, signal: request.signal });
        if (!isActive()) return;
        if (!data || typeof data.answerable !== "boolean" || !Array.isArray(data.sources)) throw Error("服务器未返回可核对的回答。");
        const rendered = resultHTML(data);
        resultNode.innerHTML = `<p class="knowledge-submitted-question">本次问题：${esc(submittedQuestion)}</p>` + rendered.answer;
        sourcesNode.innerHTML = rendered.sources;
        status.textContent = data.answerable ? "回答已完成。点击引用可以核对原文。" : "现有资料不足，已展示检索范围与可补充的信息。";
        resultNode.querySelectorAll("[data-source-index]").forEach(citation => {
          citation.onclick = () => {
            const index = Number(citation.dataset.sourceIndex);
            const source = sourcesNode.querySelector(`[data-source-card="${index}"]`);
            if (!source) return;
            const details = source.querySelector("details");
            if (details) details.open = true;
            source.scrollIntoView({ block: "center", behavior: "auto" });
            source.focus({ preventScroll: true });
          };
        });
      } catch (error) {
        if (!isActive()) return;
        errorMessage(timedOut ? "回答等待超时，请稍后重试。" : error.message || "回答未完成。");
        resultNode.innerHTML = '<section class="panel"><h2>本次回答未完成</h2><p class="muted">问题仍保留在上方。可以缩小资料范围或检查本机模型设置后重新提问。</p><button type="button" id="knowledge-answer-retry">重试这条问题</button></section>';
        resultNode.querySelector("#knowledge-answer-retry").onclick = () => form.requestSubmit();
      } finally {
        clearTimeout(timeout);
        request = null;
        if (isActive()) {
          button.disabled = false;
          root.querySelectorAll("[data-question-example]").forEach(example => example.disabled = false);
        }
      }
    };
    if (state.start || state.end || state.project) root.querySelector(".knowledge-question-filters").open = true;
    form.elements.question.focus();
  };

  // Pure rendering helper also makes source escaping reviewable without a model run.
  window.KnowledgeAnswerUI = Object.freeze({ resultHTML });
})();
