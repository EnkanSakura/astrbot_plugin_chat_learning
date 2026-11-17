/*
 * 仅用于本地预览与截图：URL 带 ?mock=1 且没有 AstrBot bridge 时由 app.js 动态加载。
 * 不参与生产页面，也不会被 AstrBot 加载。
 */
const query = new URLSearchParams(window.location.search);
const isDark = query.get("theme") === "dark" || query.get("dark") === "1";

const TYPE_LABELS = { Plain: "纯文本", Image: "图片" };

const state = {
  groups: [
    { name: "1001", label: "群 1001", kind: "group", tags: ["摸鱼"] },
    { name: "1002", label: "群 1002", kind: "group", tags: ["游戏"] },
    { name: "2001", label: "群 2001", kind: "group", tags: [] },
    { name: "游戏", label: "标签 · 游戏", kind: "tag", tags: [] },
    { name: "Merge", label: "总词库", kind: "merge", tags: [] },
  ],
  wordstock: {
    "1001": [
      { key: "q1", preview: "今天吃什么", regular: false, freq: 18, time: "2026-11-16 21:04:02", answers: [
        { key: "a1", preview: "食堂二楼新开了窗口，去试试", same: 6, time: "2026-11-16 21:04:20" },
        { key: "a2", preview: "随便，点外卖吧", same: 4, time: "2026-11-15 18:22:11" },
        { key: "a3", preview: "吃火锅", same: 2, time: "2026-11-12 12:31:08" },
      ] },
      { key: "q2", preview: "有人打游戏吗", regular: false, freq: 11, time: "2026-11-17 19:12:44", answers: [
        { key: "a4", preview: "来，开一把", same: 5, time: "2026-11-17 19:13:02" },
      ] },
      { key: "q3", preview: "周末去哪玩", regular: false, freq: 7, time: "2026-11-14 10:02:18", answers: [
        { key: "a5", preview: "爬山，早上七点出发", same: 3, time: "2026-11-14 10:03:40" },
        { key: "a6", preview: "在家躺着不香吗", same: 3, time: "2026-11-14 10:05:12" },
      ] },
      { key: "q4", preview: "^【摸鱼】.*$", regular: true, freq: 3, time: "2026-11-10 14:44:09", answers: [
        { key: "a7", preview: "摸鱼快乐", same: 2, time: "2026-11-10 14:44:31" },
      ] },
    ],
    "1002": [
      { key: "q5", preview: "开黑吗", regular: false, freq: 26, time: "2026-11-17 20:01:12", answers: [
        { key: "a8", preview: "等一个", same: 9, time: "2026-11-17 20:01:30" },
        { key: "a9", preview: "来了来了", same: 7, time: "2026-11-17 19:58:44" },
      ] },
      { key: "q6", preview: "新版本更新了什么", regular: false, freq: 9, time: "2026-11-16 09:31:07", answers: [
        { key: "a10", preview: "看公告，主要改了匹配机制", same: 4, time: "2026-11-16 09:32:18" },
      ] },
    ],
    "2001": [
      { key: "q7", preview: "晚上吃什么", regular: false, freq: 4, time: "2026-11-13 17:40:22", answers: [
        { key: "a11", preview: "麻辣烫", same: 2, time: "2026-11-13 17:41:05" },
      ] },
    ],
    "游戏": [
      { key: "q5", preview: "开黑吗", regular: false, freq: 26, time: "2026-11-17 20:01:12", answers: [
        { key: "a8", preview: "等一个", same: 9, time: "2026-11-17 20:01:30" },
      ] },
    ],
    "Merge": [
      { key: "q1", preview: "今天吃什么", regular: false, freq: 22, time: "2026-11-16 21:04:02", answers: [
        { key: "a1", preview: "食堂二楼新开了窗口，去试试", same: 6, time: "2026-11-16 21:04:20" },
        { key: "a11", preview: "麻辣烫", same: 2, time: "2026-11-13 17:41:05" },
      ] },
      { key: "q5", preview: "开黑吗", regular: false, freq: 26, time: "2026-11-17 20:01:12", answers: [
        { key: "a8", preview: "等一个", same: 9, time: "2026-11-17 20:01:30" },
      ] },
    ],
  },
  filters: {
    filter: [
      { index: 0, preview: "广告", accurate: false },
      { index: 1, preview: "二维码", accurate: true },
      { index: 2, preview: "代练", accurate: false },
    ],
    sensitive: [{ index: 0, preview: "代练" }],
    blacklist: [
      { user: "123456789", count: 3 },
      { user: "987654321", count: 99999 },
    ],
    types: ["Quote", "Poke"],
    supported_types: ["At", "AtAll", "Quote", "Poke", "Face", "Record", "Video", "File"],
  },
  groupsConfig: {
    learning: ["1001", "1002", "3001"],
    reply: ["1001", "1002"],
    unmerge: ["2001"],
    tags: { "1001": ["摸鱼"], "1002": ["游戏"], "3001": ["测试"] },
    subadmins: { "1001": ["20001"] },
  },
  config: {
    fields: [
      { key: "learning", label: "开启记录", type: "bool", group: "开关", hint: "记录群消息并学习问答对", value: true },
      { key: "reply", label: "开启回复", type: "bool", group: "开关", hint: "命中问题时自动接话", value: true },
      { key: "sendmode", label: "全局模式", type: "bool", group: "开关", hint: "使用标签词库或总词库回复", value: false },
      { key: "replychance", label: "回复概率", type: "int", group: "回复", min: 0, max: 100, unit: "%", value: 50 },
      { key: "replycd", label: "回复冷却", type: "int", group: "回复", min: 0, max: 600, unit: "秒", value: 3 },
      { key: "replywait", label: "随机等待", type: "pair", group: "回复", hint: "基准秒 + 浮动秒，发送前随机等待", value: [1, 0.5] },
      { key: "replylength", label: "答案最大字数", type: "int", group: "回复", min: 1, max: 2000, unit: "字", value: 100 },
      { key: "atreply", label: "被 @ 直接回复", type: "bool", group: "回复", value: true },
      { key: "interval", label: "词库链间隔", type: "int", group: "学习", min: 10, max: 86400, unit: "秒", value: 900 },
      { key: "mergetime", label: "总词库合并间隔", type: "int", group: "学习", min: 60, max: 86400, unit: "秒", value: 900 },
      { key: "cosmatch", label: "余弦相似度匹配", type: "bool", group: "匹配", value: false },
      { key: "cosmatching", label: "相似度阈值", type: "float", group: "匹配", min: 0, max: 1, step: 0.05, value: 0.5 },
      { key: "blackfreq", label: "黑名单容错次数", type: "int", group: "安全", min: 1, max: 100, unit: "次", value: 5 },
      { key: "tempmessagenum", label: "单群消息缓存", type: "int", group: "安全", min: 1, max: 500, unit: "条", value: 32 },
      { key: "fastdelete", label: "快速删除所有人可用", type: "bool", group: "安全", value: false },
      { key: "botname", label: "bot 昵称", type: "list", group: "其它", hint: "答案中的 {me} 会随机使用，空格或逗号分隔", value: ["我", "小助手"] },
      { key: "Administrator", label: "管理员 QQ", type: "list", group: "其它", hint: "空格或逗号分隔", value: ["10001"] },
    ],
    groups: ["开关", "回复", "学习", "匹配", "安全", "其它"],
    typefreq: { types: ["Plain", "Image"], values: { Plain: 0, Image: 0 } },
    readonly: {},
  },
  maintenanceLogs: [],
};

const startedAt = Math.floor(Date.now() / 1000) - 18420;

function stockEntries() {
  const entries = groupsPayload().groups.map((group) => ({
    name: group.group,
    label: "群 " + group.group,
    kind: "group",
    tags: group.tags,
    questions: group.questions,
    answers: group.answers,
  }));
  state.groups
    .filter((group) => group.kind !== "group")
    .forEach((group) => {
      const items = state.wordstock[group.name] || [];
      let answers = 0;
      items.forEach((item) => { answers += item.answers.length; });
      entries.push({
        name: group.name,
        label: group.label,
        kind: group.kind,
        tags: [],
        questions: items.length,
        answers,
      });
    });
  return entries;
}

function overview() {
  const stocks = stockEntries();
  const totalQuestions = stocks.reduce((sum, item) => sum + item.questions, 0);
  const totalAnswers = stocks.reduce((sum, item) => sum + item.answers, 0);
  const merge = stocks.find((item) => item.kind === "merge");
  return {
    plugin: { name: "astrbot_plugin_chat_learning" },
    runtime: {
      started_at: startedAt,
      uptime_seconds: Math.floor(Date.now() / 1000) - startedAt,
      data_dir: "AstrBot/data/plugin_data/astrbot_plugin_chat_learning",
      similarity_backend: "jieba",
      last_merge_at: "2026-11-17 19:55:12",
      last_merge_logs: state.maintenanceLogs.length ? state.maintenanceLogs : [
        "2026-11-17 19:55:12 词库合并完成：总词库 6 个问题",
        "标签词库 游戏：2 个问题（1 个群）",
      ],
    },
    status: {
      learning: Boolean(state.config.fields.find((f) => f.key === "learning").value),
      reply: Boolean(state.config.fields.find((f) => f.key === "reply").value),
      sendmode: Boolean(state.config.fields.find((f) => f.key === "sendmode").value),
      atreply: true,
      cosmatch: false,
      learning_groups: state.groupsConfig.learning.slice(),
      reply_groups: state.groupsConfig.reply.slice(),
      unmerge_groups: state.groupsConfig.unmerge.slice(),
      tags: Object.assign({}, state.groupsConfig.tags),
      subadmin: Object.assign({}, state.groupsConfig.subadmins),
      admins: ["10001"],
    },
    stocks,
    totals: {
      groups: stocks.filter((item) => item.kind === "group").length,
      questions: totalQuestions,
      answers: totalAnswers,
      merge_questions: merge ? merge.questions : 0,
      tag_stocks: stocks.filter((item) => item.kind === "tag").length,
    },
    filters: {
      filter: state.filters.filter.length,
      sensitive: state.filters.sensitive.length,
      blacklist: state.filters.blacklist.length,
      types: state.filters.types,
    },
    config_summary: {},
  };
}

function groupsPayload() {
  const config = state.groupsConfig;
  const names = new Set([
    ...config.learning,
    ...config.reply,
    ...config.unmerge,
    ...Object.keys(config.tags),
    ...Object.keys(config.subadmins),
    ...Object.keys(state.wordstock),
  ]);
  const groups = Array.from(names)
    .filter((name) => /^\d+$/.test(name))
    .sort((left, right) => Number(left) - Number(right))
    .map((name) => {
      const items = state.wordstock[name] || [];
      let answers = 0;
      items.forEach((item) => { answers += item.answers.length; });
      return {
        group: name,
        learning: config.learning.indexOf(name) !== -1,
        reply: config.reply.indexOf(name) !== -1,
        unmerge: config.unmerge.indexOf(name) !== -1,
        tags: (config.tags[name] || []).slice(),
        subadmins: (config.subadmins[name] || []).slice(),
        questions: items.length,
        answers,
        has_wordstock: Boolean(state.wordstock[name]),
      };
    });
  const tagSet = new Set();
  Object.keys(config.tags).forEach((key) => {
    config.tags[key].forEach((tag) => tagSet.add(tag));
  });
  return {
    groups,
    tags: Array.from(tagSet),
    totals: {
      groups: groups.length,
      learning: groups.filter((item) => item.learning).length,
      reply: groups.filter((item) => item.reply).length,
      tagged: groups.filter((item) => item.tags.length).length,
      unmerge: groups.filter((item) => item.unmerge).length,
      subadmin: groups.filter((item) => item.subadmins.length).length,
    },
  };
}

function wordstockPage(params) {
  const group = params.group && state.wordstock[params.group] ? params.group : "1001";
  const source = state.wordstock[group] || [];
  const queryText = (params.q || "").trim().toLowerCase();
  const filtered = source.filter((item) => {
    if (!queryText) return true;
    if (item.preview.toLowerCase().includes(queryText)) return true;
    return item.answers.some((answer) => answer.preview.toLowerCase().includes(queryText));
  });
  const pageSize = Math.min(100, Math.max(5, Number(params.page_size) || 20));
  const pages = filtered.length ? Math.ceil(filtered.length / pageSize) : 0;
  let page = Math.max(1, Number(params.page) || 1);
  if (pages) page = Math.min(page, pages);
  const start = (page - 1) * pageSize;
  return {
    groups: stockEntries(),
    group,
    query: params.q || "",
    page,
    page_size: pageSize,
    pages,
    total: filtered.length,
    items: filtered.slice(start, start + pageSize),
  };
}

function findQuestion(group, key) {
  return (state.wordstock[group] || []).find((item) => item.key === key);
}

function handleGet(path, params) {
  if (path === "dashboard/overview") return overview();
  if (path === "dashboard/groups") return groupsPayload();
  if (path === "dashboard/wordstock") return wordstockPage(params || {});
  if (path === "dashboard/filters") return state.filters;
  if (path === "dashboard/config") return state.config;
  throw new Error("mock 未实现的 GET：" + path);
}

async function handlePost(path, body) {
  if (path === "dashboard/wordstock/delete") {
    const group = state.wordstock[body.group];
    const question = findQuestion(body.group, body.question_key);
    if (!group || !question) throw new Error("问题不存在");
    if (body.answer_key) {
      question.answers = question.answers.filter((item) => item.key !== body.answer_key);
      if (!question.answers.length) {
        state.wordstock[body.group] = group.filter((item) => item.key !== body.question_key);
      }
    } else {
      state.wordstock[body.group] = group.filter((item) => item.key !== body.question_key);
    }
    return { deleted: 1, question_removed: true };
  }
  if (path === "dashboard/wordstock/weight") {
    const question = findQuestion(body.group, body.question_key);
    if (!question) throw new Error("问题不存在");
    const answer = question.answers.find((item) => item.key === body.answer_key);
    if (!answer) throw new Error("答案不存在");
    answer.same = Number(body.same);
    return { same: answer.same };
  }
  if (path === "dashboard/groups/save") {
    const group = String(body.group || "").trim();
    if (!/^\d{4,12}$/.test(group)) throw new Error("群号必须是 4-12 位数字");
    const config = state.groupsConfig;
    ["learning", "reply", "unmerge"].forEach((key) => {
      if (typeof body[key] !== "boolean") return;
      config[key] = config[key].filter((item) => item !== group);
      if (body[key]) config[key].push(group);
    });
    if (Array.isArray(body.tags)) {
      const tags = body.tags.map((tag) => String(tag).trim()).filter(Boolean);
      if (tags.length) config.tags[group] = tags;
      else delete config.tags[group];
    }
    if (Array.isArray(body.subadmins)) {
      const members = body.subadmins.map((item) => String(item).trim()).filter(Boolean);
      if (members.length) config.subadmins[group] = members;
      else delete config.subadmins[group];
    }
    return { saved: group };
  }
  if (path === "dashboard/groups/remove") {
    const group = String(body.group || "").trim();
    const config = state.groupsConfig;
    ["learning", "reply", "unmerge"].forEach((key) => {
      config[key] = config[key].filter((item) => item !== group);
    });
    delete config.tags[group];
    delete config.subadmins[group];
    if (body.delete_wordstock) delete state.wordstock[group];
    return { removed: group, wordstock_deleted: Boolean(body.delete_wordstock) };
  }
  if (path === "dashboard/filters/add") {
    if (body.kind === "filter") {
      state.filters.filter.push({
        index: state.filters.filter.length,
        preview: body.text,
        accurate: Boolean(body.accurate),
      });
    } else if (body.kind === "sensitive") {
      state.filters.sensitive.push({ index: state.filters.sensitive.length, preview: body.text });
    } else {
      throw new Error("mock 黑名单添加未实现");
    }
    return { added: body.text };
  }
  if (path === "dashboard/filters/remove") {
    if (body.kind === "filter") {
      state.filters.filter.splice(body.index, 1);
    } else if (body.kind === "sensitive") {
      state.filters.sensitive.splice(body.index, 1);
    } else {
      state.filters.blacklist = state.filters.blacklist.filter((item) => item.user !== body.user);
    }
    state.filters.filter.forEach((item, index) => { item.index = index; });
    state.filters.sensitive.forEach((item, index) => { item.index = index; });
    return { removed: 1 };
  }
  if (path === "dashboard/filters/types") {
    state.filters.types = body.types;
    return { types: body.types };
  }
  if (path === "dashboard/config/save") {
    (state.config.fields || []).forEach((field) => {
      if (Object.prototype.hasOwnProperty.call(body.values || {}, field.key)) {
        field.value = body.values[field.key];
      }
    });
    if (body.typefreq) {
      Object.keys(body.typefreq).forEach((key) => {
        state.config.typefreq.values[key] = body.typefreq[key];
      });
    }
    return { saved: Object.keys(body.values || {}) };
  }
  if (path === "dashboard/maintenance/merge") {
    state.maintenanceLogs = [
      "2026-11-17 20:05:01 词库合并完成：总词库 6 个问题",
      "标签词库 游戏：2 个问题（1 个群）",
    ];
    return { logs: state.maintenanceLogs };
  }
  if (path === "dashboard/maintenance/autodelete") {
    return { logs: ["已清理群1001：低频条目2个", "无需清理"] };
  }
  if (path === "dashboard/restore") {
    return { restored: ["config", "filters", "wordstock"], pre_backup: "chat_learning_before_restore_20261117_200512.json" };
  }
  throw new Error("mock 未实现的 POST：" + path);
}

function buildBridge() {
  const listeners = [];
  return {
    ready() {
      return Promise.resolve({
        pluginName: "astrbot_plugin_chat_learning",
        displayName: "学会你的群聊",
        pageName: "dashboard",
        pageTitle: "群学习面板",
        locale: "zh-CN",
        isDark,
        themeColors: { primary: "#3c96ca", secondary: "#2f86bd" },
      });
    },
    getContext() {
      return null;
    },
    t(key, fallback) {
      return fallback;
    },
    onContext(handler) {
      listeners.push(handler);
      return () => {
        const index = listeners.indexOf(handler);
        if (index !== -1) listeners.splice(index, 1);
      };
    },
    async apiGet(path, params) {
      return handleGet(path, params);
    },
    async apiPost(path, body) {
      return handlePost(path, body);
    },
    async download(path) {
      if (path !== "dashboard/backup") throw new Error("mock 未实现的下载");
      return { filename: "chat_learning_backup_mock.json" };
    },
    async upload() {
      throw new Error("mock 未实现上传");
    },
  };
}

window.AstrBotPluginView = buildBridge();
window.AstrBotPluginPage = window.AstrBotPluginView;
