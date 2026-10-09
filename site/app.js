const CATEGORIES = [
  { id: "latest", label: "最新" },
  { id: "saved", label: "收藏" },
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
const SAVE_KEY = "xiaobo-saved-v1";
const ZH_KEY = "xiaobo-zh-only";

const chipsEl = document.querySelector("#chips");
const listEl = document.querySelector("#list");
const updatedEl = document.querySelector("#updated");
const countEl = document.querySelector("#count");
const footEl = document.querySelector("#foot");
const searchEl = document.querySelector("#q");
const zhBtn = document.querySelector("#zh-only");
const toastEl = document.querySelector("#toast");

let data = { items: [], sources: [], generatedAt: null, windowHours: 72 };
let active = "latest";
let savedItems = loadSaved();
let zhOnly = loadZhOnly();
let toastTimer = 0;

function safeUrl(url) {
  try {
    const parsed = new URL(url);
    if (parsed.protocol === "https:" || parsed.protocol === "http:") return parsed.href;
  } catch {
    return null;
  }
  return null;
}

function loadSaved() {
  try {
    const raw = JSON.parse(localStorage.getItem(SAVE_KEY) || "[]");
    if (!Array.isArray(raw)) return [];
    return raw.filter((item) => item && item.id && safeUrl(item.url));
  } catch {
    return [];
  }
}

function loadZhOnly() {
  try {
    return localStorage.getItem(ZH_KEY) === "1";
  } catch {
    return false;
  }
}

function persistSaved() {
  try {
    localStorage.setItem(SAVE_KEY, JSON.stringify(savedItems));
  } catch {
    /* private mode or a full quota still keeps the in-memory list */
  }
}

function persistZhOnly() {
  try {
    localStorage.setItem(ZH_KEY, zhOnly ? "1" : "0");
  } catch {
    /* ignore */
  }
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

function shanghaiDateKey(iso) {
  try {
    return new Intl.DateTimeFormat("en-CA", {
      timeZone: "Asia/Shanghai",
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
    }).format(new Date(iso));
  } catch {
    return "";
  }
}

function shiftDateKey(key, days) {
  const [year, month, day] = key.split("-").map(Number);
  if (!year || !month || !day) return "";
  const date = new Date(Date.UTC(year, month - 1, day));
  date.setUTCDate(date.getUTCDate() + days);
  return date.toISOString().slice(0, 10);
}

function dayLabel(iso) {
  const key = shanghaiDateKey(iso);
  if (!key) return "";
  const today = shanghaiDateKey(new Date().toISOString());
  if (key === today) return "今天";
  if (key === shiftDateKey(today, -1)) return "昨天";
  const [, month, day] = key.split("-");
  return `${Number(month)}月${Number(day)}日`;
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

function isChinese(item) {
  if (item.lang === "zh" || item.lang === "en") return item.lang === "zh";
  return /[\u4e00-\u9fff]/.test(item.title || "");
}

function matchesCategory(item) {
  if (active === "latest") return !item.snapshot;
  if (active === "saved") return false;
  return item.category === active;
}

function matchesQuery(item, query) {
  if (!query) return true;
  const haystack = `${item.title} ${item.source} ${item.summaryZh || ""} ${item.summary || ""} ${item.meta || ""}`.toLowerCase();
  return haystack.includes(query);
}

function matchesLang(item) {
  if (!zhOnly) return true;
  return isChinese(item);
}

function visibleItems() {
  const query = searchEl.value.trim().toLowerCase();
  const pool = active === "saved" ? savedItems : data.items.filter(matchesCategory);
  return pool.filter((item) => matchesQuery(item, query) && matchesLang(item));
}

function savedLabel() {
  return savedItems.length ? `收藏 ${savedItems.length}` : "收藏";
}

function renderChips() {
  chipsEl.replaceChildren();
  for (const cat of CATEGORIES) {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = cat.id === "saved" ? savedLabel() : cat.label;
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

function showToast(message) {
  toastEl.textContent = message;
  toastEl.classList.add("show");
  window.clearTimeout(toastTimer);
  toastTimer = window.setTimeout(() => {
    toastEl.classList.remove("show");
  }, 1600);
}

function briefText(item) {
  const lines = [`【选题】${item.title}`, `来源：${item.source}`];
  const blurb = item.summaryZh || item.summary;
  if (blurb) lines.push(blurb);
  lines.push(item.url);
  return lines.join("\n");
}

async function copyBrief(item, button) {
  const text = briefText(item);
  let ok = false;
  try {
    await navigator.clipboard.writeText(text);
    ok = true;
  } catch {
    const area = document.createElement("textarea");
    area.value = text;
    area.setAttribute("readonly", "");
    area.style.position = "fixed";
    area.style.left = "-9999px";
    document.body.appendChild(area);
    area.select();
    try {
      ok = document.execCommand("copy");
    } catch {
      ok = false;
    }
    area.remove();
  }
  if (!ok) {
    showToast("没有复制成功，请再试一次");
    return;
  }
  const previous = button.textContent;
  button.textContent = "已复制";
  showToast("已复制选题");
  window.setTimeout(() => {
    if (button.isConnected) button.textContent = previous;
  }, 1600);
}

function snapshotItem(item) {
  return {
    id: item.id,
    title: item.title,
    url: item.url,
    source: item.source,
    category: item.category,
    publishedAt: item.publishedAt,
    summary: item.summary || "",
    summaryZh: item.summaryZh || null,
    meta: item.meta || "",
    snapshot: Boolean(item.snapshot),
    lang: item.lang || (isChinese(item) ? "zh" : "en"),
    savedAt: new Date().toISOString(),
  };
}

function toggleSaved(item) {
  const index = savedItems.findIndex((saved) => saved.id === item.id);
  if (index >= 0) {
    savedItems.splice(index, 1);
    showToast("已取消收藏");
  } else {
    savedItems.unshift(snapshotItem(item));
    showToast("已收藏选题");
  }
  persistSaved();
  render();
}

function refreshSavedFromFeed() {
  if (!savedItems.length || !Array.isArray(data.items)) return;
  const byId = new Map(data.items.map((item) => [item.id, item]));
  let changed = false;
  savedItems = savedItems.map((saved) => {
    const fresh = byId.get(saved.id);
    if (!fresh) return saved;
    changed = true;
    return { ...snapshotItem(fresh), savedAt: saved.savedAt || fresh.publishedAt };
  });
  if (changed) persistSaved();
}

function shouldGroup(items) {
  if (active === "models" || active === "github" || active === "saved") return false;
  return items.some((item) => !item.snapshot);
}

function appendDay(label) {
  const heading = document.createElement("h3");
  heading.className = "day";
  heading.textContent = label;
  listEl.appendChild(heading);
}

function appendArticle(item) {
  const href = safeUrl(item.url);
  if (!href) return;
  const article = document.createElement("article");
  article.className = "item";
  const link = document.createElement("a");
  link.className = "item-link";
  link.href = href;
  link.target = "_blank";
  link.rel = "noopener noreferrer";

  const kicker = document.createElement("p");
  kicker.className = "item-kicker";
  const catLabel = CATEGORY_LABEL[item.category] || item.category;
  const bits = [item.source, catLabel];
  if (item.snapshot) bits.push("热榜");
  else if (isChinese(item) && item.category !== "cn") bits.push("中文");
  kicker.textContent = bits.join(" · ");

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
  const metaBits = [relativeTime(item.publishedAt, item.snapshot)];
  if (item.meta) metaBits.push(item.meta);
  meta.textContent = metaBits.filter(Boolean).join(" · ");
  link.appendChild(meta);

  const actions = document.createElement("div");
  actions.className = "item-actions";
  const copyBtn = document.createElement("button");
  copyBtn.type = "button";
  copyBtn.textContent = "复制选题";
  copyBtn.addEventListener("click", () => {
    copyBrief(item, copyBtn);
  });
  const saveBtn = document.createElement("button");
  saveBtn.type = "button";
  const saved = savedItems.some((row) => row.id === item.id);
  saveBtn.textContent = saved ? "已收藏" : "收藏";
  saveBtn.setAttribute("aria-pressed", saved ? "true" : "false");
  saveBtn.addEventListener("click", () => toggleSaved(item));
  actions.append(copyBtn, saveBtn);

  article.append(link, actions);
  listEl.appendChild(article);
}

function emptyMessage(query) {
  if (active === "saved") {
    if (!savedItems.length) return "还没有收藏。点条目上的「收藏」，选题会留在这台浏览器。";
    if (query && zhOnly) return "收藏里没有匹配的中文选题。";
    if (query) return "收藏里没有匹配的选题。";
    return "收藏里没有中文选题。";
  }
  const label = CATEGORY_LABEL[active] || "该分类";
  if (zhOnly && query) return "没有匹配的中文内容。";
  if (zhOnly) {
    return active === "latest"
      ? `过去 ${data.windowHours || 72} 小时里没有中文内容。`
      : `${label}里没有中文内容。`;
  }
  if (active === "latest") return `过去 ${data.windowHours || 72} 小时里没有匹配的新内容。`;
  if (active === "models" || active === "github") return `${label}暂时没有条目。热榜来源可能暂不可用。`;
  return `${label}在过去 ${data.windowHours || 72} 小时里没有新内容。`;
}

function renderList() {
  const items = visibleItems();
  const query = searchEl.value.trim();
  countEl.textContent = `${items.length} 条`;
  zhBtn.setAttribute("aria-pressed", zhOnly ? "true" : "false");
  listEl.replaceChildren();
  if (!items.length) {
    const empty = document.createElement("p");
    empty.className = "empty";
    empty.textContent = emptyMessage(query);
    listEl.appendChild(empty);
    return;
  }
  const group = shouldGroup(items);
  let lastDay = "";
  let openedHot = false;
  for (const item of items) {
    if (group && item.snapshot) {
      if (!openedHot) {
        appendDay("当前热榜");
        openedHot = true;
        lastDay = "";
      }
    } else if (group) {
      const label = dayLabel(item.publishedAt);
      if (label && label !== lastDay) {
        appendDay(label);
        lastDay = label;
      }
    }
    appendArticle(item);
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
  note.textContent = "「最新」按上海时间分天，只含过去 72 小时的报道、论文、视频和版本。模型与开源是当前热榜。收藏只存在这台浏览器里。";
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
    refreshSavedFromFeed();
    render();
  } catch {
    showError("资讯数据还没生成，或当前无法读取 data/news.json。");
  }
}

searchEl.addEventListener("input", () => renderList());
zhBtn.addEventListener("click", () => {
  zhOnly = !zhOnly;
  persistZhOnly();
  renderList();
});
window.addEventListener("hashchange", () => {
  const hash = location.hash.replace("#", "");
  active = CATEGORIES.some((cat) => cat.id === hash) ? hash : "latest";
  render();
});

load();
