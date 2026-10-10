const VIEWS = {
  news: "最新资讯",
  gpus: "显卡行情",
  people: "人物动态",
};

const NEWS_FILTERS = [
  { id: "all", label: "全部" },
  { id: "models", label: "AI 模型" },
  { id: "vision", label: "图像与视频" },
  { id: "mac", label: "Mac" },
  { id: "nvidia", label: "英伟达" },
  { id: "amd", label: "AMD" },
];

const app = document.querySelector("#app");
const navLinks = document.querySelectorAll(".nav a");

const state = {
  view: "news",
  index: null,
  days: {},
  day: "",
  newsFilter: "all",
  gpus: null,
  region: "domestic",
  brand: "all",
  channel: "all",
  people: null,
  person: "all",
};

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  return node;
}

function safeUrl(url) {
  try {
    const parsed = new URL(url);
    if (parsed.protocol === "https:" || parsed.protocol === "http:") return parsed.href;
  } catch {
    return null;
  }
  return null;
}

function replaceChildren(node, children) {
  node.replaceChildren(...children.filter(Boolean));
}

function setPressed(button, on) {
  button.setAttribute("aria-pressed", on ? "true" : "false");
}

function sourceLink(url, label) {
  const safe = safeUrl(url);
  if (!safe) return el("span", "source muted", label || "来源待补充");
  const link = el("a", "source");
  link.href = safe;
  link.target = "_blank";
  link.rel = "noopener noreferrer";
  link.append(label || "查看来源");
  const arrow = el("span", "arrow", "↗");
  arrow.setAttribute("aria-hidden", "true");
  link.append(" ", arrow);
  return link;
}

function fold(summary, text) {
  if (!text) return null;
  const details = el("details", "fold");
  details.append(el("summary", "", summary));
  details.append(el("p", "", text));
  return details;
}

function pageHead(title, stamp, subtitle) {
  const head = el("div", "page-head");
  const titles = el("div");
  titles.append(el("h1", "", title));
  if (subtitle) titles.append(el("p", "sub", subtitle));
  head.append(titles);
  if (stamp) head.append(el("p", "stamp", `更新于 ${stamp}`));
  return head;
}

function chipRow(className, options, current, onPick) {
  const row = el("div", className);
  if (className === "chips" || className === "archive") {
    row.setAttribute("role", "tablist");
  }
  for (const option of options) {
    const button = el("button", "", option.label);
    button.type = "button";
    setPressed(button, option.id === current);
    button.addEventListener("click", () => onPick(option.id));
    row.append(button);
  }
  return row;
}

function currentView() {
  const hash = location.hash.replace("#", "");
  return Object.prototype.hasOwnProperty.call(VIEWS, hash) ? hash : "news";
}

function requestedDay() {
  const param = new URLSearchParams(location.search).get("d") || "";
  return /^\d{4}-\d{2}-\d{2}$/.test(param) ? param : "";
}

async function setDay(day) {
  const url = new URL(location.href);
  if (day) url.searchParams.set("d", day);
  else url.searchParams.delete("d");
  history.replaceState(null, "", `${url.pathname}${url.search}${url.hash}`);
  state.day = day;
  await ensureDay(day);
  render();
}

async function loadJson(url) {
  const response = await fetch(url, { cache: "no-cache" });
  if (!response.ok) throw new Error(String(response.status));
  return response.json();
}

async function ensureDay(day) {
  if (!day || state.days[day]) return state.days[day] || null;
  try {
    state.days[day] = await loadJson(`data/briefings/${day}.json`);
  } catch {
    state.days[day] = null;
  }
  return state.days[day];
}

function renderNews() {
  const index = state.index;
  const days = (index && index.days) || [];
  const selected = days.some((row) => row.date === state.day) ? state.day : (days[0] && days[0].date) || "";
  state.day = selected;
  const briefing = state.days[selected];
  const stamp = (briefing && briefing.updatedLabel) || (index && index.updatedLabel) || "";
  const nodes = [pageHead("最新资讯", stamp)];

  nodes.push(chipRow("chips", NEWS_FILTERS, state.newsFilter, (id) => {
    state.newsFilter = id;
    render();
  }));

  if (days.length) {
    const archive = el("div", "archive");
    archive.setAttribute("aria-label", "往期简报");
    archive.append(el("span", "archive-label", "往期"));
    for (const row of days) {
      const button = el("button", "", row.label || row.date);
      button.type = "button";
      setPressed(button, row.date === selected);
      button.addEventListener("click", () => setDay(row.date));
      archive.append(button);
    }
    nodes.push(archive);
  }

  nodes.push(el("p", "banner", "当前为最近保存的快照，下一次核查将补充最新动态。"));

  const board = el("div", "board");
  const cards = ((briefing && briefing.cards) || []).filter((card) => {
    return state.newsFilter === "all" || card.category === state.newsFilter;
  });
  if (!briefing) {
    board.append(el("p", "empty", selected ? "这一天的简报没有读到。" : "简报还没有生成。"));
  } else if (!cards.length) {
    board.append(el("p", "empty", "这个分类今天没有放进简报。"));
  }
  cards.forEach((card, index) => {
    const featured = Boolean(card.featured) && state.newsFilter === "all" && index === 0;
    board.append(newsCard(card, featured));
  });
  nodes.push(board);
  nodes.push(el("p", "foot-note", "时间均为北京时间（UTC+8）。归档按北京时间的自然日。"));
  replaceChildren(app, nodes);
}

function newsCard(card, featured) {
  const article = el("article", featured ? "card featured" : "card");
  const top = el("div", "card-top");
  const tag = el("p", `tag tag-${card.category || "models"}`, card.categoryLabel || "AI 模型");
  top.append(tag, el("p", "when", card.dateLabel || "日期待核实"));
  article.append(top);

  const title = el("h2", "", card.title || "标题待补充");
  const summary = el("p", "summary", card.summary || "摘要还在整理。");
  const takeaway = el("p", "takeaway", card.takeaway || "先核对原文，再决定要不要改你现在的做法。");

  if (featured) {
    const split = el("div", "card-split");
    const main = el("div");
    main.append(title, summary);
    split.append(main, takeaway);
    article.append(split);
  } else {
    article.append(title, summary, el("hr", "rule"), takeaway);
  }
  article.append(fold("适用说明", card.applicability));
  const foot = el("div", "card-foot");
  foot.append(
    sourceLink(card.sourceUrl, card.sourceName || "查看来源"),
    el("p", "verify", card.verifiedLabel || "待核查"),
  );
  article.append(foot);
  return article;
}

function activeRegion() {
  const regions = (state.gpus && state.gpus.regions) || [];
  return regions.find((row) => row.id === state.region) || regions[0] || { id: "domestic", subtitle: "中国大陆 · 人民币", currency: "CNY" };
}

function renderGpus() {
  const data = state.gpus || {};
  const region = activeRegion();
  const stamp = data.updatedLabel || "";
  const nodes = [pageHead("显卡行情", stamp, region.subtitle || "")];

  const regions = data.regions || [];
  if (regions.length) {
    nodes.push(chipRow("segment", regions, region.id, (id) => {
      state.region = id;
      state.channel = "all";
      render();
    }));
  }

  const brands = data.brands || [];
  nodes.push(chipRow("chips", brands, state.brand, (id) => {
    state.brand = id;
    render();
  }));

  const channels = (data.channels && data.channels[region.id]) || [];
  if (channels.length) {
    nodes.push(chipRow("channels", channels, state.channel, (id) => {
      state.channel = id;
      render();
    }));
  }

  const models = (data.models || []).filter((model) => {
    if (model.region !== region.id) return false;
    if (state.brand !== "all" && model.brand !== state.brand) return false;
    return true;
  });
  const board = el("div", "board");
  if (!models.length) {
    board.append(el("p", "empty", "这个范围里还没有要跟踪的型号。"));
  }
  for (const model of models) board.append(gpuCard(model, region));
  nodes.push(board);
  nodes.push(el("p", "foot-note", "没有核到标价时显示「待采集」。价格不会用估计值填上。时间均为北京时间。"));
  replaceChildren(app, nodes);
}

function quoteFor(model) {
  const rows = ((state.gpus && state.gpus.quotes) || []).filter((quote) => {
    return quote.modelId === model.id && quote.region === model.region && typeof quote.price === "number";
  });
  if (state.channel !== "all") return rows.find((quote) => quote.channel === state.channel) || null;
  return rows.find((quote) => quote.channel === "jd-self") || rows[0] || null;
}

function channelLabel(id) {
  const region = activeRegion();
  const channels = (state.gpus && state.gpus.channels && state.gpus.channels[region.id]) || [];
  const found = channels.find((row) => row.id === id);
  return found ? found.label : "";
}

function money(value, currency) {
  const amount = new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 0 }).format(value);
  if (currency === "USD") return `$${amount}`;
  return `¥${amount}`;
}

function gpuCard(model, region) {
  const quote = quoteFor(model);
  const article = el("article", "gpu-card");
  const top = el("div", "card-top");
  top.append(
    el("p", `tag brand-${model.brand || "nvidia"}`, model.brandLabel || ""),
    el("p", "when", model.vram || ""),
  );
  article.append(top);
  article.append(el("h2", "", model.name || "型号待确认"));
  article.append(el("p", "gpu-note", model.note || "具体板卡版本待确认"));

  const price = el("p", "price");
  if (quote) {
    price.append(money(quote.price, region.currency));
    price.append(el("small", "", quote.note || "页面参考标价"));
  } else {
    price.append("待采集");
    price.append(el("small", "", "首个报价"));
  }
  article.append(price);
  if (quote) {
    const where = [channelLabel(quote.channel), quote.checkedLabel].filter(Boolean);
    if (where.length) article.append(el("p", "checked", where.join(" · ")));
  }
  article.append(el("p", "trend", quote ? "同款渠道确认后积攒可比走势" : "价格记录建立后展示走势"));

  const foot = el("div", "card-foot");
  foot.append(
    quote && quote.sourceUrl ? sourceLink(quote.sourceUrl, quote.sourceName || "查看报价") : el("span", "source muted", "来源待补充"),
    el("p", "verify", region.currency || "CNY"),
  );
  article.append(foot);
  return article;
}

function renderPeople() {
  const data = state.people || {};
  const people = data.people || [];
  const nodes = [pageHead("人物动态", data.updatedLabel || "")];
  const filters = [{ id: "all", label: "全部" }].concat(people.map((person) => ({ id: person.id, label: person.name })));
  nodes.push(chipRow("chips", filters, state.person, (id) => {
    state.person = id;
    render();
  }));
  nodes.push(el("p", "banner", "只收录素材里已经提到这些人的公开报道。没有新动态时留空，不编造原帖。"));

  const posts = (data.posts || []).filter((post) => state.person === "all" || post.personId === state.person);
  const board = el("div", "board");
  if (!posts.length) {
    const who = people.find((person) => person.id === state.person);
    board.append(el("p", "empty", who ? `${who.name}暂时没有新的动态。` : "这几位今天还没有核查出新动态。"));
  }
  for (const post of posts) board.append(personCard(post, people));
  nodes.push(board);
  nodes.push(el("p", "foot-note", "帖子和报道里的时间已换算成北京时间。来源没有时区时，卡片会标成待核实。"));
  replaceChildren(app, nodes);
}

function personCard(post, people) {
  const person = people.find((row) => row.id === post.personId) || {
    name: "人物",
    handle: "",
    initial: "人",
  };
  const article = el("article", "person");
  const top = el("div", "person-top");
  const who = el("div", "who");
  who.append(el("span", "avatar", person.initial || person.name.slice(0, 1)));
  const names = el("div");
  names.append(el("strong", "", person.name));
  if (person.handle) names.append(el("span", "handle", `@${person.handle}`));
  who.append(names);
  top.append(who, el("p", "when", post.timeLabel || "时间待核实"));
  article.append(top);
  article.append(el("h2", "", post.title || "动态待补充"));
  article.append(el("p", "summary", post.summary || ""));
  if (post.context) {
    article.append(el("hr", "rule"));
    article.append(el("p", "context", post.context));
  }
  article.append(fold("来源说明", post.sourceNote));
  const foot = el("div", "card-foot");
  foot.append(
    sourceLink(post.sourceUrl, post.linkLabel || "查看原文"),
    el("p", "tags", (post.tags || []).join(" · ")),
  );
  article.append(foot);
  return article;
}

function render() {
  state.view = currentView();
  document.title = `${VIEWS[state.view]} · 小波AI资讯站`;
  for (const link of navLinks) {
    const on = link.dataset.view === state.view;
    if (on) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  }
  if (state.view === "gpus") renderGpus();
  else if (state.view === "people") renderPeople();
  else renderNews();
}

function showError(message) {
  replaceChildren(app, [pageHead("最新资讯", ""), el("p", "empty", message)]);
}

async function load() {
  state.view = currentView();
  try {
    const [index, gpus, people] = await Promise.all([
      loadJson("data/briefings/index.json").catch(() => ({ days: [], updatedLabel: "" })),
      loadJson("data/gpus.json").catch(() => null),
      loadJson("data/people.json").catch(() => null),
    ]);
    state.index = index;
    state.gpus = gpus;
    state.people = people;
    const wanted = requestedDay();
    const days = index.days || [];
    const day = days.some((row) => row.date === wanted) ? wanted : (days[0] && days[0].date) || "";
    state.day = day;
    if (day) await ensureDay(day);
    render();
  } catch {
    showError("页面数据还没生成，或当前无法读取。");
  }
}

document.querySelector("#refresh").addEventListener("click", () => location.reload());
window.addEventListener("hashchange", () => render());

load();
