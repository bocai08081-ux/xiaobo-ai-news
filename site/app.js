const CATEGORIES = [
  { id: "latest", label: "最新" },
  { id: "hn", label: "社区" },
  { id: "papers", label: "论文" },
  { id: "models", label: "模型" },
  { id: "github", label: "开源" },
  { id: "video", label: "视频" },
  { id: "media", label: "媒体" },
  { id: "official", label: "官方" },
  { id: "cn", label: "中文" },
  { id: "local", label: "本地部署" },
];

const CATEGORY_LABEL = Object.fromEntries(CATEGORIES.map((cat) => [cat.id, cat.label]));

const chipsEl = document.querySelector("#chips");
const listEl = document.querySelector("#list");
const updatedEl = document.querySelector("#updated");
const countEl = document.querySelector("#count");
const footEl = document.querySelector("#foot");
const searchEl = document.querySelector("#q");

let data = { items: [], sources: [], generatedAt: null, windowHours: 72 };
let active = "latest";

function safeUrl(url) {
  try {
    const parsed = new URL(url);
    if (parsed.protocol === "https:" || parsed.protocol === "http:") return parsed.href;
  } catch {
    return null;
  }
  return null;
}

function shanghaiStamp(iso) {
  if (!iso) return data.generatedAtShanghai || "";
  try {
    const formatted = new Intl.DateTimeFormat("zh-CN", {
      timeZone: "Asia/Shanghai",
      year: "numeric",
      month: "long",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
      hourCycle: "h23",
    }).format(new Date(iso));
    return `${formatted}（上海）`;
  } catch {
    return data.generatedAtShanghai ? `${data.generatedAtShanghai}（上海）` : "";
  }
}

function relativeTime(iso, snapshot) {
  if (snapshot) return "当前热榜";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "";
  const minutes = Math.round((Date.now() - then) / 60000);
  if (minutes < 1) return "刚刚";
  if (minutes < 60) return `${minutes} 分钟前`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} 小时前`;
  const days = Math.round(hours / 24);
  if (days < 7) return `${days} 天前`;
  return shanghaiStamp(iso).replace("（上海）", "");
}

function matchesCategory(item) {
  if (active === "latest") return !item.snapshot;
  return item.category === active;
}

function matchesQuery(item, query) {
  if (!query) return true;
  const haystack = `${item.title} ${item.source} ${item.summaryZh || ""} ${item.summary || ""} ${item.meta || ""}`.toLowerCase();
  return haystack.includes(query);
}

function visibleItems() {
  const query = searchEl.value.trim().toLowerCase();
  return data.items.filter((item) => matchesCategory(item) && matchesQuery(item, query));
}

function renderChips() {
  chipsEl.replaceChildren();
  for (const cat of CATEGORIES) {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = cat.label;
    button.setAttribute("role", "tab");
    button.setAttribute("aria-selected", cat.id === active ? "true" : "false");
    button.addEventListener("click", () => {
      active = cat.id;
      const target = cat.id === "latest" ? location.pathname + location.search : `#${cat.id}`;
      history.replaceState(null, "", target);
      render();
    });
    chipsEl.appendChild(button);
  }
}

function renderList() {
  const items = visibleItems();
  countEl.textContent = `${items.length} 条`;
  listEl.replaceChildren();
  if (!items.length) {
    const empty = document.createElement("p");
    empty.className = "empty";
    const label = CATEGORY_LABEL[active] || "该分类";
    empty.textContent = active === "latest"
      ? `过去 ${data.windowHours || 72} 小时里没有匹配的新内容。`
      : active === "models" || active === "github"
        ? `${label}暂时没有条目。热榜来源可能暂不可用。`
        : `${label}在过去 ${data.windowHours || 72} 小时里没有新内容。`;
    listEl.appendChild(empty);
    return;
  }
  for (const item of items) {
    const href = safeUrl(item.url);
    if (!href) continue;
    const article = document.createElement("article");
    article.className = "item";
    const link = document.createElement("a");
    link.href = href;
    link.target = "_blank";
    link.rel = "noopener noreferrer";

    const kicker = document.createElement("p");
    kicker.className = "item-kicker";
    const catLabel = CATEGORY_LABEL[item.category] || item.category;
    kicker.textContent = item.snapshot ? `${item.source} · ${catLabel} · 热榜` : `${item.source} · ${catLabel}`;

    const title = document.createElement("h2");
    title.textContent = item.title;

    link.append(kicker, title);
    const blurb = item.summaryZh || item.summary;
    if (blurb) {
      const excerpt = document.createElement("p");
      excerpt.className = "excerpt";
      excerpt.textContent = blurb;
      link.appendChild(excerpt);
    }
    const meta = document.createElement("p");
    meta.className = "meta";
    const bits = [relativeTime(item.publishedAt, item.snapshot)];
    if (item.meta) bits.push(item.meta);
    meta.textContent = bits.filter(Boolean).join(" · ");
    link.appendChild(meta);
    article.appendChild(link);
    listEl.appendChild(article);
  }
}

function renderFooter() {
  const sources = data.sources || [];
  const failed = sources.filter((source) => !source.ok);
  const okCount = sources.length - failed.length;
  footEl.replaceChildren();
  if (!sources.length) return;
  const details = document.createElement("details");
  const summary = document.createElement("summary");
  summary.textContent = failed.length
    ? `${okCount} 个来源正常 · ${failed.length} 个暂不可用`
    : `${okCount} 个来源正常`;
  details.appendChild(summary);
  const list = document.createElement("ul");
  for (const source of sources) {
    const li = document.createElement("li");
    li.textContent = source.ok
      ? `${source.name}：${source.count} 条`
      : `${source.name}：${source.error || "失败"}`;
    if (!source.ok) li.className = "bad";
    list.appendChild(li);
  }
  details.appendChild(list);
  const note = document.createElement("p");
  note.textContent = "「最新」是过去 72 小时的报道、论文、视频和版本。模型与开源是当前热榜，不混进时间线。";
  footEl.append(details, note);
}

function render() {
  updatedEl.textContent = data.generatedAt
    ? `更新于 ${shanghaiStamp(data.generatedAt)}`
    : "还没有数据";
  renderChips();
  renderList();
  renderFooter();
}

function showError(message) {
  updatedEl.textContent = "暂时没有读到数据";
  listEl.replaceChildren();
  const box = document.createElement("p");
  box.className = "error-box";
  box.textContent = message;
  listEl.appendChild(box);
}

async function load() {
  const hash = location.hash.replace("#", "");
  if (CATEGORIES.some((cat) => cat.id === hash)) active = hash;
  try {
    const response = await fetch("data/news.json", { cache: "no-cache" });
    if (!response.ok) throw new Error(String(response.status));
    data = await response.json();
    render();
  } catch {
    showError("资讯数据还没生成，或当前无法读取 data/news.json。");
  }
}

searchEl.addEventListener("input", () => renderList());
window.addEventListener("hashchange", () => {
  const hash = location.hash.replace("#", "");
  active = CATEGORIES.some((cat) => cat.id === hash) ? hash : "latest";
  render();
});

load();
