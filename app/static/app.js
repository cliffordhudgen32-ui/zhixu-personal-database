"use strict";
const $ = (s) => document.querySelector(s),
  $$ = (s) => [...document.querySelectorAll(s)];
const esc = (v) =>
  String(v ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const today = () => {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
};
const kinds = {
  entries: "全部记录",
  inbox: "收集箱",
  knowledge: "知识库",
  cases: "案例库",
  problems: "问题库",
  solutions: "解决方案",
  experiences: "经验库",
  sources: "来源资料",
  learning: "学习记录",
  projects: "项目档案",
  people: "人物",
  tasks: "待办任务",
  events: "重要事件",
  conversations: "AI 对话",
  domains: "领域",
  topics: "主题",
  reviews: "复盘",
  tags: "标签",
  "custom-fields": "自定义字段",
  "knowledge-answer": "问我的资料",
  "period-review": "周月复盘",
  "speech-capture": "语音随手记",
  quick: "随手记与草稿",
};
const assets = [
  "knowledge",
  "cases",
  "problems",
  "solutions",
  "experiences",
  "sources",
  "learning",
];
const relationLabels = {
  related_to: "相关资料",
  prerequisite_of: "前置知识",
  supplements: "补充",
  similar_to: "相似",
  conflicts_with: "观点冲突",
  case_of: "案例",
  solution_for: "解决方案",
  supersedes: "升级版本",
  derived_from: "来源于",
};
const categories = [
  "工作记录",
  "生活记录",
  "项目记录",
  "学习记录",
  "问题记录",
  "解决方案",
  "会议记录",
  "人物记录",
  "灵感",
  "决策",
  "财务记录",
  "健康/健身记录",
  "旅行记录",
  "AI 对话记录",
  "其他",
];
const icons = {
  dashboard: "◫",
  inbox: "⌑",
  entries: "▤",
  knowledge: "▥",
  cases: "▣",
  problems: "◇",
  solutions: "✓",
  experiences: "✦",
  sources: "↗",
  learning: "▧",
  projects: "▱",
  people: "♧",
  tasks: "☑",
  events: "◷",
  timeline: "◴",
  calendar: "▦",
  map: "⌘",
  stats: "▥",
  export: "⇧",
  import: "⇩",
  settings: "⚙",
  favorite: "☆",
  trash: "♲",
  review: "↻",
  conversations: "✧",
};
let token = "",
  cfg = {},
  refs = {},
  currentFilters = {},
  currentListKind = "entries",
  page = 1,
  routeSerial = 0;
const commonFields = [
  "uuid",
  "date",
  "title",
  "content",
  "summary",
  "category",
  "sub_category",
  "importance",
  "status",
  "source",
  "location_text",
  "notes",
  "privacy_level",
  "is_favorite",
  "is_archived",
  "metadata_json",
  "tags",
  "projects",
  "people",
  "item_type",
  "origin",
  "content_nature",
  "normalized_content",
  "ai_summary",
  "generated_by_ai",
  "ai_provider",
  "ai_model",
  "generated_at",
];
const detailLabels = {
  domain_id: "领域",
  topic_id: "主题",
  knowledge_type: "知识类型",
  difficulty: "难度（1–5）",
  source_type: "来源类型",
  source_url: "来源网址",
  source_title: "来源标题",
  author: "作者",
  published_date: "发布日期",
  learned_date: "学习日期",
  confidence: "可信度",
  maturity_level: "成熟度",
  next_review_date: "下次复习日期",
  needs_verification: "需要再次验证",
  case_type: "案例类型",
  background: "背景 · 当时发生了什么",
  problem: "问题 · 真正需要解决什么",
  goal: "目标",
  constraints: "已知条件与限制",
  analysis: "分析过程",
  solution: "采用的方案",
  execution: "执行过程",
  result: "最终结果",
  outcome: "成功 / 失败",
  lessons: "教训",
  mistakes: "错误判断",
  success_factors: "成功原因",
  failure_factors: "失败原因",
  reusable_method: "可复用经验",
  applicable_conditions: "适用条件",
  not_applicable_conditions: "不适用条件",
  future_improvements: "后续改进",
  start_date: "开始日期",
  end_date: "结束日期",
  problem_type: "问题类型",
  description: "说明",
  steps: "操作步骤",
  requirements: "前置要求",
  advantages: "优点",
  disadvantages: "不足",
  risks: "风险",
  cost: "成本",
  success_rate_note: "成功率说明",
  verification_status: "验证状态",
  occurred_date: "发生日期",
  impact: "影响",
  possible_causes: "可能原因",
  root_cause: "根本原因",
  final_solution: "最终解决方案",
  is_resolved: "已解决",
  recurrence_count: "再次出现次数",
  last_recurred_date: "最近再次发生日期",
  context: "适用背景",
  source_case_uuid: "来源案例 UUID",
  times_verified: "验证次数",
  last_verified_at: "最近验证时间（ISO 8601）",
  organization: "机构 / 网站",
  url: "网址",
  publication_date: "出版日期",
  access_date: "收藏日期",
  file_id: "来源附件内部 ID",
  learning_type: "学习类型",
  source: "来源说明",
  finish_date: "完成日期",
  progress: "学习进度（0–100）",
  notes: "笔记",
  key_points: "核心要点",
  questions: "待解决疑问",
  application: "如何实践",
  review_date: "复习日期",
  times_used: "复用次数",
  last_used_at: "最近复用",
  reuse_score: "复用分数",
  review_count: "复习次数",
  last_reviewed_at: "最近复习",
  is_reviewed: "已复盘",
};
const detailFields = {
  knowledge: [
    "domain_id",
    "topic_id",
    "knowledge_type",
    "difficulty",
    "maturity_level",
    "confidence",
    "source_type",
    "source_url",
    "source_title",
    "author",
    "published_date",
    "learned_date",
    "next_review_date",
    "needs_verification",
  ],
  cases: [
    "domain_id",
    "topic_id",
    "case_type",
    "outcome",
    "start_date",
    "end_date",
    "background",
    "problem",
    "goal",
    "constraints",
    "analysis",
    "solution",
    "execution",
    "result",
    "success_factors",
    "failure_factors",
    "mistakes",
    "lessons",
    "reusable_method",
    "applicable_conditions",
    "not_applicable_conditions",
    "future_improvements",
  ],
  problems: [
    "domain_id",
    "topic_id",
    "occurred_date",
    "impact",
    "possible_causes",
    "root_cause",
    "final_solution",
    "execution",
    "result",
    "is_resolved",
    "recurrence_count",
    "last_recurred_date",
  ],
  solutions: [
    "domain_id",
    "topic_id",
    "problem_type",
    "description",
    "steps",
    "requirements",
    "advantages",
    "disadvantages",
    "risks",
    "cost",
    "difficulty",
    "success_rate_note",
    "applicable_conditions",
    "not_applicable_conditions",
    "verification_status",
    "confidence",
  ],
  experiences: [
    "domain_id",
    "topic_id",
    "context",
    "source_case_uuid",
    "confidence",
    "times_verified",
    "last_verified_at",
  ],
  sources: [
    "domain_id",
    "topic_id",
    "source_type",
    "author",
    "organization",
    "url",
    "publication_date",
    "access_date",
    "description",
    "file_id",
  ],
  learning: [
    "domain_id",
    "topic_id",
    "learning_type",
    "source",
    "start_date",
    "finish_date",
    "progress",
    "maturity_level",
    "notes",
    "key_points",
    "questions",
    "application",
    "review_date",
  ],
};
const textFields = new Set([
  "background",
  "problem",
  "goal",
  "constraints",
  "analysis",
  "solution",
  "execution",
  "result",
  "lessons",
  "mistakes",
  "success_factors",
  "failure_factors",
  "reusable_method",
  "applicable_conditions",
  "not_applicable_conditions",
  "future_improvements",
  "description",
  "steps",
  "requirements",
  "advantages",
  "disadvantages",
  "risks",
  "success_rate_note",
  "impact",
  "possible_causes",
  "root_cause",
  "final_solution",
  "context",
  "notes",
  "key_points",
  "questions",
  "application",
  "summary",
  "content",
  "contact_note",
  "user_message",
  "assistant_message",
  "learning",
  "problems",
  "plan",
  "what_went_well",
  "what_went_wrong",
  "next_action",
  "reasoning",
  "expectations",
  "chance_factors",
  "reconsideration",
  "keep_methods",
  "avoid_methods",
]);
const numberFields = new Set([
  "importance",
  "priority",
  "privacy_level",
  "difficulty",
  "maturity_level",
  "progress",
  "times_verified",
  "recurrence_count",
  "file_id",
  "project_id",
  "person_id",
  "domain_id",
  "topic_id",
  "parent_id",
]);
const boolFields = new Set([
  "needs_verification",
  "is_resolved",
  "is_favorite",
  "is_archived",
]);
const entityFields = {
  projects: [
    "name",
    "description",
    "status",
    "priority",
    "start_date",
    "end_date",
    "summary",
    "privacy_level",
  ],
  people: [
    "name",
    "nickname",
    "organization",
    "position",
    "contact_note",
    "notes",
    "privacy_level",
  ],
  tasks: [
    "title",
    "description",
    "status",
    "priority",
    "due_date",
    "project_id",
    "person_id",
    "privacy_level",
  ],
  events: [
    "title",
    "description",
    "date",
    "project_id",
    "person_id",
    "privacy_level",
  ],
  tags: ["name", "color"],
  domains: ["name", "description", "parent_id"],
  topics: ["name", "domain_id", "parent_id"],
  conversations: [
    "platform",
    "conversation_title",
    "conversation_date",
    "user_message",
    "assistant_message",
    "summary",
    "source_file",
    "project_id",
    "privacy_level",
  ],
  reviews: ["date", "period", "summary", "learning", "problems", "plan"],
  "custom-fields": ["name", "label", "field_type", "entity_type"],
};
const labels = {
  ...detailLabels,
  name: "名称",
  title: "标题",
  nickname: "昵称",
  position: "职务",
  contact_note: "联系备注（可选）",
  priority: "优先级",
  privacy_level: "隐私级别",
  status: "状态",
  due_date: "截止日期",
  date: "日期",
  project_id: "关联项目",
  person_id: "关联人物",
  parent_id: "上级",
  color: "标签颜色",
  platform: "AI 平台",
  conversation_title: "对话标题",
  conversation_date: "对话日期",
  user_message: "我的消息",
  assistant_message: "AI 回复",
  summary: "摘要 / 总结",
  source_file: "来源文件",
  period: "周期",
  learning: "今日收获",
  problems: "存在问题",
  plan: "明天 / 下阶段计划",
  label: "显示名称",
  field_type: "字段类型",
  entity_type: "所属类型",
};
async function api(path, opts = {}) {
  const requestHash = location.hash, requestSerial = routeSerial;
  const headers = { "x-local-token": token, ...opts.headers };
  if (opts.body && !(opts.body instanceof FormData)) {
    headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(opts.body);
  }
  const r = await fetch(path, { ...opts, headers });
  let data;
  try {
    data = await r.json();
  } catch {
    throw Error("服务器返回异常，请检查程序是否仍在运行");
  }
  // A slow response from a previous page cannot replace the current editor.
  if (requestSerial > 0 && (!opts.method || opts.method === "GET") && (requestHash !== location.hash || requestSerial !== routeSerial)) {
    throw Object.assign(Error("页面已经切换，请在当前页面继续操作。"), { stalePage: true });
  }
  if (!r.ok) {
    const error = Error(
      typeof data.detail === "string" ? data.detail : "操作失败，请检查输入",
    );
    error.status = r.status;
    throw error;
  }
  return data;
}
function toast(msg) {
  $("#toast").textContent = msg;
  $("#toast").style.display = "block";
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => ($("#toast").style.display = "none"), 5000);
}
function busy(fn) {
  return async (e) => {
    const b = e?.currentTarget;
    if (b?.tagName === "BUTTON") b.disabled = true;
    try {
      await fn(e);
    } catch (err) {
      if (!err.stalePage) toast(err.message);
    } finally {
      if (b?.tagName === "BUTTON") b.disabled = false;
    }
  };
}
function modal(title, body) {
  $("#dialog-content").innerHTML =
    `<div class="dialog-head"><h2>${esc(title)}</h2><button class="icon" id="close-dialog" aria-label="关闭">×</button></div>${body}`;
  $("#close-dialog").onclick = () => $("#dialog").close();
  if (!$("#dialog").open) $("#dialog").showModal();
  if (typeof attachLookups === "function") attachLookups($("#dialog"));
}
function heading(title, sub = "", action = "") {
  return `<div class="heading"><div><div class="eyebrow">YOUR PERSONAL KNOWLEDGE SPACE</div><h1>${esc(title)}</h1><p class="subtitle">${esc(sub)}</p></div>${action}</div>`;
}
function empty(text = "还没有内容，留下第一条记忆吧。", button = "") {
  return `<div class="empty"><span class="glyph">▤</span>${esc(text)}${button}</div>`;
}
function fmtDate(v) {
  if (!v) return "";
  if (v.includes("T")) return new Date(v).toLocaleString("zh-CN");
  return cfg.date_format === "YYYY/MM/DD" ? v.replaceAll("-", "/") : v;
}
function itemRow(x) {
  return `<div class="record"><span class="record-icon">${icons[x.item_type] || "▤"}</span><div class="record-body"><h3><a href="#item/${esc(x.uuid)}">${esc(x.title)}</a> ${x.is_favorite ? "⭐" : ""}</h3><p>${esc((x.summary || x.content || "暂无摘要").slice(0, 180))}</p><div class="meta"><span>${esc(fmtDate(x.date))}</span><span class="tag neutral">${esc(kinds[x.item_type] || x.category)}</span>${(
    x.tags || []
  )
    .slice(0, 5)
    .map((t) => `<span class="tag">${esc(t.name)}</span>`)
    .join(
      "",
    )}<span>${x.privacy_level === 3 ? "敏感" : x.privacy_level === 2 ? "私人" : ""}</span></div></div></div>`;
}
function taskRows(items) {
  return items.length
    ? items
        .map(
          (t) =>
            `<div class="task"><button class="check" data-task="${esc(t.uuid)}" title="标记完成">${t.status === "已完成" ? "✓" : ""}</button><span class="text">${esc(t.title)}</span><small>${esc(t.due_date || "无截止日期")}</small></div>`,
        )
        .join("")
    : empty("当前没有待办任务");
}
async function loadRefs(existing = null) {
  const referenceKinds = ["projects", "people", "domains", "topics", "tags"];
  const lists = await Promise.all(
    referenceKinds.map((k) => api(`/api/${k}?size=100`)),
  );
  referenceKinds.forEach((k, i) => (refs[k] = lists[i].items));
  if (existing) {
    for (const k of ["projects", "people"])
      for (const item of existing[k] || [])
        if (!refs[k].some((v) => v.uuid === item.uuid)) refs[k].push(item);
    for (const [field, kind] of [
      ["domain_id", "domains"],
      ["topic_id", "topics"],
      ["project_id", "projects"],
      ["person_id", "people"],
    ]) {
      const id = existing.details?.[field] || existing[field];
      if (id && !refs[kind].some((v) => v.id === id)) {
        const result = await api(`/api/${kind}?internal_id=${id}`);
        refs[kind].push(...result.items);
      }
    }
  }
}
function optionList(items, value, key = "uuid", label = "name") {
  return items
    .map(
      (x) =>
        `<option value="${esc(x[key])}" ${String(x[key]) === String(value) ? "selected" : ""}>${esc(x[label])}</option>`,
    )
    .join("");
}
function selectField(key, value, options) {
  return `<select name="${esc(key)}" aria-label="${esc(labels[key.replace("detail.", "")] || key)}">${options.map(([v, l]) => `<option value="${esc(v)}" ${String(value ?? "") === String(v) ? "selected" : ""}>${esc(l)}</option>`).join("")}</select>`;
}
function field(key, value, kind = "", prefix = "") {
  let input;
  const name = prefix + key;
  const related = {
    project_id: "projects",
    person_id: "people",
    domain_id: "domains",
    topic_id: "topics",
    parent_id: kind === "domains" ? "domains" : "topics",
  };
  if (related[key])
    input = `<select name="${name}" data-reference-kind="${related[key]}" aria-label="${esc(labels[key] || key)}"><option value="">未选择</option>${optionList(refs[related[key]] || [], value, "id")}</select>`;
  else if (key === "privacy_level")
    input = selectField(name, value ?? 1, [
      [1, "1 · 普通"],
      [2, "2 · 私人"],
      [3, "3 · 敏感"],
    ]);
  else if (key === "confidence")
    input = selectField(name, value || "unknown", [
      ["unknown", "未知"],
      ["low", "低"],
      ["medium", "中"],
      ["high", "高"],
      ["verified", "已验证"],
    ]);
  else if (key === "maturity_level")
    input = selectField(name, value || 1, [
      [1, "1 · 刚收集"],
      [2, "2 · 已阅读"],
      [3, "3 · 已理解"],
      [4, "4 · 已实践"],
      [5, "5 · 已验证"],
      [6, "6 · 已多次复用"],
    ]);
  else if (key === "outcome")
    input = selectField(name, value || "未知", [
      ["未知", "尚未判断"],
      ["成功", "成功"],
      ["失败", "失败"],
      ["部分成功", "部分成功"],
    ]);
  else if (key === "status" && kind === "tasks")
    input = selectField(name, value || "待办", [
      ["待办", "待办"],
      ["进行中", "进行中"],
      ["已完成", "已完成"],
      ["暂停", "暂停"],
    ]);
  else if (boolFields.has(key)) {
    if (value === undefined && key === "needs_verification") value = true;
    input = `<input name="${name}" type="checkbox" ${value ? "checked" : ""}>`;
  } else if (textFields.has(key))
    input = `<textarea name="${name}" rows="3">${esc(value || "")}</textarea>`;
  else
    input = `<input name="${name}" ${key.endsWith("_date") || key === "date" ? 'type="date"' : numberFields.has(key) ? 'type="number" min="0"' : ""} value="${esc(value ?? "")}" ${["title", "name", "conversation_title", "label"].includes(key) ? "required" : ""}>`;
  return `<label class="${textFields.has(key) ? "full" : ""}">${esc(labels[key] || key)}${input}</label>`;
}
function formValues(form, fields, prefix = "") {
  const data = {};
  for (const k of fields) {
    const e = form.elements[prefix + k];
    if (!e) continue;
    data[k] = boolFields.has(k)
      ? e.checked
      : numberFields.has(k)
        ? e.value === ""
          ? null
          : Number(e.value)
        : e.value;
    if (
      (k.endsWith("_date") ||
        k === "date" ||
        k === "source_case_uuid" ||
        k === "last_verified_at") &&
      !e.value
    )
      data[k] = null;
  }
  return data;
}
function pickEntry(x) {
  const out = {};
  for (const k of commonFields) {
    if (x[k] !== undefined) out[k] = x[k];
  }
  out.tags = (x.tags || []).map((t) => (typeof t === "string" ? t : t.name));
  out.projects = (x.projects || []).map((t) =>
    typeof t === "string" ? t : t.uuid,
  );
  out.people = (x.people || []).map((t) =>
    typeof t === "string" ? t : t.uuid,
  );
  return out;
}
async function editItem(kind = "entries", existing = null) {
  await loadRefs(existing);
  const x = existing || {
    date: today(),
    category: assets.includes(kind) ? kinds[kind] : "工作记录",
    importance: 3,
    privacy_level: 1,
    status: "有效",
    details: {},
  };
  modal(
    (existing ? "编辑" : "新建") + (kinds[kind] || "记录"),
    `<form id="item-form"><div class="form-grid"><label class="full">标题<input name="title" value="${esc(x.title || "")}" required maxlength="500" autofocus placeholder="为这条记忆起一个名字"></label><label class="full">正文 · 支持 Markdown<textarea name="content" rows="7" placeholder="写下发生的事、学到的知识，或值得记住的想法…">${esc(x.content || "")}</textarea></label><div class="full"><button type="button" id="preview-draft" class="small">预览 Markdown</button><div id="draft-preview" class="markdown"></div></div></div><details ${assets.includes(kind) ? "open" : ""}><summary>分类、关联与隐私</summary><div class="form-grid"><label>日期<input type="date" name="date" value="${x.date}" required></label><label>分类<input name="category" list="categories" value="${esc(x.category)}"><datalist id="categories">${categories.map((c) => `<option>${c}</option>`).join("")}</datalist></label><label>标签 · 逗号分隔<input name="tags" value="${esc((x.tags || []).map((t) => t.name).join(", "))}"></label>${field("privacy_level", x.privacy_level)}<label>关联项目 · 按 Ctrl 多选<select name="projects" multiple>${(refs.projects || []).map((p) => `<option value="${p.uuid}" ${(x.projects || []).some((v) => v.uuid === p.uuid) ? "selected" : ""}>${esc(p.name)}</option>`).join("")}</select></label><label>关联人物 · 按 Ctrl 多选<select name="people" multiple>${(refs.people || []).map((p) => `<option value="${p.uuid}" ${(x.people || []).some((v) => v.uuid === p.uuid) ? "selected" : ""}>${esc(p.name)}</option>`).join("")}</select></label><label>重要程度<input name="importance" type="number" min="1" max="5" value="${x.importance || 3}"></label>${field("status", x.status)}${field("summary", x.summary)}${field("source", x.source)}${field("notes", x.notes)}<label>信息性质${selectField(
      "content_nature",
      x.content_nature || "unknown",
      [
        ["unknown", "尚未判断"],
        ["fact", "事实"],
        ["opinion", "观点"],
        ["hypothesis", "假设"],
        ["experience", "实践经验"],
        ["reference", "引用资料"],
        ["ai_generated", "AI 生成"],
      ],
    )}</label><label>创建来源${selectField("origin", x.origin || "manual", Object.entries(originLabels))}</label><label class="full">扩展字段 · JSON<textarea name="metadata_json" rows="2">${esc(JSON.stringify(x.metadata_json || {}, null, 2))}</textarea></label></div></details>${assets.includes(kind) ? `<details open><summary>${kinds[kind]}结构化资料 · 可稍后补充</summary><div class="form-grid">${detailFields[kind].map((k) => field(k, x.details?.[k], kind, "detail.")).join("")}</div></details>` : ""}<div class="warning">本系统不是密码管理器，请勿保存银行卡密码、账户密码、API Key、助记词等高敏感认证信息。</div><div class="drop-zone" id="drop-zone">添加或拖入附件（单个最大 100 MB）<input type="file" name="attachments" multiple><label>附件说明<input name="attachment_description"></label></div>${existing ? '<label>修改原因<input name="revision_reason" value="编辑更新"></label>' : ""}<div class="actions"><button class="primary" type="submit">保存${assets.includes(kind) ? "资料" : "记录"}</button><button type="button" id="cancel-item">取消</button></div></form>`,
  );
  $("#cancel-item").onclick = () => $("#dialog").close();
  $("#preview-draft").onclick = busy(
    async () =>
      ($("#draft-preview").innerHTML = (
        await api("/api/markdown", {
          method: "POST",
          body: { content: $("#item-form").elements.content.value },
        })
      ).html),
  );
  let dropped = [];
  const drop = $("#drop-zone");
  drop.ondragover = (e) => {
    e.preventDefault();
    drop.classList.add("over");
  };
  drop.ondragleave = () => drop.classList.remove("over");
  drop.ondrop = (e) => {
    e.preventDefault();
    dropped = [...e.dataTransfer.files];
    drop.classList.remove("over");
    toast(`已选择 ${dropped.length} 个附件`);
  };
  $("#item-form").onsubmit = busy(async (e) => {
    e.preventDefault();
    const f = e.currentTarget;
    const data = {
      ...pickEntry(x),
      ...formValues(f, [
        "title",
        "content",
        "date",
        "category",
        "importance",
        "status",
        "summary",
        "source",
        "notes",
        "privacy_level",
        "origin",
        "content_nature",
      ]),
    };
    data.tags = f.elements.tags.value
      .split(/[,，]/)
      .map((s) => s.trim())
      .filter(Boolean);
    data.projects = [...f.elements.projects.selectedOptions].map(
      (o) => o.value,
    );
    data.people = [...f.elements.people.selectedOptions].map((o) => o.value);
    try {
      data.metadata_json = JSON.parse(f.elements.metadata_json.value || "{}");
    } catch {
      throw Error("扩展字段需要有效的 JSON 对象");
    }
    data.item_type = kind;
    if (assets.includes(kind)) {
      data.details = formValues(f, detailFields[kind], "detail.");
      for (const key of Object.keys(data.details))
        if (data.details[key] === "" || data.details[key] === null)
          delete data.details[key];
      data.revision_reason = f.elements.revision_reason?.value || "编辑更新";
    }
    const button = f.querySelector("[type=submit]");
    button.disabled = true;
    try {
      const saved = await api(
        `/api/${assets.includes(kind) ? kind : "entries"}${existing ? "/" + x.uuid : ""}`,
        { method: existing ? "PUT" : "POST", body: data },
      );
      for (const file of [...f.elements.attachments.files, ...dropped]) {
        const fd = new FormData();
        fd.append("file", file);
        fd.append("description", f.elements.attachment_description.value);
        await api(`/api/entries/${saved.uuid}/attachments`, {
          method: "POST",
          body: fd,
        });
      }
      $("#dialog").close();
      toast("已保存到本地数据库");
      location.hash = "item/" + saved.uuid;
      if (location.hash === "#item/" + saved.uuid) route();
    } finally {
      button.disabled = false;
    }
  });
}
async function quickCollect() {
  if (location.hash.split("?")[0].startsWith("#quick")) return;
  location.hash = "quick";
}
async function editEntity(kind, x = null) {
  await loadRefs(x);
  const data = x || {
    date: today(),
    conversation_date: today(),
    priority: 3,
    privacy_level: kind === "people" ? 2 : 1,
    status: kind === "tasks" ? "待办" : "进行中",
  };
  modal(
    (x ? "编辑" : "新建") + kinds[kind],
    `<form id="entity-form"><div class="form-grid">${entityFields[kind].map((k) => field(k, data[k], kind)).join("")}</div>${kind === "people" ? '<div class="warning">请勿保存密码、API Key、助记词等认证信息。</div>' : ""}<div class="actions"><button class="primary">保存</button></div></form>`,
  );
  $("#entity-form").onsubmit = busy(async (e) => {
    e.preventDefault();
    const values = formValues(e.currentTarget, entityFields[kind]);
    if (x) { values.metadata_json = x.metadata_json || {}; values.origin = x.origin || "manual"; }
    for (const key of Object.keys(values))
      if (values[key] === "" || values[key] === null) delete values[key];
    const result = await api(`/api/${kind}${x ? "/" + x.uuid : ""}`, {
      method: x ? "PUT" : "POST",
      body: values,
    });
    $("#dialog").close();
    toast("已保存");
    await loadRefs();
    route();
    return result;
  });
}
async function dashboard() {
  const [s, recent, updated, fav, projects] = await Promise.all([
    api("/api/stats"),
    api("/api/entries?size=5"),
    api("/api/entries?size=4&sort=updated"),
    api("/api/entries?size=3&is_favorite=true"),
    api("/api/projects?size=3"),
  ]);
  $("#main").innerHTML =
    '<section class="dashboard-hero" aria-label="我的记忆空间">' +
    heading(
      (cfg.nickname ? cfg.nickname + "，" : "") + "每一份经验，都值得留下。",
      new Date().toLocaleDateString("zh-CN", {
        year: "numeric",
        month: "long",
        day: "numeric",
        weekday: "long",
      }),
      '<button class="primary" id="new-entry">＋ 新建记录</button>',
    ) +
    '<div class="dashboard-caption"><span>一叶一记，把今天留给未来的自己。</span><div class="section-links"><a href="#ai-workflow">每日整理与审核 →</a><a href="#semantic-search">寻找旧经验 →</a></div></div></section>' +
    `<div class="stats">${[
      ["今日记录", s.today_count, "今天的新记忆", "▤"],
      ["本周积累", s.week_count, "一点一滴，持续生长", "◷"],
      ["本月记录", s.month_count, "把经历变成你的资产", "▥"],
      ["知识资产", s.total, "所有记录与知识内容", "✦"],
    ]
      .map(
        ([l, n, t, i]) =>
          `<div class="stat"><label>${l}</label><span class="stat-icon">${i}</span><strong>${n}</strong><small>${t}</small></div>`,
      )
      .join(
        "",
      )}</div><form id="inline-quick" class="quick-box"><span>✎</span><textarea name="content" rows="1" required placeholder="此刻有什么值得记下？先收集，稍后整理。"></textarea><button class="primary">收集</button></form><div class="columns"><div><section class="panel"><div class="panel-top"><h2>最近记录 <span class="muted">/ RECENT</span></h2><a href="#entries">查看全部 →</a></div>${recent.items.map(itemRow).join("") || empty("你的知识空间已准备好，从第一条记录开始。", '<p><button id="empty-create">＋ 新建记录</button></p>')}</section><section class="panel"><div class="panel-top"><h2>最近项目</h2><a href="#projects">项目档案 →</a></div>${projects.items.length ? projects.items.map((p) => `<div class="record"><span class="record-icon">▱</span><div class="record-body"><h3><a href="#entity/projects/${p.uuid}">${esc(p.name)}</a></h3><p>${esc(p.description || "暂无说明")}</p><span class="tag">${esc(p.status)}</span></div></div>`).join("") : empty("把一个长期目标，建立为项目档案。")}</section><section class="panel"><h2>最近修改</h2>${updated.items.map(itemRow).join("") || empty("暂无修改记录")}</section></div><div><section class="panel"><div class="panel-top"><h2>今天待办</h2><a href="#tasks">全部任务 →</a></div>${taskRows(s.today_tasks)}<div class="actions"><button id="new-task" class="small">＋ 添加任务</button></div></section><section class="panel"><h2>未完成任务</h2>${taskRows(s.open_tasks.slice(0, 6))}</section><section class="panel"><div class="panel-top"><h2>收藏记录</h2><a href="#favorite">☆ 收藏夹</a></div>${fav.items.map(itemRow).join("") || empty("收藏重要内容，下次更快找到。")}</section><section class="panel"><h2>常用标签</h2><div class="section-links">${
      s.tags
        .slice(0, 12)
        .map(
          (t) =>
            `<a href="#entries?tag=${encodeURIComponent(t.label)}">${esc(t.label)} <small>${t.count}</small></a>`,
        )
        .join("") ||
      '<span class="muted">记录时添加标签，它们会在这里汇聚。</span>'
    }</div></section><div class="hero-note"><strong>属于你的长期记忆</strong>收集 → 理解 → 实践 → 复盘 → 复用<br>所有数据保存在本地，随时可以完整带走。</div></div></div>`;
  $("#new-entry").onclick = () => editItem();
  if ($("#empty-create")) $("#empty-create").onclick = () => editItem();
  $("#new-task").onclick = () => editEntity("tasks");
  $("#inline-quick").onsubmit = busy(async (e) => {
    e.preventDefault();
    await api("/api/inbox", {
      method: "POST",
      body: { content: e.currentTarget.elements.content.value },
    });
    toast("已收集");
    dashboard();
  });
  bindTasks();
}
function bindTasks() {
  $$("[data-task]").forEach(
    (b) =>
      (b.onclick = busy(async () => {
        const t = await api("/api/tasks/" + b.dataset.task);
        const d = {};
        for (const k of entityFields.tasks) d[k] = t[k];
        d.status = t.status === "已完成" ? "待办" : "已完成";
        await api("/api/tasks/" + t.uuid, { method: "PUT", body: d });
        route();
      })),
  );
}
function filterForm(kind) {
  return `<form id="filters"><div class="filters"><input name="q" placeholder="关键词，以空格组合" value="${esc(currentFilters.q || "")}"><select name="scope"><option value="all">标题 + 正文</option><option value="title">仅标题</option><option value="content">仅正文</option></select><select name="preset"><option value="">时间快捷筛选</option>${[
    ["today", "今天"],
    ["yesterday", "昨天"],
    ["7", "最近 7 天"],
    ["30", "最近 30 天"],
    ["year", "今年"],
  ]
    .map(([v, t]) => `<option value="${v}">${t}</option>`)
    .join(
      "",
    )}</select><input type="date" name="start" value="${esc(currentFilters.start || "")}" aria-label="开始日期"><input type="date" name="end" value="${esc(currentFilters.end || "")}" aria-label="结束日期"><input name="category" list="filter-categories" placeholder="分类" value="${esc(currentFilters.category || "")}"><datalist id="filter-categories">${categories.map((c) => `<option>${c}</option>`).join("")}</datalist><select name="project"><option value="">全部项目</option>${optionList(refs.projects || [], currentFilters.project)}</select><select name="person"><option value="">全部人物</option>${optionList(refs.people || [], currentFilters.person)}</select><input name="tag" placeholder="标签" value="${esc(currentFilters.tag || "")}"><input name="status" placeholder="状态" value="${esc(currentFilters.status || "")}"><select name="importance"><option value="">全部重要程度</option>${[1, 2, 3, 4, 5].map((i) => `<option ${String(currentFilters.importance) === String(i) ? "selected" : ""}>${i}</option>`).join("")}</select>${assets.includes(kind) ? `<select name="domain_id"><option value="">全部领域</option>${optionList(refs.domains || [], currentFilters.domain_id, "id")}</select><select name="topic_id"><option value="">全部主题</option>${optionList(refs.topics || [], currentFilters.topic_id, "id")}</select>${kind === "knowledge" || kind === "learning" ? `<select name="maturity_level"><option value="">全部成熟度</option>${[1, 2, 3, 4, 5, 6].map((v) => `<option value="${v}" ${String(currentFilters.maturity_level) === String(v) ? "selected" : ""}>${v}</option>`).join("")}</select>` : ""}${kind === "cases" ? `<select name="outcome"><option value="">全部结果</option><option>成功</option><option>失败</option><option>部分成功</option></select><select name="is_reviewed"><option value="">全部复盘状态</option><option value="true">已复盘</option><option value="false">未复盘</option></select><select name="reusable"><option value="">全部复用条件</option><option value="true">有可复用方法</option></select><input type="number" name="min_used" min="0" placeholder="至少复用次数" value="${esc(currentFilters.min_used || "")}">` : ""}` : ""}<button class="primary">筛选</button><button type="button" id="clear-filter">清除</button></div></form>`;
}
async function listPage(kind = "entries") {
  currentListKind = kind;
  await loadRefs();
  let filters = { ...currentFilters };
  if (kind === "favorite") filters.is_favorite = true;
  else if (kind === "trash") filters.trash = true;
  else if (kind !== "entries") filters.item_type = kind;
  const r = await api(
    "/api/entries?" + new URLSearchParams({ ...filters, page, size: 25 }),
  );
  $("#main").innerHTML =
    heading(
      kinds[kind] || { favorite: "收藏夹", trash: "回收站" }[kind],
      kind === "inbox"
        ? "先捕捉想法，再整理成长期知识。"
        : `共 ${r.total} 项 · 记录、关联，让经验可被再次找到。`,
      kind === "trash"
        ? ""
        : `<button id="new-item" class="primary">＋ ${kind === "inbox" ? "快速收集" : "新建"}</button>`,
    ) +
    filterForm(kind) +
    `<section class="panel">${r.items.map(itemRow).join("") || empty("没有找到内容。试试调整筛选，或创建第一项资料。")}</section><div class="pagination"><button id="prev-page" ${page <= 1 ? "disabled" : ""}>← 上一页</button><span>第 ${page} / ${Math.max(1, Math.ceil(r.total / 25))} 页 · ${r.total} 项</span><button id="next-page" ${page * 25 >= r.total ? "disabled" : ""}>下一页 →</button></div>`;
  if ($("#new-item"))
    $("#new-item").onclick = () =>
      kind === "inbox"
        ? quickCollect()
        : editItem(assets.includes(kind) ? kind : "entries");
  $("#prev-page").onclick = () => {
    page--;
    listPage(kind);
  };
  $("#next-page").onclick = () => {
    page++;
    listPage(kind);
  };
  $("#clear-filter").onclick = () => {
    currentFilters = {};
    page = 1;
    listPage(kind);
  };
  const f = $("#filters");
  for (const [k, v] of Object.entries(currentFilters))
    if (f.elements[k]) f.elements[k].value = v;
  f.elements.preset.onchange = () => {
    const v = f.elements.preset.value,
      d = new Date(),
      e = today();
    if (v === "today") f.elements.start.value = e;
    else if (v === "year") f.elements.start.value = e.slice(0, 4) + "-01-01";
    else {
      d.setDate(d.getDate() - (v === "yesterday" ? 1 : Number(v) - 1));
      f.elements.start.value = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
    }
    f.elements.end.value = v === "yesterday" ? f.elements.start.value : e;
  };
  f.onsubmit = (e) => {
    e.preventDefault();
    currentFilters = Object.fromEntries(
      [...new FormData(f)].filter(([k, v]) => v && k !== "preset"),
    );
    page = 1;
    listPage(kind);
  };
}
async function itemDetail(uid) {
  const x = await api("/api/entries/" + uid),
    kind = x.item_type || "entries";
  const html = (
    await api("/api/markdown", { method: "POST", body: { content: x.content } })
  ).html;
  const relations = await api(`/api/items/${uid}/relations`);
  $("#main").innerHTML =
    `<a href="#${kind}">← ${kinds[kind] || "记录"}</a><div class="heading"><h1 class="detail-title">${esc(x.title)}</h1><div class="actions">${x.deleted_at ? '<button id="restore" class="primary">恢复记录</button><button id="purge" class="danger">彻底删除</button>' : '<button id="edit" class="primary">编辑</button><button id="favorite">' + (x.is_favorite ? "★ 已收藏" : "☆ 收藏") + '</button><button id="delete" class="danger">移入回收站</button>'}</div></div><div class="detail-meta"><span>${esc(fmtDate(x.date))}</span><span>${esc(kinds[kind])}</span><span>${esc(x.category)}</span><span>重要度 ${x.importance}/5</span><span>隐私 ${x.privacy_level}</span><span>${esc(natureLabels[x.content_nature] || x.content_nature)}</span><span>来源 ${esc(originLabels[x.origin] || x.origin)}</span></div>${x.deleted_at ? '<div class="notice">此内容在回收站，恢复后重新参与搜索与导出。</div>' : ""}<div class="columns"><div><section class="panel"><div class="markdown">${html || '<span class="muted">暂无正文</span>'}</div>${x.summary ? `<h2>摘要</h2><p class="field-text">${esc(x.summary)}</p>` : ""}</section>${
      x.details
        ? `<section class="panel"><h2>结构化资料</h2>${Object.entries(x.details)
            .filter(
              ([k, v]) =>
                !["id", "uuid"].includes(k) &&
                v !== null &&
                v !== "" &&
                v !== false,
            )
            .map(
              ([k, v]) =>
                `<h3>${esc(detailLabels[k] || k)}</h3><p class="field-text">${esc(k === "domain_id" ? refs.domains?.find((d) => d.id === v)?.name || v : k === "topic_id" ? refs.topics?.find((d) => d.id === v)?.name || v : detailValue(k, v))}</p>`,
            )
            .join("")}</section>`
        : ""
    }<section class="panel"><div class="panel-top"><h2>关联资料与知识关系</h2><button id="add-relation" class="small">＋ 建立关联</button></div>${relations.map((r) => `<div class="record"><div class="record-body"><a href="#item/${r.target.uuid}">${esc(r.target.title)}</a> <span class="tag">${esc(relationLabels[r.relation_type] || r.relation_type)}</span><p>${esc(r.description)}</p>${r.judgment ? `<p>判断：${esc(r.judgment)}</p>` : ""}${r.evidence ? `<p>证据：${esc(r.evidence)}</p>` : ""}</div><button class="small danger" data-remove-relation="${r.uuid}">解除</button></div>`).join("") || '<p class="muted">关联来源、知识、案例或方案，保留完整来龙去脉。</p>'}</section><section class="panel"><h2>原始资料与修改轨迹</h2><details><summary>查看最初保存的原文</summary><p class="field-text">${esc(x.original_content)}</p></details>${typeof x.metadata_json?.speech?.original_transcript === "string" && x.metadata_json.speech.original_transcript ? `<details><summary>查看机器原始转写</summary><p class="muted">机器转写可能存在识别错误；正文是核对后保存的文字，原始录音见附件。</p><p class="field-text">${esc(x.metadata_json.speech.original_transcript)}</p></details>` : ""}<button id="history" class="small">修改历史 / 复用日志</button><p class="muted">创建：${esc(fmtDate(x.created_at))}<br>更新：${esc(fmtDate(x.updated_at))}<br>UUID：${esc(x.uuid)}</p></section></div><div><section class="panel"><h2>整理与复用</h2>${kind === "inbox" ? `<label>整理为<select id="convert-kind">${["entries", ...assets, "projects", "tasks"].map((k) => `<option value="${k}">${kinds[k]}</option>`).join("")}</select></label><button id="convert" class="primary">完成整理</button>` : ""}${["knowledge", "cases", "solutions", "experiences"].includes(kind) ? '<button id="reuse" class="primary">↻ 我今天重新使用了它</button>' : ""}${kind === "knowledge" ? '<div class="actions"><button id="reviewed">已复习 / 下次复习</button></div>' : ""}${kind === "cases" ? '<div class="actions"><button id="case-review">开始复盘</button><button id="associate-case">关联任务 / 事件</button></div><div id="case-associations"></div>' : ""}<div class="actions"><button id="extract">提炼知识 / 方案 / 经验</button><button id="recommend">寻找相似案例与旧经验</button><button id="ai-button">AI 整理</button></div><div id="recommendations"></div></section><section class="panel"><h2>所属项目与人物</h2>${x.projects.map((p) => `<p><a href="#entity/projects/${p.uuid}">▱ ${esc(p.name)}</a></p>`).join("")}${x.people.map((p) => `<p><a href="#entity/people/${p.uuid}">♧ ${esc(p.name)}</a></p>`).join("")}<div class="section-links">${x.tags.map((t) => `<a href="#entries?tag=${encodeURIComponent(t.name)}"># ${esc(t.name)}</a>`).join("")}</div></section><section class="panel"><h2>附件</h2>${x.attachments.map((a) => `<div class="record"><div class="record-body"><a href="/api/attachments/${a.uuid}">${esc(a.original_filename)}</a><p>${esc(a.description)} · ${(a.file_size / 1024).toFixed(1)} KB</p></div><button data-del-attachment="${a.uuid}" class="small danger">删除</button></div>`).join("") || '<p class="muted">暂无附件</p>'}<label>添加附件<input id="add-attachment" type="file" multiple></label></section><section class="panel"><h2>相关链接</h2>${x.links.map((l) => `<p><a href="${esc(l.url)}" target="_blank" rel="noopener">${esc(l.title || l.url)}</a> <button class="small" data-del-link="${l.uuid}">移除</button></p>`).join("")}<button id="add-link" class="small">＋ 添加链接</button></section></div></div>`;
  for (const attachment of x.attachments) {
    const anchor = document.querySelector(`[data-del-attachment="${attachment.uuid}"]`);
    if (anchor && !x.deleted_at) {
      const textButton = document.createElement("button");
      textButton.className = "small";
      textButton.textContent = "文字识别";
      textButton.onclick = busy(() => attachmentExtractionUI(attachment.uuid));
      anchor.before(textButton);
    }
  }
  if ($("#edit"))
    $("#edit").onclick = async () =>
      kind === "conversations"
        ? editEntity("conversations", await api("/api/conversations/" + uid))
        : editItem(kind, x);
  if ($("#delete"))
    $("#delete").onclick = busy(async () => {
      if (!confirm("将此项移入回收站？可以随时恢复。")) return;
      await api("/api/entries/" + uid, { method: "DELETE" });
      toast("已移入回收站");
      location.hash = "trash";
    });
  if ($("#restore"))
    $("#restore").onclick = busy(async () => {
      await api(`/api/entries/${uid}/restore`, { method: "POST" });
      route();
    });
  if ($("#purge"))
    $("#purge").onclick = busy(async () => {
      if (
        prompt("此操作同时永久删除附件和版本记录。输入“彻底删除”确认") !==
        "彻底删除"
      )
        return;
      await api(`/api/entries/${uid}/permanent`, { method: "DELETE" });
      location.hash = "trash";
    });
  if ($("#favorite"))
    $("#favorite").onclick = busy(async () => {
      await api("/api/items/" + uid + "/flags", {
        method: "PATCH",
        body: { is_favorite: !x.is_favorite },
      });
      route();
    });
  if ($("#convert"))
    $("#convert").onclick = busy(async () => {
      await api(`/api/items/${uid}/convert`, {
        method: "POST",
        body: { kind: $("#convert-kind").value },
      });
      route();
    });
  if ($("#reuse"))
    $("#reuse").onclick = () => {
      modal(
        "记录本次复用",
        `<form id="reuse-form"><div class="form-grid">${field("project_id", null)}${field("result", "")}${field("notes", "")}</div><button class="primary">记录复用</button></form>`,
      );
      $("#reuse-form").onsubmit = busy(async (e) => {
        e.preventDefault();
        await api(`/api/items/${uid}/reuse`, {
          method: "POST",
          body: formValues(e.currentTarget, ["project_id", "result", "notes"]),
        });
        $("#dialog").close();
        route();
      });
    };
  if ($("#reviewed"))
    $("#reviewed").onclick = busy(async () => {
      const d = prompt("下次复习日期（YYYY-MM-DD，可留空）", today());
      if (d === null) return;
      await api(`/api/knowledge/${uid}/reviewed`, {
        method: "POST",
        body: { next_review_date: d },
      });
      route();
    });
  $("#extract").onclick = () => {
    modal(
      "从原资料中提炼",
      `<form id="extract-form"><label>提炼为<select name="kind" aria-label="提炼为"><option value="knowledge">知识</option><option value="solutions">解决方案</option><option value="experiences">经验</option></select></label><label>标题<input name="title" required value="${esc("从「" + x.title + "」提炼")}"></label><label>值得留下的内容<textarea name="content" required></textarea></label><button class="primary">保存并关联原资料</button></form>`,
    );
    $("#extract-form").onsubmit = busy(async (e) => {
      e.preventDefault();
      const saved = await api(`/api/items/${uid}/extract`, {
        method: "POST",
        body: Object.fromEntries(new FormData(e.currentTarget)),
      });
      $("#dialog").close();
      location.hash = "item/" + saved.uuid;
    });
  };
  $("#recommend").onclick = busy(async () => {
    const r = await api(`/api/items/${uid}/recommendations`);
    $("#recommendations").innerHTML =
      r.reusable.map(itemRow).join("") ||
      '<p class="muted">暂未找到同领域、标签或关键词的旧经验。</p>';
  });
  if (x.metadata_json?.ai_workflow_archive && x.metadata_json?.draft_uuid) {
    $("#ai-button").textContent = "查看审核草稿";
  }
  $("#ai-button").onclick = busy(async () => {
    if (x.metadata_json?.ai_workflow_archive && x.metadata_json?.draft_uuid) {
      location.hash = "ai-draft/" + x.metadata_json.draft_uuid;
      return;
    }
    const draft = await api("/api/ai/organize", { method: "POST", body: { uuid: uid } });
    location.hash = "ai-draft/" + draft.uuid;
  });
  $("#history").onclick = busy(async () => {
    const h = await api(`/api/items/${uid}/history`);
    modal(
      "修改历史与复用记录",
      `<p class="muted">显示最近 20 次，所有历史保存在完整数据库导出中。</p>${h.revisions.map((r) => `<details><summary>${esc(fmtDate(r.created_at))} · ${esc(r.reason)}</summary><h3>旧版本</h3><pre>${esc(JSON.stringify(r.old_version, null, 2))}</pre><h3>新版本</h3><pre>${esc(JSON.stringify(r.new_version, null, 2))}</pre></details>`).join("") || "<p>暂无修改历史</p>"}<h2>复用日志</h2>${h.reuse.map((r) => `<p>${esc(fmtDate(r.used_at))} · ${esc(r.result)}<br>${esc(r.notes)}</p>`).join("") || "<p>暂无复用</p>"}`,
    );
  });
  $("#add-relation").onclick = () => {
    modal(
      "建立知识关系",
      `<form id="relation-form"><label>搜索要关联的资料<input id="relation-query" placeholder="输入标题关键词"></label><button type="button" id="relation-search">搜索</button><label>关联目标<select name="to_uuid" id="relation-target" required></select></label><label>关系类型<select name="relation_type">${[
        ["related_to", "相关资料"],
        ["prerequisite_of", "前置知识"],
        ["supplements", "补充"],
        ["similar_to", "相似"],
        ["conflicts_with", "观点冲突"],
        ["case_of", "案例"],
        ["solution_for", "解决方案"],
        ["supersedes", "升级版本"],
        ["derived_from", "来源于"],
      ]
        .map(([v, l]) => `<option value="${v}">${l}</option>`)
        .join(
          "",
        )}</select></label><label>说明<textarea name="description"></textarea></label><label>自己的判断<textarea name="judgment"></textarea></label><label>证据<textarea name="evidence"></textarea></label><button class="primary">保存关系</button></form>`,
    );
    $("#relation-search").onclick = busy(async () => {
      const r = await api(
        "/api/entries?size=50&q=" +
          encodeURIComponent($("#relation-query").value),
      );
      $("#relation-target").innerHTML = r.items
        .filter((v) => v.uuid !== uid)
        .map(
          (v) =>
            `<option value="${v.uuid}">${esc(kinds[v.item_type])} · ${esc(v.title)}</option>`,
        )
        .join("");
    });
    $("#relation-form").onsubmit = busy(async (e) => {
      e.preventDefault();
      await api("/api/item-relations", {
        method: "POST",
        body: {
          ...Object.fromEntries(new FormData(e.currentTarget)),
          from_uuid: uid,
        },
      });
      $("#dialog").close();
      route();
    });
  };
  $$("[data-remove-relation]").forEach(
    (b) =>
      (b.onclick = busy(async () => {
        await api("/api/relations/" + b.dataset.removeRelation, {
          method: "DELETE",
        });
        route();
      })),
  );
  $("#add-attachment").onchange = busy(async (e) => {
    for (const file of e.target.files) {
      const f = new FormData();
      f.append("file", file);
      await api(`/api/entries/${uid}/attachments`, { method: "POST", body: f });
    }
    route();
  });
  $$("[data-del-attachment]").forEach(
    (b) =>
      (b.onclick = busy(async () => {
        if (!confirm("永久删除这个附件文件？")) return;
        await api("/api/attachments/" + b.dataset.delAttachment, {
          method: "DELETE",
        });
        route();
      })),
  );
  $("#add-link").onclick = busy(async () => {
    const url = prompt("输入网页 URL");
    if (!url) return;
    const title = prompt("链接标题（可选）") || "";
    await api(`/api/entries/${uid}/links`, {
      method: "POST",
      body: { url, title },
    });
    route();
  });
  $$("[data-del-link]").forEach(
    (b) =>
      (b.onclick = busy(async () => {
        await api("/api/links/" + b.dataset.delLink, { method: "DELETE" });
        route();
      })),
  );
  if ($("#case-review")) $("#case-review").onclick = () => caseReview(uid);
  if ($("#associate-case"))
    $("#associate-case").onclick = () => associateCase(uid);
  if (kind === "cases") {
    const a = await api(`/api/cases/${uid}/associations`);
    $("#case-associations").innerHTML =
      `<p class="muted">关联任务：${a.tasks.map((t) => esc(t.title)).join("、") || "无"}<br>关联事件：${a.events.map((t) => esc(t.title)).join("、") || "无"}</p>`;
  }
}
function caseReview(uid) {
  const fields = [
    "reasoning",
    "expectations",
    "what_went_well",
    "what_went_wrong",
    "chance_factors",
    "reconsideration",
    "keep_methods",
    "avoid_methods",
    "lessons",
    "next_action",
  ];
  const names = [
    "当时为什么这样做",
    "结果是否达到预期",
    "哪些判断正确",
    "哪些判断错误",
    "有哪些偶然因素",
    "今天重新处理会怎么做",
    "哪些经验值得永久保存",
    "哪些方法未来不要再使用",
    "核心教训",
    "下一步行动",
  ];
  modal(
    "案例复盘",
    `<form id="case-review-form"><div class="form-grid">${fields.map((k, i) => `<label class="full">${names[i]}<textarea name="${k}"></textarea></label>`).join("")}</div><button class="primary">保存复盘</button></form>`,
  );
  $("#case-review-form").onsubmit = busy(async (e) => {
    e.preventDefault();
    await api(`/api/cases/${uid}/review`, {
      method: "POST",
      body: Object.fromEntries(new FormData(e.currentTarget)),
    });
    $("#dialog").close();
    toast("已保存复盘，可继续提炼知识与经验");
    route();
  });
}
async function associateCase(uid) {
  modal(
    "关联案例任务 / 事件",
    '<form id="associate-form"><select name="kind" id="associate-kind"><option value="tasks">任务</option><option value="events">事件</option></select><select name="uuid" id="associate-target"></select><button class="primary">关联</button></form>',
  );
  async function load() {
    const r = await api("/api/" + $("#associate-kind").value + "?size=100");
    $("#associate-target").innerHTML = optionList(
      r.items,
      null,
      "uuid",
      "title",
    );
  }
  $("#associate-kind").onchange = load;
  await load();
  $("#associate-form").onsubmit = busy(async (e) => {
    e.preventDefault();
    await api(`/api/cases/${uid}/associate`, {
      method: "POST",
      body: Object.fromEntries(new FormData(e.currentTarget)),
    });
    $("#dialog").close();
    route();
  });
}
async function entityList(kind) {
  const r = await api(
    `/api/${kind}?` +
      new URLSearchParams({ ...currentFilters, page, size: 30 }),
  );
  $("#main").innerHTML =
    heading(
      kinds[kind],
      `共 ${r.total} 项 · 所有内容都保存在本地数据库中。`,
      '<button id="new-entity" class="primary">＋ 新建</button>',
    ) +
    `<section class="panel">${r.items.length ? `<div class="wrap"><table class="data-table"><thead><tr><th>名称 / 标题</th><th>状态 / 类型</th><th>更新时间</th><th>操作</th></tr></thead><tbody>${r.items.map((x) => `<tr><td><a href="#entity/${kind}/${x.uuid}">${esc(x.name || x.title || x.conversation_title || x.date || x.label)}</a></td><td>${esc(x.status || x.platform || x.period || "—")}</td><td>${esc(fmtDate(x.updated_at))}</td><td><button class="small" data-edit="${x.uuid}">编辑</button> <button class="small danger" data-del="${x.uuid}">删除</button></td></tr>`).join("")}</tbody></table></div>` : empty("还没有内容，点击右上角新建。")}</section><div class="pagination"><button id="prev-page" ${page === 1 ? "disabled" : ""}>上一页</button><span>第 ${page} 页</span><button id="next-page" ${page * 30 >= r.total ? "disabled" : ""}>下一页</button></div>`;
  $("#new-entity").onclick = () => editEntity(kind);
  $$("[data-edit]").forEach(
    (b) =>
      (b.onclick = () =>
        editEntity(
          kind,
          r.items.find((x) => x.uuid === b.dataset.edit),
        )),
  );
  $$("[data-del]").forEach(
    (b) =>
      (b.onclick = busy(async () => {
        if (
          !confirm("删除此项资料？项目、人物删除后，其关联会解除，原记录保留。")
        )
          return;
        await api(`/api/${kind}/${b.dataset.del}`, { method: "DELETE" });
        route();
      })),
  );
  $("#prev-page").onclick = () => {
    page--;
    entityList(kind);
  };
  $("#next-page").onclick = () => {
    page++;
    entityList(kind);
  };
}
async function entityDetail(kind, uid) {
  const x = await api(`/api/${kind}/${uid}`);
  let overview = null;
  if (["projects", "people"].includes(kind))
    overview = await api(`/api/${kind}/${uid}/overview?page=${page}`);
  $("#main").innerHTML =
    heading(
      x.name || x.title || x.conversation_title || "资料详情",
      kinds[kind],
      '<button id="edit-entity" class="primary">编辑</button>' +
        (kind === "projects"
          ? '<button id="project-review">开始项目复盘</button>'
          : ""),
    ) +
    `<section class="panel">${Object.entries(x)
      .filter(([k, v]) => !["id", "uuid", "metadata_json"].includes(k) && v)
      .map(
        ([k, v]) =>
          `<h3>${esc(labels[k] || k)}</h3><p class="field-text">${esc(typeof v === "object" ? JSON.stringify(v) : v)}</p>`,
      )
      .join(
        "",
      )}</section>${kind === "conversations" && x.entry_uuid ? `<p><a href="#item/${x.entry_uuid}">查看共享资料、附件、标签与知识关系 →</a></p>` : ""}${overview ? `<div class="columns"><section class="panel"><div class="panel-top"><h2>相关记录与时间线 · ${overview.entries.total} 项</h2><a href="#entries?${kind === "projects" ? "project" : "person"}=${uid}">查看全部 →</a></div>${overview.entries.items.map(itemRow).join("") || empty("尚无关联记录")}</section><div><section class="panel"><h2>相关${kind === "projects" ? "人物" : "项目"}</h2>${overview.related.map((v) => `<p><a href="#entity/${kind === "projects" ? "people" : "projects"}/${v.uuid}">${esc(v.name)}</a></p>`).join("") || '<p class="muted">暂无</p>'}</section><section class="panel"><h2>任务</h2>${taskRows(overview.tasks)}</section><section class="panel"><h2>事件与附件</h2>${overview.events.map((v) => `<p>${esc(v.date)} · ${esc(v.title)}</p>`).join("")}${overview.attachments.map((v) => `<p><a href="/api/attachments/${v.uuid}">${esc(v.original_filename)}</a></p>`).join("")}</section></div></div>` : ""}`;
  if (kind === "tasks" && typeof aiFollowupProvenance === "function") $("#main").insertAdjacentHTML("beforeend", aiFollowupProvenance(x));
  $("#edit-entity").onclick = () => editEntity(kind, x);
  if ($("#project-review"))
    $("#project-review").onclick = () => projectReview(uid);
  bindTasks();
  if (overview) {
    const max = Math.max(
      overview.entries.total,
      ...Object.values(overview.totals),
    );
    $("#main").insertAdjacentHTML(
      "beforeend",
      `<div class="pagination"><button id="overview-prev" ${page === 1 ? "disabled" : ""}>上一页</button><span>第 ${page} 页 · 每组 ${overview.size} 项</span><button id="overview-next" ${page * overview.size >= max ? "disabled" : ""}>下一页</button></div>`,
    );
    $("#overview-prev").onclick = () => {
      page--;
      entityDetail(kind, uid);
    };
    $("#overview-next").onclick = () => {
      page++;
      entityDetail(kind, uid);
    };
  }
}
async function calendarPage() {
  const month = currentFilters.month || today().slice(0, 7),
    data = await api("/api/calendar?month=" + month),
    [y, m] = month.split("-").map(Number),
    first = (new Date(y, m - 1, 1).getDay() + 6) % 7,
    days = new Date(y, m, 0).getDate();
  $("#main").innerHTML =
    heading("日历", "让每一天的积累，变得清晰可见。") +
    `<div class="filters"><input id="calendar-month" type="month" value="${month}"></div><div class="calendar">${["一", "二", "三", "四", "五", "六", "日"].map((v) => `<div class="weekday">周${v}</div>`).join("")}${Array.from({ length: first }, () => "<span></span>").join("")}${Array.from(
      { length: days },
      (_, i) => {
        const d = month + "-" + String(i + 1).padStart(2, "0");
        return `<div data-date="${d}" class="${d === today() ? "today" : ""}"><strong>${i + 1}</strong>${data
          .filter((v) => v.date === d)
          .map((v) => `<span class="tag">${esc(v.category)} ${v.count}</span>`)
          .join("")}</div>`;
      },
    ).join("")}</div>`;
  $("#calendar-month").onchange = (e) => {
    currentFilters.month = e.target.value;
    calendarPage();
  };
  $$("[data-date]").forEach(
    (d) =>
      (d.onclick = () =>
        (location.hash =
          "entries?start=" + d.dataset.date + "&end=" + d.dataset.date)),
  );
}
async function timelinePage() {
  const r = await api("/api/timeline");
  let year = "";
  $("#main").innerHTML =
    heading("时间线", "年 → 月 → 日，回看走过的每一步。") +
    r
      .map((v) => {
        let h = "";
        if (v.month.slice(0, 4) !== year) {
          year = v.month.slice(0, 4);
          h = `<div class="timeline-year">${year} 年</div>`;
        }
        const [y, m] = v.month.split("-").map(Number),
          end = `${v.month}-${new Date(y, m, 0).getDate()}`;
        return (
          h +
          `<div class="timeline-month"><a href="#entries?start=${v.month}-01&end=${end}">${v.month} · ${v.count} 条内容 →</a></div>`
        );
      })
      .join("") +
    (r.length ? "" : empty("第一条记录会成为时间线的起点。"));
}
function bars(rows) {
  const max = Math.max(...rows.map((r) => r.count), 1);
  return rows.length
    ? `<div class="bars">${rows
        .slice(-90)
        .map(
          (r) =>
            `<div class="bar" style="height:${Math.max(3, (r.count / max) * 100)}%" title="${esc(r.label)}：${r.count}"></div>`,
        )
        .join(
          "",
        )}</div><p class="muted">${esc(rows[0].label)} → ${esc(rows.at(-1).label)}</p>`
    : empty("积累记录后，这里会出现长期趋势。");
}
function horizontal(rows) {
  const max = Math.max(...rows.map((r) => r.count), 1);
  return (
    rows
      .slice(0, 20)
      .map(
        (r) =>
          `<div class="horizontal"><span>${esc(r.label)}</span><i style="width:${(r.count / max) * 100}%"></i><span>${r.count}</span></div>`,
      )
      .join("") || '<p class="muted">暂无数据</p>'
  );
}
async function statsPage() {
  const r = await api("/api/stats?" + new URLSearchParams(currentFilters));
  $("#main").innerHTML =
    heading("统计中心", "观察长期积累，而不只是记录数量。") +
    `<form id="stats-filter" class="filters"><input name="start" type="date" value="${esc(currentFilters.start || "")}"><input name="end" type="date" value="${esc(currentFilters.end || "")}"><button>更新范围</button></form><div class="stats"><div class="stat"><label>范围内记录</label><strong>${r.total}</strong></div><div class="stat"><label>完成任务</label><strong>${r.completed_tasks}</strong></div><div class="stat"><label>活跃项目</label><strong>${r.project_count}</strong></div><div class="stat"><label>涉及人物</label><strong>${r.people_count}</strong></div></div><div class="columns"><div><section class="panel"><h2>每日积累 · 最近 90 个有记录日期</h2>${bars(r.daily)}</section><section class="panel"><h2>月度趋势</h2>${bars(r.monthly)}</section></div><div><section class="panel"><h2>分类分布</h2>${horizontal(r.categories)}</section><section class="panel"><h2>活跃项目</h2>${horizontal(r.projects)}</section><section class="panel"><h2>标签频率</h2>${horizontal(r.tags)}</section></div></div>`;
  $("#stats-filter").onsubmit = (e) => {
    e.preventDefault();
    currentFilters = Object.fromEntries(
      [...new FormData(e.currentTarget)].filter(([, v]) => v),
    );
    statsPage();
  };
}
async function reviewPage() {
  const end = currentFilters.end || today(),
    period = currentFilters.period || "day";
  let d = new Date(end + "T12:00:00");
  if (period === "week") d.setDate(d.getDate() - 6);
  if (period === "month") d.setDate(1);
  if (period === "year") {
    d.setMonth(0);
    d.setDate(1);
  }
  const start = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  const s = await api(`/api/stats?start=${start}&end=${end}`);
  $("#main").innerHTML =
    heading("总结与复盘", "统计提供事实，你来沉淀收获、判断和下一步。") +
    `<form id="review-range" class="filters"><select name="period">${[
      ["day", "每日总结"],
      ["week", "最近一周"],
      ["month", "月度总结"],
      ["year", "年度总结"],
    ]
      .map(
        ([v, l]) =>
          `<option value="${v}" ${v === period ? "selected" : ""}>${l}</option>`,
      )
      .join(
        "",
      )}</select><input type="date" name="end" value="${end}"><button>查看</button><a href="#reviews">查看已保存复盘 →</a></form><div class="notice">${start} 至 ${end}：${s.total} 条记录，完成 ${s.completed_tasks} 项任务。涉及人物：${s.people.map((v) => esc(v.name)).join("、") || "无"}。</div><div class="columns"><section class="panel"><h2>写下你的复盘</h2><form id="review-form"><div class="form-grid">${["summary", "learning", "problems", "plan"].map((k) => field(k, "")).join("")}</div><div class="actions"><button class="primary">保存复盘</button><button type="button" id="ai-review">AI 生成总结</button></div></form></section><div><section class="panel"><h2>分类与主要标签</h2>${horizontal(s.categories)}${horizontal(s.tags)}</section><section class="panel"><h2>活跃项目</h2>${horizontal(s.projects)}</section><section class="panel"><h2>重要事件</h2>${s.events.map((v) => `<p>${esc(v.date)} · ${esc(v.title)}</p>`).join("") || '<p class="muted">暂无</p>'}</section></div></div>`;
  $("#review-range").onsubmit = (e) => {
    e.preventDefault();
    currentFilters = Object.fromEntries(new FormData(e.currentTarget));
    reviewPage();
  };
  $("#review-form").onsubmit = busy(async (e) => {
    e.preventDefault();
    await api("/api/reviews", {
      method: "POST",
      body: {
        ...Object.fromEntries(new FormData(e.currentTarget)),
        date: end,
        period,
        review_type: period,
      },
    });
    toast("复盘已保存");
  });
  $("#ai-review").onclick = busy(async () => {
    const draft = await api("/api/ai/daily", { method: "POST", body: { date: end } });
    location.hash = "ai-draft/" + draft.uuid;
  });
}
async function searchPage(query) {
  const r = await api(
    "/api/global-search?q=" + encodeURIComponent(query) + "&page=" + page,
  );
  $("#main").innerHTML =
    heading("全局搜索", `“${query}” · 按资产类型查找过去的记忆。`) +
    Object.entries(r)
      .filter(([, v]) => v.total)
      .map(
        ([kind, v]) =>
          `<section class="panel"><h2>${kinds[kind]} <span class="pill">${v.total}</span></h2>${v.items.map((x) => (x.item_type ? itemRow(x) : `<div class="record"><a href="#entity/${kind}/${x.uuid}">${esc(x.name || x.title || x.conversation_title)}</a></div>`)).join("")}</section>`,
      )
      .join("") +
    (Object.values(r).some((v) => v.total)
      ? `<div class="pagination"><button id="search-prev" ${page === 1 ? "disabled" : ""}>上一页</button><span>第 ${page} 页 · 每类 20 项</span><button id="search-next" ${!Object.values(r).some((v) => v.total > page * 20) ? "disabled" : ""}>下一页</button></div>`
      : empty("没有找到匹配内容。"));
  if ($("#search-prev"))
    $("#search-prev").onclick = () => {
      page--;
      searchPage(query);
    };
  if ($("#search-next"))
    $("#search-next").onclick = () => {
      page++;
      searchPage(query);
    };
}
async function exportPage() {
  await loadRefs();
  $("#main").innerHTML =
    heading(
      "导出与 AI 数据中心",
      "你的数据始终属于你。选择范围，生成可长期读取的开放文件。",
    ) +
    `<div class="columns"><section class="panel"><h2>生成 AI 上下文包 / 导出记录</h2><form id="export-form"><div class="form-grid"><label>导出格式<select name="format"><option value="context">AI Context Pack · ZIP</option><option value="json">JSON · ZIP</option><option value="jsonl">JSONL · ZIP</option><option value="csv">CSV · ZIP</option><option value="markdown">Markdown · ZIP</option><option value="txt">TXT · ZIP</option></select></label><label>内容类型<select name="item_type"><option value="">全部知识资产</option>${["entries", ...assets, "inbox", "conversations"].map((k) => `<option value="${k}">${kinds[k]}</option>`).join("")}</select></label><label>某一年<input name="year" type="number" placeholder="例如 2026"></label><label>某个月<input name="month" type="month"></label><label>起始日期<input name="start" type="date"></label><label>结束日期<input name="end" type="date"></label><label>项目<select name="project"><option value="">全部项目</option>${optionList(refs.projects, null)}</select></label><label>人物<select name="person"><option value="">全部人物</option>${optionList(refs.people, null)}</select></label><label>标签<input name="tag"></label><label>分类<input name="category"></label><label><input name="include_content" type="checkbox" checked> 包含原始正文</label><label><input name="include_summary" type="checkbox" checked> 包含摘要</label><label><input name="include_attachments" type="checkbox" checked> 包含附件名称与说明</label><label><input name="include_tasks" type="checkbox" checked> 包含任务</label><label><input name="include_events" type="checkbox" checked> 包含事件</label><label><input name="include_sensitive" type="checkbox"> 包含敏感数据（默认排除）</label></div><div class="warning">生成的 ZIP 含 README_AI、schema、manifest。资料不会自动发送到云端。上传给 AI 前，请检查正文中手写的敏感信息。</div><button class="primary">生成并下载</button><div id="export-result"></div></form></section><div><section class="panel"><h2>完整数据库迁移</h2><p class="muted">保留所有实体、关系、历史版本和回收站，不应用 AI 隐私过滤。</p><div class="actions"><button data-full-export="full-json">完整关系 JSON</button><button data-full-export="db">SQLite 文件</button><button data-full-export="sql">SQL 备份</button></div><p class="muted">附件二进制请用“备份中心”的完整 ZIP 携带。</p></section><section class="panel"><h2>面向长期理解</h2><p>区分事实、观点、引用资料和 AI 生成内容。知识成熟度、可信度、验证与复用次数随包保留。</p><p>保留 UUID，未来可接入 Embedding、RAG 或 MCP。</p><a href="#settings">前往备份与恢复 →</a></section></div></div>`;
  $("#export-form").onsubmit = busy(async (e) => {
    e.preventDefault();
    const f = e.currentTarget,
      d = Object.fromEntries(new FormData(f)),
      filters = {};
    for (const k of [
      "start",
      "end",
      "project",
      "person",
      "tag",
      "category",
      "item_type",
    ])
      if (d[k]) filters[k] = d[k];
    if (d.year) {
      filters.start = d.year + "-01-01";
      filters.end = d.year + "-12-31";
    }
    if (d.month) {
      const [y, m] = d.month.split("-").map(Number);
      filters.start = d.month + "-01";
      filters.end = d.month + "-" + new Date(y, m, 0).getDate();
    }
    const options = { ai: true };
    for (const k of [
      "include_content",
      "include_summary",
      "include_attachments",
      "include_tasks",
      "include_events",
      "include_sensitive",
    ])
      options[k] = f.elements[k].checked;
    const b = f.querySelector("button");
    b.disabled = true;
    try {
      const r = await api("/api/export", {
        method: "POST",
        body: { format: d.format, filters, options },
      });
      $("#export-result").innerHTML =
        `<p><a href="${esc(r.url)}" download>下载 ${esc(r.name)}</a></p>`;
      download(r.url);
      toast("导出已生成，保存在 exports 文件夹");
    } finally {
      b.disabled = false;
    }
  });
  $$("[data-full-export]").forEach(
    (b) =>
      (b.onclick = busy(async () => {
        const r = await api("/api/export", {
          method: "POST",
          body: { format: b.dataset.fullExport },
        });
        download(r.url);
      })),
  );
}
function download(url) {
  const a = document.createElement("a");
  a.href = url;
  a.download = "";
  document.body.appendChild(a);
  a.click();
  a.remove();
}
async function importPage() {
  $("#main").innerHTML =
    heading("数据导入中心", "先预览、检查重复，再确认写入。支持 UTF-8 文件。") +
    `<section class="panel"><form id="import-form"><label>选择 CSV / JSON / JSONL / Markdown / TXT<input name="file" type="file" required accept=".csv,.json,.jsonl,.md,.txt"></label><p class="muted">单次最多 5000 条、20 MB。Markdown/TXT 文件作为一条原文导入。知识资产 JSON/JSONL 保留类型及结构化字段。</p><label>字段映射 · 原字段名 → 系统字段名（JSON，可留空）<textarea name="mapping" rows="2" placeholder='{"标题":"title","正文":"content","日期":"date"}'>{}</textarea></label><p class="muted">常用字段：title、content、date、category、tags、uuid、summary、importance、privacy_level。可从本系统导出的记录 JSON/JSONL/CSV 直接导入。</p><button class="primary">解析并预览</button></form></section><div id="import-preview"></div>`;
  $("#import-form").onsubmit = busy(async (e) => {
    e.preventDefault();
    const r = await api("/api/import/preview", {
      method: "POST",
      body: new FormData(e.currentTarget),
    });
    $("#import-preview").innerHTML =
      `<section class="panel"><h2>预览 · ${r.count} 条，重复 ${r.duplicates} 条，无效 ${r.errors.length} 条</h2><p class="muted">文件字段：${r.fields.map(esc).join("、")}</p>${r.errors.map((v) => `<p class="danger">第 ${v.line} 条：${esc(v.message)}</p>`).join("")}<table class="data-table"><thead><tr><th>日期</th><th>标题</th><th>冲突检测</th></tr></thead><tbody>${r.preview.map((v) => `<tr><td>${esc(v.date)}</td><td>${esc(v.title)}</td><td>${v.duplicate ? "重复" : "新记录"}</td></tr>`).join("")}</tbody></table><p class="muted">最多展示前 50 条。匹配依据：永久 UUID 或日期、标题、正文内容哈希。</p><label>重复时<select id="import-strategy"><option value="skip">跳过</option><option value="overwrite">覆盖（保留永久 UUID）</option><option value="new">作为新记录导入</option></select></label><button id="confirm-import" class="primary" ${r.errors.length ? "disabled" : ""}>确认导入 ${r.valid_count} 条</button></section>`;
    $("#confirm-import").onclick = busy(async () => {
      const result = await api("/api/import/confirm", {
        method: "POST",
        body: { token: r.token, strategy: $("#import-strategy").value },
      });
      toast(`导入 ${result.imported} 条，跳过 ${result.skipped} 条`);
      $("#import-preview").innerHTML =
        '<div class="notice">导入完成，可前往记录或知识库查看。</div>';
    });
  });
}
async function settingsPage(welcome = false) {
  const sys = await api("/api/system");
  cfg = await api("/api/settings");
  $("#main").innerHTML =
    heading(
      welcome ? "欢迎使用个人知识与数字记忆数据库" : "设置与数据管理",
      welcome
        ? "只需简单设置，开始积累属于自己的长期知识。"
        : "本地保存、开放格式、可验证备份。",
    ) +
    `<div class="columns"><div><section class="panel"><h2>基本设置</h2><form id="settings-form"><div class="form-grid"><label>数据库名称<input name="name" value="${esc(cfg.name)}" required></label><label>用户昵称（可跳过）<input name="nickname" value="${esc(cfg.nickname)}"></label><label>附件目录<input name="attachment_dir" value="${esc(cfg.attachment_dir)}"></label><label>备份目录<input name="backup_dir" value="${esc(cfg.backup_dir)}"></label><label>自动备份${selectField(
      "auto_backup",
      cfg.auto_backup,
      [
        [0, "关闭"],
        [1, "每天"],
        [3, "每 3 天"],
        [7, "每周"],
      ],
    )}</label><label>保留自动备份${selectField("retention", cfg.retention, [
      [30, "30 个"],
      [60, "60 个"],
      [100, "100 个"],
    ])}</label><label>界面皮肤<button type="button" id="settings-appearance">🍁 选择皮肤与角色插画</button></label><label>日期格式${selectField("date_format", cfg.date_format, [
      ["YYYY-MM-DD", "2026-10-03"],
      ["YYYY/MM/DD", "2026/10/03"],
    ])}</label></div><p class="muted">自动备份在程序启动时检查。已有附件时，为保护数据不允许直接更换目录。</p><button class="primary">${welcome ? "完成设置，开始使用" : "保存设置"}</button></form></section><section class="panel"><h2>备份与恢复</h2><div class="actions"><button class="primary" id="backup-now">一键备份</button><button id="check-db">检查数据库</button><label>导入另一台电脑的备份<input id="upload-backup" type="file" accept=".zip"></label></div><p class="muted">备份包括数据库、附件和配置。恢复前会自动保存当前数据库。手动备份与恢复前备份不会被自动清理。</p><div class="wrap"><table class="data-table"><thead><tr><th>备份文件</th><th>大小</th><th>操作</th></tr></thead><tbody>${sys.backups
      .slice(0, 30)
      .map(
        (b) =>
          `<tr><td><a href="/api/backups/${encodeURIComponent(b.name)}">${esc(b.name)}</a></td><td>${(b.size / 1024 / 1024).toFixed(2)} MB</td><td><button class="small" data-restore-backup="${esc(b.name)}">恢复</button></td></tr>`,
      )
      .join(
        "",
      )}</tbody></table></div><div id="integrity-result"></div></section><section class="panel"><h2>数据与扩展</h2><div class="section-links"><a href="#export">导出 / AI Context Pack</a><a href="#import">数据导入</a><a href="#tags">标签管理</a><a href="#custom-fields">自定义字段定义</a><a href="#conversations">AI 对话</a><a href="#events">重要事件</a><a href="#domains">领域</a><a href="#topics">主题</a></div><div class="actions"><button id="audit-button">操作日志</button><button id="schema-button">查看数据库结构</button></div></section></div><div><section class="panel"><h2>系统信息</h2><p>软件版本：${sys.version}<br>数据库版本：${esc(sys.database_version)}<br>数据库大小：${(sys.database_size / 1024 / 1024).toFixed(2)} MB<br>附件大小：${(sys.attachment_size / 1024 / 1024).toFixed(2)} MB</p><p class="field-text">数据库路径<br>${esc(sys.database_path)}<br><br>附件路径<br>${esc(sys.attachment_path)}</p><p>内容 ${sys.counts.entries} · 项目 ${sys.counts.projects} · 人物 ${sys.counts.people} · 任务 ${sys.counts.tasks} · 附件 ${sys.counts.attachments}</p><p class="muted">最近备份：${esc(sys.backups[0]?.time ? fmtDate(sys.backups[0].time) : "暂无")}</p></section><section class="panel"><h2>AI 服务</h2><span class="pill">${esc(sys.ai_status)}</span><p class="muted">本地模型整理原始记录，生成待审核草稿。你修改并确认后归档。</p><div class="section-links"><a href="#ai-workflow">每日整理与审核 →</a><a href="#ai-settings">AI 与模型设置 →</a></div></section><div class="hero-note"><strong>让数据陪伴你，而不是绑定你</strong>迁移新电脑：携带程序文件夹和实际数据目录，或使用完整 ZIP 备份恢复。</div></div></div>`;
  $("#settings-appearance").onclick = () => window.KnowledgeAppearance?.open();
  $("#settings-form").onsubmit = busy(async (e) => {
    e.preventDefault();
    const d = Object.fromEntries(new FormData(e.currentTarget));
    d.auto_backup = Number(d.auto_backup);
    d.retention = Number(d.retention);
    d.setup_done = true;
    cfg = await api("/api/settings", { method: "PUT", body: d });
    applyTheme();
    toast("设置已保存");
    if (welcome) location.hash = "dashboard";
  });
  $("#backup-now").onclick = busy(async () => {
    await api("/api/backups", { method: "POST" });
    toast("完整备份已创建");
    settingsPage();
  });
  $("#check-db").onclick = busy(async () => {
    const r = await api("/api/integrity", { method: "POST" });
    $("#integrity-result").innerHTML =
      `<div class="notice">${r.ok ? "数据库、外键、索引和附件检查通过" : "发现需要处理的问题"}</div><details><summary>查看检查详情</summary><pre>${esc(JSON.stringify(r, null, 2))}</pre></details>`;
  });
  $$("[data-restore-backup]").forEach(
    (b) =>
      (b.onclick = busy(async () => {
        if (
          prompt(
            "将恢复到这份备份。系统会先备份当前数据。请输入“恢复备份”确认",
          ) !== "恢复备份"
        )
          return;
        const r = await api("/api/backup-restore", {
          method: "POST",
          body: { name: b.dataset.restoreBackup, confirmation: "恢复备份" },
        });
        toast("恢复完成；当前数据已另存为 " + r.safety_backup);
        await loadRefs();
        settingsPage();
      })),
  );
  $("#upload-backup").onchange = busy(async (e) => {
    const fd = new FormData();
    fd.append("file", e.target.files[0]);
    await api("/api/backup-upload", { method: "POST", body: fd });
    toast("备份已验证并加入列表，点击恢复即可使用");
    settingsPage();
  });
  $("#audit-button").onclick = busy(async () => {
    const rows = await api("/api/audit");
    modal(
      "最近 100 条操作日志",
      rows
        .map(
          (r) =>
            `<p>${esc(fmtDate(r.created_at))} · ${esc(r.action)} · ${esc(r.entity)}<br><small>${esc(r.object_uuid)} ${esc(r.description)}</small></p>`,
        )
        .join("") || "暂无日志",
    );
  });
  $("#schema-button").onclick = busy(async () => {
    const r = await api("/api/schema");
    modal("数据库结构", `<pre>${esc(r.markdown)}</pre>`);
  });
}
function applyTheme() {
  if (window.KnowledgeAppearance) {
    window.KnowledgeAppearance.apply();
    return;
  }
  document.body.classList.toggle(
    "dark",
    cfg.theme === "dark" ||
      (cfg.theme === "system" &&
        matchMedia("(prefers-color-scheme:dark)").matches),
  );
}
function setupNav() {
  const groups = [
    [
      "我的空间",
      [
        ["dashboard", "总览"],
        ["quick", "随手记与草稿"],
        ["inbox", "收集箱"],
        ["speech-capture", "语音随手记"],
        ["entries", "全部记录"],
        ["ai-workflow", "每日整理与审核"],
        ["semantic-search", "语义搜索"],
        ["knowledge-answer", "问我的资料"],
        ["period-review", "周月复盘"],
        ["favorite", "收藏夹"],
      ],
    ],
    [
      "知识与经验",
      [
        ["knowledge", "知识库"],
        ["cases", "案例库"],
        ["problems", "问题与解决方案"],
        ["solutions", "解决方案库"],
        ["experiences", "经验库"],
        ["learning", "学习记录"],
        ["sources", "来源资料"],
        ["map", "知识地图"],
      ],
    ],
    [
      "工作与生活",
      [
        ["projects", "项目档案"],
        ["tasks", "待办任务"],
        ["people", "人物关系"],
        ["timeline", "时间线"],
        ["calendar", "日历"],
        ["review", "总结与复盘"],
      ],
    ],
    [
      "数据空间",
      [
        ["stats", "统计中心"],
        ["export", "AI 数据导出"],
        ["ai-settings", "AI 与模型设置"],
        ["import", "数据导入"],
        ["trash", "回收站"],
        ["settings", "设置与备份"],
      ],
    ],
  ];
  $("#nav").innerHTML = groups
    .map(
      ([g, items]) =>
        `<div class="group">${g}</div>${items.map(([k, l]) => `<a href="#${k}" data-nav="${k}"><b>${icons[k] || "·"}</b>${l}</a>`).join("")}`,
    )
    .join("");
}
async function route() {
  if (typeof captureBeforeLeave === "function" && !(await captureBeforeLeave())) return;
  const serial = ++routeSerial;
  let hash = location.hash.slice(1) || "dashboard";
  const [path, query] = hash.split("?"),
    parts = path.split("/"),
    view = parts[0];
  if (route.lastPath !== hash) {
    page = 1;
    currentFilters = Object.fromEntries(new URLSearchParams(query || ""));
    route.lastPath = hash;
  }
  $$("[data-nav]").forEach((a) =>
    a.classList.toggle("active", a.dataset.nav === view),
  );
  $("#breadcrumb").textContent =
    "我的空间 / " +
    (kinds[view] ||
      {
        dashboard: "总览",
        settings: "设置",
        export: "AI 数据中心",
        item: "资料详情",
      }[view] ||
      "知识空间");
  try {
    if (view === "dashboard") await dashboard();
    else if (view === "quick") await captureWorkspacePage(parts[1]);
    else if (
      ["entries", "inbox", "favorite", "trash", ...assets].includes(view)
    )
      await listPage(view);
    else if (view === "item") await itemDetail(parts[1]);
    else if (view === "ai-workflow") await aiWorkflowPage();
    else if (view === "ai-draft") await draftDetailPage(parts[1]);
    else if (view === "ai-settings") await AISettingsPage();
    else if (view === "knowledge-answer") await knowledgeAnswerPage();
    else if (view === "period-review") await periodReviewPage();
    else if (view === "speech-capture") await speechCapturePage();
    else if (view === "semantic-search") await semanticSearchPage(currentFilters.q || "");
    else if (view === "entity") await entityDetail(parts[1], parts[2]);
    else if (entityFields[view]) await entityList(view);
    else if (view === "calendar") await calendarPage();
    else if (view === "timeline") await timelinePage();
    else if (view === "stats") await statsPage();
    else if (view === "review") await reviewPage();
    else if (view === "search") await searchPage(currentFilters.q || "");
    else if (view === "map") await knowledgeMap();
    else if (view === "export") await exportPage();
    else if (view === "import") await importPage();
    else if (view === "settings" || view === "welcome")
      await settingsPage(view === "welcome");
    else location.hash = "dashboard";
    if (typeof attachLookups === "function") attachLookups($("#main"));
  } catch (err) {
    if (serial === routeSerial && (location.hash.slice(1) || "dashboard") === hash && !err.stalePage) {
      $("#main").innerHTML =
        heading("页面暂时无法加载", err.message) +
        '<button id="retry">重试</button>';
      $("#retry").onclick = route;
      toast(err.message);
    }
  }
}
async function boot() {
  const s = await api("/api/session");
  token = s.token;
  cfg = s.settings;
  applyTheme();
  setupNav();
  await loadRefs();
  $("#quick-button").onclick = quickCollect;
  $("#global-form").onsubmit = (e) => {
    e.preventDefault();
    location.hash = "search?q=" + encodeURIComponent($("#global-query").value);
  };
  window.addEventListener("hashchange", route);
  matchMedia("(prefers-color-scheme:dark)").addEventListener(
    "change",
    applyTheme,
  );
  if (!cfg.setup_done) location.hash = "welcome";
  await route();
}
boot().catch((err) => {
  $("#main").textContent = "无法连接本地数据库：" + err.message;
});

function projectReview(uid) {
  const fields = [
    "summary",
    "what_went_well",
    "what_went_wrong",
    "lessons",
    "next_action",
  ];
  modal(
    "项目复盘",
    `<form id="project-review-form"><div class="form-grid">${fields.map((k) => field(k, "")).join("")}</div><button class="primary">保存项目复盘</button></form>`,
  );
  $("#project-review-form").onsubmit = busy(async (e) => {
    e.preventDefault();
    await api(`/api/projects/${uid}/review`, {
      method: "POST",
      body: Object.fromEntries(new FormData(e.currentTarget)),
    });
    $("#dialog").close();
    toast("项目复盘已保存");
  });
}

const originLabels = {
  manual: "手动录入",
  import: "文件导入",
  ai: "AI生成",
  web: "网页资料",
  file: "文件资料",
  conversation: "AI对话",
  api: "程序接口",
  migration: "数据迁移",
};
const natureLabels = {
  fact: "事实",
  opinion: "观点",
  hypothesis: "假设",
  experience: "实践经验",
  reference: "引用资料",
  ai_generated: "AI生成内容",
  unknown: "尚未判断",
};
function detailValue(key, value) {
  if (typeof value === "boolean") return value ? "是" : "否";
  if (key === "confidence")
    return (
      {
        unknown: "未知",
        low: "低",
        medium: "中",
        high: "高",
        verified: "已验证",
      }[value] || value
    );
  if (key === "maturity_level")
    return (
      [
        "",
        "1 · 刚收集",
        "2 · 已阅读",
        "3 · 已理解",
        "4 · 已实践",
        "5 · 已验证",
        "6 · 已多次复用",
      ][value] || value
    );
  if (key.endsWith("_at")) return fmtDate(value);
  return value;
}
