"use strict";
// Bounded initial lists remain quick. Remote lookup makes older items reachable.
function attachLookups(root = document) {
  const mapping = {
    projects: "projects",
    people: "people",
    project: "projects",
    person: "people",
    project_id: "projects",
    person_id: "people",
    domain_id: "domains",
    topic_id: "topics",
  };
  root.querySelectorAll("select[name]").forEach((select) => {
    const key = select.name.replace("detail.", "");
    const kind =
      select.dataset.referenceKind ||
      mapping[key] ||
      (key === "parent_id" && location.hash.includes("domains")
        ? "domains"
        : key === "parent_id"
          ? "topics"
          : null);
    if (!kind || select.dataset.lookup) return;
    select.dataset.lookup = "true";
    const input = document.createElement("input");
    input.type = "search";
    input.placeholder = "输入关键词查找更多" + (kinds[kind] || "资料");
    input.className = "lookup";
    input.setAttribute("aria-label", "查找" + kinds[kind]);
    select.before(input);
    let timer;
    input.addEventListener("input", () => {
      clearTimeout(timer);
      timer = setTimeout(async () => {
        try {
          const data = await api(
            "/api/" + kind + "?size=50&q=" + encodeURIComponent(input.value),
          );
          const selected = [...select.selectedOptions]
            .filter((o) => o.value)
            .map((o) => ({ value: o.value, text: o.textContent }));
          const useId = key.endsWith("_id");
          const values = new Set(selected.map((v) => v.value));
          select.innerHTML =
            (!select.multiple
              ? '<option value="">未选择 / 全部</option>'
              : "") +
            selected
              .map(
                (v) =>
                  `<option value="${esc(v.value)}" selected>${esc(v.text)}</option>`,
              )
              .join("") +
            data.items
              .filter((v) => !values.has(String(v[useId ? "id" : "uuid"])))
              .map(
                (v) =>
                  `<option value="${esc(v[useId ? "id" : "uuid"])}">${esc(v.name)}</option>`,
              )
              .join("");
          refs[kind] = [
            ...(refs[kind] || []),
            ...data.items.filter(
              (v) => !(refs[kind] || []).some((old) => old.uuid === v.uuid),
            ),
          ].slice(-1000);
        } catch (err) {
          toast(err.message);
        }
      }, 250);
    });
  });
}

// Load a branch only when expanded; page through sibling directories and items.
async function knowledgeMap() {
  $("#main").innerHTML =
    heading(
      "知识地图",
      "按领域与主题逐层浏览，每个目录按需加载。",
      '<div class="actions"><button id="new-domain">＋ 领域</button><button id="new-topic">＋ 主题</button></div>',
    ) +
    '<section class="panel tree"><div id="map-roots"></div><div class="actions"><a href="#domains">管理领域</a><a href="#topics">管理主题</a></div></section>';
  $("#new-domain").onclick = () => editEntity("domains");
  $("#new-topic").onclick = () => editEntity("topics");
  async function directory(
    container,
    kind,
    parent = null,
    domain = null,
    branchPage = 1,
  ) {
    const filters = { parent_id: parent ?? "null", page: branchPage, size: 50 };
    if (kind === "topics") filters.domain_id = domain ?? "null";
    const result = await api(
      "/api/" + kind + "?" + new URLSearchParams(filters),
    );
    container.innerHTML =
      result.items
        .map(
          (node) =>
            `<details data-node="${node.id}" data-kind="${kind}"><summary>${esc(node.name)} <a href="#entries?${kind === "domains" ? "domain_id" : "topic_id"}=${node.id}">浏览所有资料 →</a></summary><div class="map-children"></div></details>`,
        )
        .join("") +
      (result.total > 50
        ? `<div class="actions"><button class="small" data-map-prev ${branchPage === 1 ? "disabled" : ""}>上一页目录</button><span class="muted">第 ${branchPage} 页 / ${result.total} 项</span><button class="small" data-map-next ${branchPage * 50 >= result.total ? "disabled" : ""}>下一页目录</button></div>`
        : "");
    if (!result.total && parent === null)
      container.innerHTML =
        '<p class="muted">暂无目录。可创建领域与主题，或将资料归入已有目录。</p>';
    for (const detail of container.querySelectorAll(":scope > details"))
      detail.addEventListener("toggle", async () => {
        if (!detail.open || detail.dataset.loaded) return;
        detail.dataset.loaded = "true";
        const id = Number(detail.dataset.node),
          children = detail.querySelector(".map-children");
        children.innerHTML =
          '<div class="domain-children"></div><div class="topic-children"></div><div class="item-children"></div>';
        try {
          if (kind === "domains")
            await directory(
              children.querySelector(".domain-children"),
              "domains",
              id,
            );
          await directory(
            children.querySelector(".topic-children"),
            "topics",
            kind === "topics" ? id : null,
            kind === "domains" ? id : domain,
          );
          const itemFilter =
            kind === "domains" ? { domain_id: id } : { topic_id: id };
          const items = await api(
            "/api/entries?" + new URLSearchParams({ ...itemFilter, size: 10 }),
          );
          children.querySelector(".item-children").innerHTML =
            items.items
              .map(
                (x) =>
                  `<p><a href="#item/${x.uuid}">${esc(kinds[x.item_type])} · ${esc(x.title)}</a></p>`,
              )
              .join("") +
            (items.total > 10
              ? `<a href="#entries?${new URLSearchParams(itemFilter)}">查看全部 ${items.total} 项 →</a>`
              : "");
        } catch (err) {
          children.textContent = err.message;
          delete detail.dataset.loaded;
        }
      });
    const prev = container.querySelector("[data-map-prev]"),
      next = container.querySelector("[data-map-next]");
    if (prev)
      prev.onclick = () =>
        directory(container, kind, parent, domain, branchPage - 1);
    if (next)
      next.onclick = () =>
        directory(container, kind, parent, domain, branchPage + 1);
  }
  await directory($("#map-roots"), "domains");
}
