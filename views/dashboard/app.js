const WORDSTOCK_PAGE_SIZE = 20;
const POLL_INTERVAL_MS = 5000;

const state = {
  tab: "status",
  overview: null,
  wordstock: null,
  filtersPayload: null,
  config: null,
  wordstockGroup: "",
  wordstockQuery: "",
  wordstockPage: 1,
  pollTimer: null,
  dirty: false,
  backup: null,
};

let bridge = null;

function byId(id) {
  return document.getElementById(id);
}

function node(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined && text !== null) element.textContent = String(text);
  return element;
}

function clear(element) {
  while (element && element.firstChild) element.removeChild(element.firstChild);
}

function setText(id, text) {
  const element = byId(id);
  if (element) element.textContent = text;
}

let toastTimer = null;

function toast(message, tone) {
  const box = byId("toast");
  box.textContent = message;
  box.dataset.tone = tone || "info";
  box.hidden = false;
  if (toastTimer) window.clearTimeout(toastTimer);
  toastTimer = window.setTimeout(() => {
    box.hidden = true;
  }, 3200);
}

function setConn(stateName, label) {
  const box = byId("conn");
  box.dataset.state = stateName;
  setText("conn-label", label);
}

function shortenPath(path) {
  const normalized = String(path || "")
    .split(String.fromCharCode(92))
    .join("/");
  const parts = normalized.split("/").filter(Boolean);
  if (parts.length <= 2) return normalized;
  const last = parts[parts.length - 1];
  if (last && last.length <= 32) return "…/" + last;
  return "…/" + parts.slice(-2).join("/");
}

function formatDuration(seconds) {
  const total = Math.max(0, Math.floor(seconds || 0));
  const days = Math.floor(total / 86400);
  const hours = Math.floor((total % 86400) / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  if (days > 0) return days + " 天 " + hours + " 小时";
  if (hours > 0) return hours + " 小时 " + minutes + " 分";
  if (minutes > 0) return minutes + " 分钟";
  return total + " 秒";
}

function splitList(text) {
  const parts = [];
  String(text)
    .replace(/，/g, ",")
    .split(",")
    .forEach((chunk) => {
      chunk.split(" ").forEach((piece) => {
        if (piece.trim()) parts.push(piece.trim());
      });
    });
  return parts;
}

function applyContext(context) {
  const root = document.documentElement;
  if (context && context.isDark) root.dataset.theme = "dark";
  else root.dataset.theme = "light";
  if (context && context.themeColors) {
    if (context.themeColors.primary) {
      root.style.setProperty("--primary", context.themeColors.primary);
    }
    if (context.themeColors.secondary) {
      root.style.setProperty("--primary-strong", context.themeColors.secondary);
    }
  }
  if (bridge && typeof bridge.t === "function") {
    const title = bridge.t("views.dashboard.title") || "群学习面板";
    document.title = title;
    setText("page-title", title);
  }
  setText("hero-sub", "ChatLearning · 记录群问答，学会群友的说话方式");
}

async function connectBridge() {
  if (window.AstrBotPluginView) return window.AstrBotPluginView;
  const params = new URLSearchParams(window.location.search);
  if (params.has("mock") || window.location.protocol === "file:") {
    await import("./dev-mock.js");
    if (window.AstrBotPluginView) return window.AstrBotPluginView;
  }
  throw new Error("无法连接 AstrBot 页面桥接，请在 AstrBot WebUI 中打开本视图");
}

async function apiGet(path, params) {
  return bridge.apiGet(path, params || {});
}

async function apiPost(path, body) {
  return bridge.apiPost(path, body || {});
}

async function withError(work, fallbackMessage) {
  try {
    return await work();
  } catch (error) {
    const message = error && error.message ? error.message : fallbackMessage;
    toast(message, "error");
    return null;
  }
}

function confirmAction(title, message, confirmLabel) {
  return new Promise((resolve) => {
    const dialog = byId("confirm-dialog");
    setText("confirm-title", title);
    setText("confirm-message", message);
    setText("confirm-ok", confirmLabel || "确认");
    dialog.returnValue = "";
    const onClose = () => {
      dialog.removeEventListener("close", onClose);
      resolve(dialog.returnValue === "confirm");
    };
    dialog.addEventListener("close", onClose);
    dialog.showModal();
  });
}

/* ---------------- 标签页与刷新 ---------------- */

function selectTab(tab) {
  state.tab = tab;
  document.querySelectorAll(".tab").forEach((button) => {
    const active = button.dataset.tab === tab;
    button.classList.toggle("is-active", active);
    button.setAttribute("aria-selected", active ? "true" : "false");
  });
  document.querySelectorAll(".panel").forEach((panel) => {
    panel.classList.toggle("is-active", panel.id === "panel-" + tab);
  });
  loadTab(tab);
  restartPolling();
}

function loadTab(tab) {
  if (tab === "status") return loadOverview(false);
  if (tab === "wordstock") return loadWordstock();
  if (tab === "filters") return loadFilters();
  if (tab === "settings") return loadConfig();
  return Promise.resolve();
}

function restartPolling() {
  if (state.pollTimer) {
    window.clearInterval(state.pollTimer);
    state.pollTimer = null;
  }
  if (state.tab === "status") {
    state.pollTimer = window.setInterval(() => {
      if (!document.hidden) loadOverview(true);
    }, POLL_INTERVAL_MS);
  }
}

/* ---------------- 状态页 ---------------- */

async function loadOverview(silent) {
  const data = await withError(() => apiGet("dashboard/overview"), "加载运行状态失败");
  if (!data) {
    setConn("error", "连接失败");
    return;
  }
  state.overview = data;
  renderOverview(data);
  setConn("online", "已连接");
  if (!silent) setText("hero-sub", "数据目录 " + shortenPath(data.runtime.data_dir));
}

function renderOverview(data) {
  renderStats(data);
  renderStockTable(data);
  renderRuntime(data);
}

function renderStats(data) {
  const grid = byId("stat-grid");
  clear(grid);
  const stats = [
    {
      label: "群词库",
      value: data.totals.groups,
      hint: data.totals.tag_stocks + " 个标签词库",
    },
    {
      label: "问题总数",
      value: data.totals.questions,
      hint: "总词库 " + data.totals.merge_questions + " 条",
    },
    {
      label: "答案总数",
      value: data.totals.answers,
      hint: "按权重随机抽取",
    },
    {
      label: "过滤规则",
      value: data.filters.filter + data.filters.sensitive,
      hint: "黑名单 " + data.filters.blacklist + " 个账号",
    },
  ];
  stats.forEach((item) => {
    const card = node("div", "stat");
    card.appendChild(node("div", "stat-label", item.label));
    card.appendChild(node("div", "stat-value", item.value));
    card.appendChild(node("div", "stat-hint", item.hint));
    grid.appendChild(card);
  });
}

function renderStockTable(data) {
  const body = byId("stock-body");
  clear(body);
  const empty = byId("stock-empty");
  const wrap = document.querySelector("#panel-status .table-wrap");
  if (!data.stocks.length) {
    empty.hidden = false;
    wrap.hidden = true;
    setText("stock-hint", "暂无数据");
    return;
  }
  empty.hidden = true;
  wrap.hidden = false;
  setText("stock-hint", data.stocks.length + " 个词库");
  data.stocks.forEach((stock) => {
    const row = document.createElement("tr");
    const nameCell = document.createElement("td");
    nameCell.className = "col-name";
    nameCell.appendChild(node("div", "", stock.label));
    nameCell.appendChild(node("div", "q-meta", stock.name));
    row.appendChild(nameCell);
    row.appendChild(node("td", "num", stock.questions));
    row.appendChild(node("td", "num", stock.answers));
    const tagCell = document.createElement("td");
    tagCell.className = "col-tags";
    if (stock.tags.length) {
      const chips = node("div", "chips");
      stock.tags.forEach((tag) => chips.appendChild(node("span", "chip", tag)));
      tagCell.appendChild(chips);
    } else {
      tagCell.appendChild(node("span", "muted", "—"));
    }
    row.appendChild(tagCell);
    const isGroup = stock.kind === "group";
    const learning = isGroup && data.status.learning_groups.indexOf(stock.name) !== -1;
    const reply = isGroup && data.status.reply_groups.indexOf(stock.name) !== -1;
    row.appendChild(statusCell(learning));
    row.appendChild(statusCell(reply));
    if (isGroup) {
      const mobileStatus = node("div", "chips mobile-status");
      mobileStatus.appendChild(
        node(
          "span",
          learning ? "pill badge-on" : "pill badge-off",
          "记录 " + (learning ? "开" : "关")
        )
      );
      mobileStatus.appendChild(
        node(
          "span",
          reply ? "pill badge-on" : "pill badge-off",
          "回复 " + (reply ? "开" : "关")
        )
      );
      nameCell.appendChild(mobileStatus);
    }
    body.appendChild(row);
  });
}

function statusCell(enabled) {
  const cell = document.createElement("td");
  cell.className = "col-status";
  cell.appendChild(
    node("span", enabled ? "pill badge-on" : "pill badge-off", enabled ? "开" : "关")
  );
  return cell;
}

function renderRuntime(data) {
  const info = byId("runtime-info");
  clear(info);
  const pairs = [
    ["运行状态", (data.status.learning ? "记录中" : "记录已关闭") + " · " + (data.status.reply ? "回复中" : "回复已关闭")],
    ["全局模式", data.status.sendmode ? "开启" : "关闭"],
    ["已运行", formatDuration(data.runtime.uptime_seconds)],
    ["相似度分词", data.runtime.similarity_backend === "jieba" ? "jieba" : "字符二元组（未安装 jieba）"],
    ["上次合并", data.runtime.last_merge_at || "尚未合并"],
    ["数据目录", shortenPath(data.runtime.data_dir)],
    ["管理员", data.status.admins.length ? data.status.admins.join("、") : "未设置"],
  ];
  pairs.forEach((pair) => {
    info.appendChild(node("dt", "", pair[0]));
    const dd = node("dd", "", pair[1]);
    if (pair[0] === "数据目录") {
      dd.className = "mono";
      dd.title = data.runtime.data_dir;
    }
    info.appendChild(dd);
  });
  if (data.runtime.last_merge_logs && data.runtime.last_merge_logs.length) {
    byId("maintenance-log").hidden = false;
    byId("maintenance-log").textContent = data.runtime.last_merge_logs.join("\n");
  }
}

function showLogs(logs) {
  const box = byId("maintenance-log");
  box.hidden = false;
  box.textContent = (logs || []).join("\n") || "没有输出";
}

/* ---------------- 词库页 ---------------- */

async function loadWordstock() {
  const params = {
    group: state.wordstockGroup || undefined,
    q: state.wordstockQuery || undefined,
    page: state.wordstockPage,
    page_size: WORDSTOCK_PAGE_SIZE,
  };
  const data = await withError(() => apiGet("dashboard/wordstock", params), "加载词库失败");
  if (!data) return;
  state.wordstock = data;
  state.wordstockGroup = data.group || "";
  renderWordstock(data);
}

function renderWordstock(data) {
  const select = byId("wordstock-group");
  clear(select);
  if (!data.groups.length) {
    select.appendChild(node("option", "", "暂无词库"));
    select.disabled = true;
  } else {
    select.disabled = false;
    data.groups.forEach((group) => {
      const option = node("option", "", group.label + "（" + group.questions + " 问）");
      option.value = group.name;
      if (group.name === data.group) option.selected = true;
      select.appendChild(option);
    });
  }
  byId("wordstock-query").value = state.wordstockQuery;
  const body = byId("wordstock-body");
  clear(body);
  const empty = byId("wordstock-empty");
  empty.hidden = data.items.length > 0;
  setText("wordstock-hint", "共 " + data.total + " 条问答");
  setText(
    "page-info",
    data.pages ? "第 " + data.page + " / " + data.pages + " 页" : "没有结果"
  );
  byId("page-prev").disabled = data.page <= 1;
  byId("page-next").disabled = data.pages === 0 || data.page >= data.pages;
  data.items.forEach((item) => body.appendChild(buildQuestionRow(item)));
}

function buildQuestionRow(item) {
  const row = document.createElement("tr");
  const questionCell = document.createElement("td");
  questionCell.className = "q-preview";
  questionCell.appendChild(node("div", "", item.preview));
  const metaText = "记录 " + item.time + (item.regular ? " · 正则问题" : "");
  questionCell.appendChild(node("div", "q-meta", metaText));
  const actions = node("div", "row-actions");
  actions.appendChild(
    makeButton("删除问题", "btn tiny danger ghost", async () => {
      const ok = await confirmAction("删除问题", "将从词库中删除这条问题及其全部答案。", "删除");
      if (!ok) return;
      const result = await withError(
        () =>
          apiPost("dashboard/wordstock/delete", {
            group: state.wordstockGroup,
            question_key: item.key,
          }),
        "删除失败"
      );
      if (result) {
        toast("已删除问题", "success");
        await loadWordstock();
        await loadOverview(true);
      }
    })
  );
  questionCell.appendChild(actions);
  row.appendChild(questionCell);
  row.appendChild(node("td", "num", item.freq));
  row.appendChild(buildAnswersCell(item));
  return row;
}

function buildAnswersCell(item) {
  const cell = document.createElement("td");
  if (!item.answers.length) {
    cell.appendChild(node("span", "muted", "暂无答案"));
    return cell;
  }
  const list = node("div", "a-list");
  item.answers.forEach((answer) => {
    const answerRow = node("div", "a-row");
    answerRow.appendChild(node("span", "a-text", answer.preview));
    answerRow.appendChild(node("span", "item-meta", answer.time));
    const weight = node("label", "a-weight");
    weight.appendChild(node("span", "", "权重"));
    const input = document.createElement("input");
    input.type = "number";
    input.min = "0";
    input.max = "9999";
    input.value = String(answer.same);
    input.setAttribute("aria-label", "答案权重");
    weight.appendChild(input);
    answerRow.appendChild(weight);
    answerRow.appendChild(
      makeButton("保存", "btn tiny", async () => {
        const value = Number(input.value);
        if (!Number.isInteger(value) || value < 0 || value > 9999) {
          toast("权重需为 0-9999 的整数", "error");
          return;
        }
        const result = await withError(
          () =>
            apiPost("dashboard/wordstock/weight", {
              group: state.wordstockGroup,
              question_key: item.key,
              answer_key: answer.key,
              same: value,
            }),
          "保存权重失败"
        );
        if (result) toast("权重已更新", "success");
      })
    );
    answerRow.appendChild(
      makeButton("删除", "btn tiny danger ghost", async () => {
        const ok = await confirmAction("删除答案", "将从这条问题下删除该答案。", "删除");
        if (!ok) return;
        const result = await withError(
          () =>
            apiPost("dashboard/wordstock/delete", {
              group: state.wordstockGroup,
              question_key: item.key,
              answer_key: answer.key,
            }),
          "删除失败"
        );
        if (result) {
          toast("已删除答案", "success");
          await loadWordstock();
          await loadOverview(true);
        }
      })
    );
    list.appendChild(answerRow);
  });
  if (item.answer_count > item.answers.length) {
    list.appendChild(
      node("div", "q-meta", "仅显示前 " + item.answers.length + " 条，共 " + item.answer_count + " 条")
    );
  }
  cell.appendChild(list);
  return cell;
}

function makeButton(label, className, handler) {
  const button = node("button", className, label);
  button.type = "button";
  button.addEventListener("click", handler);
  return button;
}

/* ---------------- 过滤页 ---------------- */

async function loadFilters() {
  const data = await withError(() => apiGet("dashboard/filters"), "加载过滤规则失败");
  if (!data) return;
  state.filtersPayload = data;
  renderFilters(data);
}

function renderFilters(data) {
  renderFilterList("filter-list", "filter-count", data.filter, "filter", (item) => ({
    text: item.preview,
    meta: item.accurate ? "精确" : "模糊",
    payload: { kind: "filter", index: item.index },
  }));
  renderFilterList("sensitive-list", "sensitive-count", data.sensitive, "sensitive", (item) => ({
    text: item.preview,
    meta: "",
    payload: { kind: "sensitive", index: item.index },
  }));
  renderBlackList(data.blacklist);
  renderTypes(data);
}

function renderFilterList(listId, countId, items, kind, mapItem) {
  const list = byId(listId);
  clear(list);
  setText(countId, items.length);
  if (!items.length) {
    list.appendChild(node("li", "", "暂无条目"));
    const empty = list.firstChild;
    empty.className = "muted";
    empty.style.justifyContent = "center";
    return;
  }
  items.forEach((item) => {
    const mapped = mapItem(item);
    const entry = document.createElement("li");
    const text = node("span", "item-text", mapped.text);
    entry.appendChild(text);
    const right = node("span", "item-meta");
    if (mapped.meta) right.appendChild(node("span", "", mapped.meta));
    right.appendChild(
      makeButton("删除", "btn tiny danger ghost", async () => {
        const ok = await confirmAction("删除条目", "确认从" + (kind === "filter" ? "过滤词" : "敏感词") + "中删除该条目？", "删除");
        if (!ok) return;
        const result = await withError(
          () => apiPost("dashboard/filters/remove", mapped.payload),
          "删除失败"
        );
        if (result) {
          toast("已删除", "success");
          await loadFilters();
          await loadOverview(true);
        }
      })
    );
    entry.appendChild(right);
    list.appendChild(entry);
  });
}

function renderBlackList(items) {
  const list = byId("black-list");
  clear(list);
  setText("black-count", items.length);
  if (!items.length) {
    list.appendChild(node("li", "muted", "暂无黑名单"));
    list.firstChild.style.justifyContent = "center";
    return;
  }
  items.forEach((item) => {
    const entry = document.createElement("li");
    entry.appendChild(node("span", "item-text", item.user));
    const right = node("span", "item-meta", "触发 " + item.count + " 次");
    right.appendChild(
      makeButton("移除", "btn tiny danger ghost", async () => {
        const ok = await confirmAction("移除黑名单", "确认把 " + item.user + " 从黑名单中移除？", "移除");
        if (!ok) return;
        const result = await withError(
          () => apiPost("dashboard/filters/remove", { kind: "black", user: item.user }),
          "移除失败"
        );
        if (result) {
          toast("已移除", "success");
          await loadFilters();
          await loadOverview(true);
        }
      })
    );
    entry.appendChild(right);
    list.appendChild(entry);
  });
}

function renderTypes(data) {
  const box = byId("types-box");
  clear(box);
  data.supported_types.forEach((type) => {
    const label = node("label", "check");
    const input = document.createElement("input");
    input.type = "checkbox";
    input.value = type;
    input.checked = data.types.indexOf(type) !== -1;
    label.appendChild(input);
    label.appendChild(node("span", "", type));
    box.appendChild(label);
  });
}

/* ---------------- 设置页 ---------------- */

async function loadConfig() {
  const data = await withError(() => apiGet("dashboard/config"), "加载配置失败");
  if (!data) return;
  state.config = data;
  state.dirty = false;
  renderSettings(data);
}

function renderSettings(data) {
  const container = byId("settings-groups");
  clear(container);
  data.groups.forEach((groupName) => {
    const fields = data.fields.filter((field) => field.group === groupName);
    if (!fields.length) return;
    const section = node("section", "settings-group");
    section.appendChild(node("h2", "", groupName));
    const grid = node("div", "settings-grid");
    fields.forEach((field) => grid.appendChild(buildSetting(field)));
    section.appendChild(grid);
    container.appendChild(section);
  });
  renderTypefreq(data.typefreq);
  byId("savebar").hidden = true;
}

function buildSetting(field) {
  const row = node("div", "setting");
  const main = node("div", "setting-main");
  main.appendChild(node("div", "setting-label", field.label));
  if (field.hint) main.appendChild(node("div", "setting-hint", field.hint));
  row.appendChild(main);
  const control = node("div", "setting-control");
  if (field.type === "bool") {
    const label = node("label", "switch");
    const input = document.createElement("input");
    input.type = "checkbox";
    input.dataset.field = field.key;
    input.checked = Boolean(field.value);
    input.setAttribute("aria-label", field.label);
    label.appendChild(input);
    label.appendChild(node("span", "switch-track"));
    control.appendChild(label);
  } else if (field.type === "pair") {
    const pair = node("div", "setting-pair");
    pair.style.display = "inline-flex";
    pair.style.gap = "6px";
    const base = document.createElement("input");
    base.type = "number";
    base.step = "0.1";
    base.min = "0";
    base.value = String(field.value[0]);
    base.dataset.field = field.key;
    base.dataset.index = "0";
    base.setAttribute("aria-label", field.label + " 基准");
    const jitter = document.createElement("input");
    jitter.type = "number";
    jitter.step = "0.1";
    jitter.min = "0";
    jitter.value = String(field.value[1]);
    jitter.dataset.field = field.key;
    jitter.dataset.index = "1";
    jitter.setAttribute("aria-label", field.label + " 浮动");
    pair.appendChild(base);
    pair.appendChild(node("span", "muted", "+"));
    pair.appendChild(jitter);
    control.appendChild(pair);
  } else if (field.type === "int" || field.type === "float") {
    const input = document.createElement("input");
    input.type = "number";
    input.dataset.field = field.key;
    if (field.min !== undefined) input.min = String(field.min);
    if (field.max !== undefined) input.max = String(field.max);
    if (field.step !== undefined) input.step = String(field.step);
    else if (field.type === "int") input.step = "1";
    else input.step = "0.05";
    input.value = String(field.value);
    input.setAttribute("aria-label", field.label);
    control.appendChild(input);
    if (field.unit) control.appendChild(node("span", "unit", field.unit));
  } else if (field.type === "list") {
    const input = document.createElement("input");
    input.type = "text";
    input.dataset.field = field.key;
    input.value = (field.value || []).join(" ");
    input.placeholder = "空格或逗号分隔";
    input.setAttribute("aria-label", field.label);
    input.style.width = "220px";
    control.appendChild(input);
  }
  row.appendChild(control);
  return row;
}

function renderTypefreq(typefreq) {
  const box = byId("typefreq-box");
  clear(box);
  const labels = { Plain: "纯文本", Image: "图片" };
  typefreq.types.forEach((type) => {
    const label = node("label", "check");
    label.appendChild(node("span", "", (labels[type] || type) + " 阈值"));
    const input = document.createElement("input");
    input.type = "number";
    input.min = "0";
    input.max = "9999";
    input.step = "1";
    input.style.width = "78px";
    input.dataset.typefreq = type;
    input.value = String((typefreq.values && typefreq.values[type]) || 0);
    input.setAttribute("aria-label", type + " 回复阈值");
    label.appendChild(input);
    box.appendChild(label);
  });
}

function markDirty() {
  if (state.dirty) return;
  state.dirty = true;
  byId("savebar").hidden = false;
}

function collectSettings() {
  const values = {};
  const typefreq = {};
  if (!state.config) return { values, typefreq };
  state.config.fields.forEach((field) => {
    if (field.type === "bool") {
      const input = document.querySelector('[data-field="' + field.key + '"]');
      values[field.key] = Boolean(input && input.checked);
    } else if (field.type === "pair") {
      const base = document.querySelector(
        '[data-field="' + field.key + '"][data-index="0"]'
      );
      const jitter = document.querySelector(
        '[data-field="' + field.key + '"][data-index="1"]'
      );
      values[field.key] = [Number(base.value), Number(jitter.value)];
    } else if (field.type === "int" || field.type === "float") {
      const input = document.querySelector('[data-field="' + field.key + '"]');
      values[field.key] = Number(input.value);
    } else if (field.type === "list") {
      const input = document.querySelector('[data-field="' + field.key + '"]');
      values[field.key] = splitList(input.value);
    }
  });
  document.querySelectorAll("[data-typefreq]").forEach((input) => {
    typefreq[input.dataset.typefreq] = Number(input.value);
  });
  return { values, typefreq };
}

async function saveSettings() {
  const payload = collectSettings();
  const data = await withError(
    () => apiPost("dashboard/config/save", payload),
    "保存配置失败"
  );
  if (!data) return;
  state.dirty = false;
  byId("savebar").hidden = true;
  toast("配置已保存并生效", "success");
  await loadConfig();
  await loadOverview(true);
}

/* ---------------- 备份页 ---------------- */

async function exportBackup() {
  try {
    const result = await bridge.download(
      "dashboard/backup",
      {},
      "chat_learning_backup.json"
    );
    toast("已开始下载" + (result && result.filename ? " " + result.filename : ""), "success");
  } catch (error) {
    toast((error && error.message) || "导出失败", "error");
  }
}

async function handleBackupFile(file) {
  const preview = byId("backup-preview");
  clear(preview);
  state.backup = null;
  byId("restore-options").hidden = true;
  if (!file) return;
  try {
    const text = await file.text();
    const parsed = JSON.parse(text);
    if (!parsed || typeof parsed !== "object" || parsed.format !== "astrbot_plugin_chat_learning.backup") {
      throw new Error("不是本插件的备份文件");
    }
    state.backup = parsed;
    const meta = node("div", "meta");
    meta.appendChild(node("div", "", "备份时间：" + (parsed.exported_at || "未知")));
    meta.appendChild(
      node(
        "div",
        "",
        "包含 " +
          Object.keys(parsed.wordstock || {}).length +
          " 个词库、" +
          ((parsed.filters && parsed.filters.filter && parsed.filters.filter.length) || 0) +
          " 条过滤词"
      )
    );
    preview.appendChild(node("p", "", "文件读取成功，请选择恢复范围："));
    preview.appendChild(meta);
    byId("restore-options").hidden = false;
    byId("restore-confirm").checked = false;
    byId("restore-run").disabled = true;
  } catch (error) {
    preview.appendChild(node("p", "muted", "读取失败：" + ((error && error.message) || "无效的 JSON")));
  }
}

async function restoreBackup() {
  if (!state.backup) return;
  if (!byId("restore-confirm").checked) {
    toast("请先勾选确认", "error");
    return;
  }
  const restore = {
    config: byId("restore-config").checked,
    filters: byId("restore-filters").checked,
    wordstock: byId("restore-wordstock").checked,
    wordstock_mode: document.querySelector('input[name="wordstock-mode"]:checked').value,
  };
  if (!restore.config && !restore.filters && !restore.wordstock) {
    toast("至少选择一项恢复内容", "error");
    return;
  }
  const ok = await confirmAction(
    "恢复备份",
    "将按所选范围恢复数据，恢复前会自动备份当前数据。",
    "开始恢复"
  );
  if (!ok) return;
  const data = await withError(
    () => apiPost("dashboard/restore", { backup: state.backup, restore }),
    "恢复失败"
  );
  if (!data) return;
  toast("恢复完成，已备份当前数据：" + (data.pre_backup || ""), "success");
  byId("restore-options").hidden = true;
  state.backup = null;
  byId("backup-file").value = "";
  await loadOverview(true);
  if (state.tab === "settings") await loadConfig();
  if (state.tab === "filters") await loadFilters();
}

/* ---------------- 事件绑定 ---------------- */

function bindEvents() {
  document.querySelectorAll(".tab").forEach((button) => {
    button.addEventListener("click", () => selectTab(button.dataset.tab));
  });
  document.querySelector(".tabs").addEventListener("keydown", (event) => {
    if (event.key !== "ArrowRight" && event.key !== "ArrowLeft") return;
    const tabs = Array.from(document.querySelectorAll(".tab"));
    const index = tabs.indexOf(document.activeElement);
    if (index === -1) return;
    const next = tabs[(index + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length];
    next.focus();
    selectTab(next.dataset.tab);
  });
  byId("refresh").addEventListener("click", async () => {
    if (state.tab === "backup") {
      await loadOverview(true);
      toast("已刷新状态", "success");
    } else {
      await loadTab(state.tab);
    }
  });
  byId("merge-now").addEventListener("click", async () => {
    const data = await withError(
      () => apiPost("dashboard/maintenance/merge", {}),
      "合并失败"
    );
    if (data) {
      showLogs(data.logs);
      toast("合并完成", "success");
      await loadOverview(true);
    }
  });
  byId("autodelete").addEventListener("click", async () => {
    const ok = await confirmAction(
      "清理词库",
      "将删除合并后仅出现一次的问题，以及命中过滤规则的条目，操作不可逆。",
      "确认清理"
    );
    if (!ok) return;
    const data = await withError(
      () => apiPost("dashboard/maintenance/autodelete", { confirm: true }),
      "清理失败"
    );
    if (data) {
      showLogs(data.logs);
      toast("清理完成", "success");
      await loadOverview(true);
    }
  });
  byId("wordstock-group").addEventListener("change", (event) => {
    state.wordstockGroup = event.target.value;
    state.wordstockPage = 1;
    loadWordstock();
  });
  byId("wordstock-search").addEventListener("submit", (event) => {
    event.preventDefault();
    state.wordstockQuery = byId("wordstock-query").value.trim();
    state.wordstockPage = 1;
    loadWordstock();
  });
  byId("page-prev").addEventListener("click", () => {
    if (!state.wordstock || state.wordstock.page <= 1) return;
    state.wordstockPage = state.wordstock.page - 1;
    loadWordstock();
  });
  byId("page-next").addEventListener("click", () => {
    if (!state.wordstock || state.wordstock.page >= state.wordstock.pages) return;
    state.wordstockPage = state.wordstock.page + 1;
    loadWordstock();
  });
  byId("filter-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const text = byId("filter-text").value.trim();
    if (!text) return;
    const data = await withError(
      () =>
        apiPost("dashboard/filters/add", {
          kind: "filter",
          text,
          accurate: byId("filter-accurate").checked,
        }),
      "添加失败"
    );
    if (data) {
      byId("filter-text").value = "";
      byId("filter-accurate").checked = false;
      toast("已添加过滤词", "success");
      await loadFilters();
      await loadOverview(true);
    }
  });
  byId("sensitive-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const text = byId("sensitive-text").value.trim();
    if (!text) return;
    const data = await withError(
      () => apiPost("dashboard/filters/add", { kind: "sensitive", text }),
      "添加失败"
    );
    if (data) {
      byId("sensitive-text").value = "";
      toast("已添加敏感词", "success");
      await loadFilters();
      await loadOverview(true);
    }
  });
  byId("black-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const text = byId("black-text").value.trim();
    if (!text) return;
    const data = await withError(
      () => apiPost("dashboard/filters/add", { kind: "black", text }),
      "添加失败"
    );
    if (data) {
      byId("black-text").value = "";
      toast("已加入黑名单", "success");
      await loadFilters();
      await loadOverview(true);
    }
  });
  byId("types-save").addEventListener("click", async () => {
    const types = Array.from(document.querySelectorAll("#types-box input:checked")).map(
      (input) => input.value
    );
    const data = await withError(
      () => apiPost("dashboard/filters/types", { types }),
      "保存类型失败"
    );
    if (data) toast("类型过滤已保存", "success");
  });
  byId("settings-form").addEventListener("input", markDirty, { capture: true });
  byId("settings-form").addEventListener("change", markDirty, { capture: true });
  byId("settings-form").addEventListener("submit", (event) => {
    event.preventDefault();
    saveSettings();
  });
  byId("settings-save").addEventListener("click", saveSettings);
  byId("settings-reset").addEventListener("click", () => {
    state.dirty = false;
    loadConfig();
  });
  byId("backup-export").addEventListener("click", exportBackup);
  byId("backup-file").addEventListener("change", (event) => {
    handleBackupFile(event.target.files && event.target.files[0]);
  });
  byId("restore-confirm").addEventListener("change", (event) => {
    byId("restore-run").disabled = !event.target.checked;
  });
  byId("restore-run").addEventListener("click", restoreBackup);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden && state.tab === "status") loadOverview(true);
  });
}

/* ---------------- 启动 ---------------- */

async function start() {
  try {
    bridge = await connectBridge();
  } catch (error) {
    setConn("error", "未连接");
    setText("hero-sub", (error && error.message) || "无法连接 AstrBot 页面桥接");
    toast((error && error.message) || "无法连接 AstrBot", "error");
    return;
  }
  try {
    const context = await bridge.ready();
    applyContext(context);
    if (typeof bridge.onContext === "function") bridge.onContext(applyContext);
  } catch (error) {
    setText("hero-sub", "页面桥接初始化失败");
  }
  bindEvents();
  selectTab("status");
}

start();
