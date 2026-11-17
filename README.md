# astrbot_plugin_chat_learning

让 bot 学会你所在群的说话方式的 AstrBot 插件，移植自 MiraiConsole 插件
[ChatLearning](https://github.com/JHue58/ChatLearning)。

插件会静默记录群里连续的两条消息（前一条为「问」，间隔内后一条为「答」），
在之后有人说出相同或相似的问题时，按答案权重随机抽一条学到的回答接话。

> 本项目是为 AstrBot（aiocqhttp 平台）重写的实现，不是原项目的直接打包；
> 功能与交互参考原项目，代码按 AstrBot 插件规范重新组织，并按 AGPL-3.0 保留原项目署名。

## 功能

- **记录问答**：词库链间隔内（默认 900 秒）的连续消息自动组成 问 -> 答，文本与图片原样保存
- **自动回复**：命中问题后按答案权重随机回复，支持回复概率、每群独立概率、冷却时间、随机等待
- **阈值**：每个答案有出现次数权重；可按消息类型设置回复阈值
- **相似度匹配**：jieba 分词 + 词频余弦，阀值可调，用于近似问题的兜底匹配
- **正则问题**：可把某个问题设为正则表达式，按内容匹配
- **群管理**：记录群/回复群分别开关，群标签（支持中文）、不录入总词库的群、群内子管理员
- **全局模式**：按标签词库或总词库回复，总词库定时由各分群词库合成
- **过滤**：过滤词（模糊/精确）、敏感词 + 黑名单容错次数、按消息类型过滤
- **管理模式**：私聊 bot 进入交互式管理，搜索/查看/按标记删除词库条目，维护过滤与黑名单，自动清理低频条目，添加自定义问答
- **快速删除**：发送 `/cl d`，或引用机器人消息后发送 `/cl d`（`/cl d 2` 删除倒数第二条），把刚才的回复从词库中删掉
- **可视化面板**：在 AstrBot WebUI 的插件详情页打开「群学习面板」，查看运行状态、管理词库与过滤、编辑配置、备份恢复，无需记指令

## 安装

1. 将本目录放到 `AstrBot/data/plugins/astrbot_plugin_chat_learning`
2. 在 WebUI 插件页重载插件（依赖会自动安装）
3. 在插件配置里填写 **Administrator**（管理员 QQ），或私聊 bot 发送 `/cl setadmin <你的QQ>` 完成初始化
4. 在 WebUI 插件详情页打开「群学习面板」，之后的可视化管理都在这里（AstrBot >= 4.30）

> 可选依赖 `jieba`：用于余弦相似度匹配。未安装时插件仍可运行，相似度匹配会退化为字符二元组。

## 快速开始

1. 私聊 bot 发送 `/cl add learnings <群号>`：同时开启该群的记录与回复
2. 发送 `/cl learning` 开启记录（或在插件配置里打开）
3. 让群友正常聊天，积累词库
4. 发送 `/cl reply` 开启回复
5. 发送 `/cl check` 查看词库规模与设置

群聊里需要先 @bot 或加唤醒前缀（AstrBot 默认 `/`）；私聊可直接发送 `cl ...` 或 `/cl ...`。
管理类指令仅 bot 管理员可用，群内子管理员可用本群开关/概率与 `/cl admin`。

## 指令

指令组为 `/cl`（别名 `/chatlearning`），完整指令名可在 AstrBot 的指令管理中查看。

| 指令 | 说明 |
| --- | --- |
| `/cl help` / `/cl ?` | 指令表 |
| `/cl check` | 词库问答数量与全部设置 |
| `/cl grouplist` | 各群开关状态 |
| `/cl learning` | 开启/关闭记录（群内：开关本群） |
| `/cl learning <秒>` | 设置词库链间隔 |
| `/cl reply` | 开启/关闭回复（群内：开关本群） |
| `/cl reply <百分比>` | 设置回复概率（群内：设置本群概率） |
| `/cl reply -s <百分比> <群号...>` / `/cl reply -d <群号...>` | 设置/移除指定群概率 |
| `/cl replywait <基准> <浮动>` | 回复前随机等待 |
| `/cl replycd <秒>` | 回复冷却 |
| `/cl replylength <字数>` | 答案最大长度 |
| `/cl cosmatch` / `/cl cosmatch <0-1>` | 相似度匹配开关 / 阈值 |
| `/cl globe` | 全局模式开关 |
| `/cl merge <秒>` | 总词库合成间隔 |
| `/cl typefreq <类型> <次数>` | 按消息类型的回复阈值 |
| `/cl settemp <条数>` | 每群消息缓存条数 |
| `/cl blackfreq <次数>` | 黑名单容错次数 |
| `/cl setadmin <QQ...>` | 设置管理员 |
| `/cl setbotname <昵称...>` | bot 昵称（答案中 `{me}` 使用） |
| `/cl atreply` | 被 @ 时是否直接回复 |
| `/cl fastdelete` | 快速删除权限 |
| `/cl add learning/learnings/reply <群号...>` | 添加记录群/记录+回复群/回复群 |
| `/cl add tag <标签> <群号...>` | 给群加标签（可多个，支持中文） |
| `/cl add unmerge <群号...>` | 该群不录入总词库 |
| `/cl add subadmin <群号> [QQ...]` | 设置群内子管理员 |
| `/cl remove learning/reply/unmerge/subadmin/tag <群号...>` | 移除 |
| `/cl admin` | 进入交互式管理模式 |
| `/cl d [序号]` | 删除机器人刚发的回复 |

群内子管理员指令：`/cl learning`、`/cl reply`、`/cl reply <概率>`、`/cl admin`

## 工作原理

- **记录**：群消息通过过滤/敏感词检查后，与同群上一条消息按时间间隔配对：
  间隔超过 `interval` 时该消息成为新的「问」；否则它既是「问」，也是上一条问题的「答」。
- **回复**：选择词库（分群 / 标签 / 总词库）→ 精确匹配 → 正则匹配 → 余弦相似度兜底；
  过滤低于类型阈值的答案后按权重随机抽取，检查概率、冷却、长度与过滤规则后发送。
- **合并**：后台定时把各分群词库合并为 `Merge`（跳过带标签的群和不录入的群），并为每个标签生成独立词库。

## 数据目录

```
data/plugin_data/astrbot_plugin_chat_learning/
├── config.json          # 运行时配置（指令修改后写入，仅保存与默认值不同的项）
├── filter.json          # 过滤词 / 敏感词 / 黑名单
├── backups/             # 面板导出的备份（最多保留 5 份）
├── images/              # 学到的图片（按 sha1 去重）
└── wordstock/
    ├── <群号>.json      # 分群词库
    ├── Merge.json       # 总词库
    └── <标签>.json      # 标签词库
```

词库为可读 JSON，每个问题包含出现次数、记录时间、是否正则以及答案列表（含权重与时间）。
所有持久化数据（配置、过滤词、图片、词库）都保存在 AstrBot 的 `data` 目录下，
插件目录保持只读，升级或重装插件不会覆盖数据。

## 可视化面板

在 AstrBot WebUI 的插件详情页打开 **群学习面板**，单页多标签，亮暗色跟随 WebUI：

- **状态**：群列表与词库（含已配置但尚无词库的群）、记录/回复开关、运行信息、最近合并日志；可手动合并词库、清理低频条目
- **群组**：群列表管理，开关记录/回复/不合并，增删标签与子管理员，移除群配置或删除群词库
- **词库**：按群/标签/总词库切换，搜索问题或答案，分页浏览，删除问题/答案，直接调整答案权重
- **过滤**：过滤词（模糊/精确）、敏感词、黑名单增删，按消息类型过滤
- **设置**：回复、学习、匹配、安全等核心配置表单，保存后立即生效
- **备份**：下载包含配置、过滤规则与词库的 JSON；导入时可选恢复范围与合并/覆盖方式，恢复前自动备份当前数据

面板每 5 秒轮询状态页，其他标签切换时按需加载。开发预览时用任意静态服务器
（例如 `python -m http.server`）打开 `views/dashboard/index.html?mock=1`，
不带 AstrBot 时会使用 `dev-mock.js` 的示例数据。
后端接口注册在插件 Web API 前缀 `/astrbot_plugin_chat_learning/dashboard/` 下。

## 与原版的差异

- 仅支持 aiocqhttp 平台，消息只保留 **文本 + 图片**（@、表情、引用等不记录）
- 去掉依赖作者服务器的 **文字转语音**、**定时任务**、**Excel 导入导出** 功能
- 命令改为 AstrBot 标准指令组 `/cl <子指令>`（别名 `/chatlearning`），管理模式为交互式 `/cl admin`；不再使用原版的 `!指令` 与控制台 TUI
- 词库使用 JSON，不再兼容原版 `.cl` pickle 文件
- 新增：`{me}` / `{name}` / `{segment}` 占位符沿用原版语义；修复了原版答案文本含花括号会抛异常的问题
- 新增：AstrBot WebUI 的「群学习面板」，用于状态查看、群列表/词库/过滤/配置管理与备份恢复

## 开发与测试

```bash
python tests/test_core.py               # 核心逻辑（学习/回复/过滤/合并/管理模式/指令）
python tests/test_plugin_integration.py # 带 AstrBot 桩模块的事件路由端到端测试
python tests/test_dashboard.py          # 面板业务层（群列表/词库/过滤/配置/备份恢复）
python tests/test_dashboard_web.py      # 面板 Web API 注册与响应（桩模块）
python tests/test_rounds.py             # 不同配置下的记录/回复轮次模拟（打印对照表）

# 代码规范：ruff（配置见 pyproject.toml，与 AstrBot 主仓库一致）
python -m ruff check .
python -m ruff format --check .
```

## 灵感来源与许可

- 功能、交互与算法思路来自 [ChatLearning](https://github.com/JHue58/ChatLearning)（作者 JHue58）；
  本项目是面向 AstrBot + aiocqhttp 的重写移植，不是原项目代码的直接打包。
- 原项目是 MiraiConsole 插件，最后更新于 2024 年 1 月，无法通过 PR 直接支持 AstrBot，
  因此以独立插件形式发布；如果原作者计划支持 AstrBot，欢迎改为向其提交 PR。
- 原项目与本项目均以 **GNU AGPL-3.0** 发布：原项目版权归其作者所有，
  本插件保留同样的许可与署名（见 [LICENSE](LICENSE)），并在此说明代码来源。
- 插件框架： [AstrBot](https://github.com/AstrBotDevs/AstrBot)
