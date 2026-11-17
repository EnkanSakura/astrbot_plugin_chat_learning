"""聊天指令：管理员单行命令、群管理与开关设置。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from .config import PluginConfig
from .fastdelete import FastDeleteService
from .filtering import FilterStore
from .learning import LearningEngine
from .merge import rebuild_all
from .reply import ReplyEngine
from .storage import MERGE_STOCK, StockManager
from .utils import parse_id_list, split_command, to_int

SUPPORTED_TYPES = [
    "Plain",
    "Image",
    "Face",
    "Voice",
    "At",
    "AtAll",
    "Quote",
    "Poke",
    "Video",
    "File",
]

HELP_TEXT = """ChatLearning 指令表（AstrBot 标准指令组；群聊需先 @bot 或使用唤醒前缀，如 /cl help）
==== 基本 ====
/cl help                    查看本指令表
/cl check                   查看词库问答个数与属性设置
/cl grouplist               查看各群开关状态
/cl learning                开启/关闭记录（群内：开关本群）
/cl learning <秒>           设置词库链间隔时间
/cl reply                   开启/关闭回复（群内：开关本群）
/cl reply <百分比>          设置回复概率（群内：设置本群概率）
/cl reply -s <百分比> <群号...>  为指定群设置回复概率
/cl reply -d <群号...>           移除指定群的回复概率
/cl replywait <基准> <浮动>      设置回复等待时间（秒）
/cl replycd <秒>            设置回复冷却时间
/cl replylength <字数>      设置答案最大长度
/cl cosmatch                开启/关闭问题余弦相似度计算
/cl cosmatch <匹配率>       设置相似度阈值（0-1）
/cl globe                   开启/关闭全局模式
/cl merge <秒>              设置总词库合成间隔
/cl typefreq <类型> <次数>  设置某类消息的回复阈值
/cl settemp <条数>          设置单群消息缓存条数
/cl blackfreq <次数>        设置黑名单容错次数
/cl setadmin <QQ...>        设置管理员
/cl setbotname <昵称...>    设置 bot 昵称
/cl atreply                 开启/关闭艾特回复
/cl fastdelete              设置快速删除权限（所有人/仅管理员）
==== 群管理 ====
/cl add learning <群号...>     添加记录群
/cl add learnings <群号...>    同时添加记录与回复群
/cl add reply <群号...>        添加回复群
/cl add tag <标签> <群号...>   为群添加标签（可多个，支持中文）
/cl add unmerge <群号...>      该群不录入总词库
/cl add subadmin <群号> [QQ...] 设置可自主管理本群的成员
/cl remove learning|reply|unmerge|subadmin|tag <群号...>
==== 维护 ====
/cl admin                   进入管理模式（交互式搜索/删除/过滤/清理）
/cl d [序号]                删除机器人刚才的回复（可引用该消息）
==== 可视化面板 ====
WebUI 插件详情 > 群学习面板  查看状态、管理群列表/词库/过滤/配置、备份恢复"""


@dataclass
class CommandContext:
    sender_id: str
    sender_name: str = ""
    group_id: str = ""
    is_admin: bool = False
    is_private: bool = True
    is_subadmin: bool = False
    event: Any = None


def _toggle_value(arg: str) -> bool | None:
    text = (arg or "").strip().lower()
    if text in ("", "toggle"):
        return None
    if text in ("1", "on", "open", "开", "开启", "enable", "true", "yes"):
        return True
    if text in ("0", "off", "close", "关", "关闭", "disable", "false", "no"):
        return False
    return None


class CommandService:
    def __init__(
        self,
        config: PluginConfig,
        stocks: StockManager,
        filters: FilterStore,
        learning: LearningEngine,
        reply_engine: ReplyEngine,
        fastdelete: FastDeleteService,
        resolve_group_admins: Callable[[str, CommandContext], Awaitable[list[str]]]
        | None = None,
    ) -> None:
        self.config = config
        self.stocks = stocks
        self.filters = filters
        self.learning = learning
        self.reply_engine = reply_engine
        self.fastdelete = fastdelete
        self.resolve_group_admins = resolve_group_admins
        self._handlers = {
            "help": self._cmd_help,
            "?": self._cmd_help,
            "？": self._cmd_help,
            "check": self._cmd_check,
            "grouplist": self._cmd_grouplist,
            "learning": self._cmd_learning,
            "reply": self._cmd_reply,
            "replywait": self._cmd_replywait,
            "replycd": self._cmd_replycd,
            "replylength": self._cmd_replylength,
            "cosmatch": self._cmd_cosmatch,
            "globe": self._cmd_globe,
            "merge": self._cmd_merge,
            "typefreq": self._cmd_typefreq,
            "settemp": self._cmd_settemp,
            "blackfreq": self._cmd_blackfreq,
            "setadmin": self._cmd_setadmin,
            "setbotname": self._cmd_setbotname,
            "atreply": self._cmd_atreply,
            "fastdelete": self._cmd_fastdelete,
            "add": self._cmd_add,
            "remove": self._cmd_remove,
        }

    # ---- 入口 -------------------------------------------------------
    async def handle(self, text: str, ctx: CommandContext) -> list[str]:
        """执行一条管理指令，返回要发送的文本列表。"""
        text = (text or "").strip()
        if not text:
            return [HELP_TEXT]
        name, args = split_command(text)
        handler = self._handlers.get(name)
        if handler is None:
            return [f'未知指令"{name}"，请输入 /cl help 查看帮助']
        return await handler(args, ctx)

    # ---- 基础信息 ---------------------------------------------------
    async def _cmd_help(self, args: str, ctx: CommandContext) -> list[str]:
        return [HELP_TEXT]

    def summary_lines(self) -> list[str]:
        """生成 /cl check 的统计与设置文本。"""
        lines = ["==== 群词库统计 ===="]
        tags = set(self.config.all_tags())
        groups: list[str] = []
        for name in self.stocks.names():
            if name == MERGE_STOCK or name in tags:
                continue
            groups.append(name)
        if not groups:
            lines.append("暂无词库，先开启记录并添加群")
        for name in groups:
            questions, answers = self.stocks.get(name).stats()
            tags_of_group = self.config.tag_dict().get(name) or []
            extra = f" 标签:{'、'.join(tags_of_group)}" if tags_of_group else ""
            lines.append(f"词库{name}收集到问题{questions}个 答案{answers}个{extra}")
        lines.append("==== 设置 ====")
        lines.append(
            "记录功能：" + ("开启" if self.config.as_bool("learning") else "关闭")
        )
        lines.append(
            "回复功能：" + ("开启" if self.config.as_bool("reply") else "关闭")
        )
        lines.append(
            "全局模式：" + ("开启" if self.config.as_int("sendmode") else "关闭")
        )
        lines.append(
            "问题余弦相似度计算："
            + ("开启" if self.config.as_bool("cosmatch") else "关闭")
        )
        lines.append(
            "艾特回复：" + ("开启" if self.config.as_bool("atreply") else "关闭")
        )
        lines.append(f"回复触发概率：{self.config.as_int('replychance')}%")
        wait = self.config.get("replywait") or [0, 0]
        try:
            wait_base, wait_jitter = float(wait[0]), float(wait[1])
        except (TypeError, ValueError, IndexError):
            wait_base, wait_jitter = 0.0, 0.0
        lines.append(f"回复等待时间：{wait_base:g}±{wait_jitter:g}秒")
        lines.append(f"回复冷却时间：{self.config.as_int('replycd')}秒")
        lines.append(f"余弦相似度匹配率阈值：{self.config.as_float('cosmatching'):g}")
        lines.append(f"总词库合成间隔：{self.config.as_int('mergetime')}秒")
        lines.append(f"词库链间隔：{self.config.as_int('interval')}秒")
        lines.append(f"黑名单容错次数：{self.config.as_int('blackfreq')}次")
        lines.append(f"单个群最大消息缓存数：{self.config.as_int('tempmessagenum')}条")
        botname = self.config.get("botname") or []
        lines.append("bot昵称设定：" + "、".join(str(item) for item in botname))
        lines.append(f"回复答案最大长度限制：{self.config.as_int('replylength')}个字数")
        typefreq = self.config.get("typefreq") or {}
        if typefreq:
            lines.append("回复阈值设定：")
            for key, value in typefreq.items():
                lines.append(f"  {key}:{value}次")
        table = self.config.get("singlereplychance") or {}
        if table:
            lines.append("指定群回复触发概率：")
            for key, value in table.items():
                lines.append(f"  群{key}：{value}%")
        return lines

    async def _cmd_check(self, args: str, ctx: CommandContext) -> list[str]:
        return self.summary_lines()

    async def _cmd_grouplist(self, args: str, ctx: CommandContext) -> list[str]:
        learning = "、".join(self.config.group_list("learning")) or "无"
        reply = "、".join(self.config.group_list("reply")) or "无"
        subadmin = (
            "、".join(str(key) for key in (self.config.get("subadmin") or {}).keys())
            or "无"
        )
        unmerge = "、".join(self.config.group_list("unmerge")) or "无"
        admins = "、".join(self.config.admins()) or "未设置"
        return [
            f"管理员QQ号：{admins}",
            f"已开启记录的群：{learning}",
            f"已开启答复的群：{reply}",
            f"允许自主管理的群：{subadmin}",
            f"不录入总词库的群：{unmerge}",
        ]

    # ---- 开关与数值 -------------------------------------------------
    async def _cmd_learning(self, args: str, ctx: CommandContext) -> list[str]:
        arg = (args or "").strip()
        if arg and (to_int(arg) or 0) > 0:
            self.config.set("interval", to_int(arg))
            self.config.save()
            return [f"已设置词库链学习间隔时间{to_int(arg)}秒"]
        value = _toggle_value(arg)
        enabled = (not self.config.as_bool("learning")) if value is None else value
        self.config.set("learning", 1 if enabled else 0)
        self.config.save()
        if not enabled:
            self.learning.reset()
        return ["开始记录" if enabled else "停止记录"]

    async def _cmd_reply(self, args: str, ctx: CommandContext) -> list[str]:
        parts = (args or "").split()
        if not parts:
            enabled = not self.config.as_bool("reply")
            self.config.set("reply", 1 if enabled else 0)
            self.config.save()
            return ["开启回复功能" if enabled else "关闭回复功能"]
        if parts[0] in ("-s", "-d"):
            mode = parts[0]
            rest = parts[1:]
            if mode == "-s":
                if len(rest) < 2:
                    return ["参数错误，用法：reply -s <百分比> <群号...>"]
                chance = to_int(rest[0])
                if chance is None or chance <= 0 or chance > 100:
                    return ["参数错误，百分比应为 1-100"]
                groups = parse_id_list(" ".join(rest[1:]))
                if not groups:
                    return ["参数错误,群号为空"]
                table = dict(self.config.get("singlereplychance") or {})
                for group in groups:
                    table[group] = chance
                self.config.set("singlereplychance", table)
                self.config.save()
                return ["添加完毕"]
            groups = parse_id_list(" ".join(rest))
            if not groups:
                return ["参数错误,群号为空"]
            table = dict(self.config.get("singlereplychance") or {})
            if not table:
                return ["请先添加！"]
            missing = []
            for group in groups:
                if group in table:
                    table.pop(group)
                else:
                    missing.append(group)
            self.config.set("singlereplychance", table)
            self.config.save()
            lines = [f"群{group}\n不存在" for group in missing]
            lines.append("移除完毕")
            return lines
        chance = to_int(parts[0])
        if chance is None:
            value = _toggle_value(parts[0])
            if value is None:
                return ["参数错误，百分比应为 1-100"]
            self.config.set("reply", 1 if value else 0)
            self.config.save()
            return ["开启回复功能" if value else "关闭回复功能"]
        if chance <= 0 or chance > 100:
            return ["参数错误，百分比应为 1-100"]
        self.config.set("replychance", chance)
        self.config.save()
        return [f"已设置回复的触发概率{chance}%"]

    async def _cmd_replywait(self, args: str, ctx: CommandContext) -> list[str]:
        parts = (args or "").split()
        if len(parts) < 2:
            return ["参数错误，用法：replywait <基准时间> <浮动时间>"]
        try:
            base = float(parts[0])
            jitter = float(parts[1])
        except ValueError:
            return ["参数错误，请输入数字"]
        if base < 0 or jitter < 0:
            return ["时间不得为负数！"]
        if jitter > base:
            return ["浮动时间范围不得大于基准时间！"]
        if base > 4 or jitter > 2:
            return ["基准时间不得大于4秒，浮动时间不得大于2秒！"]
        self.config.set("replywait", [base, jitter])
        self.config.save()
        return [f"已将回复等待时间设置为{base:g}±{jitter:g}秒"]

    async def _cmd_replycd(self, args: str, ctx: CommandContext) -> list[str]:
        value = to_int(args)
        if value is None or value < 1:
            return ["参数错误"]
        self.config.set("replycd", value)
        self.config.save()
        return [f"回复的冷却时间已设置为{value}秒"]

    async def _cmd_replylength(self, args: str, ctx: CommandContext) -> list[str]:
        value = to_int(args)
        if value is None or value < 1:
            return ["参数错误"]
        self.config.set("replylength", value)
        self.config.save()
        return [f"答案最大长度已经设置为 {value}"]

    async def _cmd_cosmatch(self, args: str, ctx: CommandContext) -> list[str]:
        arg = (args or "").strip()
        if arg:
            try:
                threshold = float(arg)
            except ValueError:
                return ["参数错误,匹配率的数值区间为[0-1]"]
            if threshold < 0 or threshold > 1:
                return ["参数错误,匹配率的数值区间为[0-1]"]
            self.config.set("cosmatching", threshold)
            self.config.save()
            return [f"已将匹配率阈值设置为{threshold:g}"]
        enabled = not self.config.as_bool("cosmatch")
        self.config.set("cosmatch", 1 if enabled else 0)
        self.config.save()
        if enabled:
            return ["已开启问题余弦相似度计算,请注意,将会消耗较多的计算机资源"]
        return ["已关闭问题余弦相似度计算"]

    async def _cmd_globe(self, args: str, ctx: CommandContext) -> list[str]:
        enabled = not self.config.as_int("sendmode")
        self.config.set("sendmode", 1 if enabled else 0)
        self.config.save()
        return ["已开启全局模式" if enabled else "已关闭全局模式"]

    async def _cmd_merge(self, args: str, ctx: CommandContext) -> list[str]:
        value = to_int(args)
        if value is None or value <= 0:
            return ["参数错误"]
        self.config.set("mergetime", value)
        self.config.save()
        return [f"已设置总词库更新时间{value}秒"]

    async def _cmd_typefreq(self, args: str, ctx: CommandContext) -> list[str]:
        parts = (args or "").split()
        if len(parts) < 2:
            return ["参数错误，用法：typefreq <消息类型> <次数>"]
        type_name = next(
            (item for item in SUPPORTED_TYPES if item.lower() == parts[0].lower()), None
        )
        if type_name is None:
            return ["消息类型错误\n支持的类型：\n" + "、".join(SUPPORTED_TYPES)]
        value = to_int(parts[1])
        if value is None or value <= 0:
            return ["参数错误"]
        table = dict(self.config.get("typefreq") or {})
        table[type_name] = value
        self.config.set("typefreq", table)
        self.config.save()
        return [f"已设置{type_name}回复阈值{value}次"]

    async def _cmd_settemp(self, args: str, ctx: CommandContext) -> list[str]:
        value = to_int(args)
        if value is None or value < 1:
            return ["参数错误"]
        self.config.set("tempmessagenum", value)
        self.config.save()
        return [f"每个群的消息缓存已设置为{value}条"]

    async def _cmd_blackfreq(self, args: str, ctx: CommandContext) -> list[str]:
        value = to_int(args)
        if value is None or value < 1:
            return ["参数错误"]
        self.config.set("blackfreq", value)
        self.config.save()
        return [f"黑名单容错次数已设置为{value}次"]

    async def _cmd_setadmin(self, args: str, ctx: CommandContext) -> list[str]:
        admins = parse_id_list(args)
        if not admins:
            return ["参数错误，用法：setadmin <QQ号...>"]
        if any(not item.isdigit() for item in admins):
            return ["参数错误，管理员必须是QQ号"]
        self.config.set("Administrator", admins)
        self.config.save()
        return ["管理员QQ号已设置为 " + "、".join(admins)]

    async def _cmd_setbotname(self, args: str, ctx: CommandContext) -> list[str]:
        names = parse_id_list(args)
        if not names:
            return ["参数错误，用法：setbotname <昵称...>"]
        self.config.set("botname", names)
        self.config.save()
        return ["已将bot昵称设置为 " + "、".join(names)]

    async def _cmd_atreply(self, args: str, ctx: CommandContext) -> list[str]:
        enabled = not self.config.as_bool("atreply")
        self.config.set("atreply", 1 if enabled else 0)
        self.config.save()
        return ["已开启艾特回复" if enabled else "已关闭艾特回复"]

    async def _cmd_fastdelete(self, args: str, ctx: CommandContext) -> list[str]:
        value = _toggle_value(args)
        enabled = (not self.config.as_int("fastdelete")) if value is None else value
        self.config.set("fastdelete", 1 if enabled else 0)
        self.config.save()
        return (
            ["快速删除已设置为所有人可用"]
            if enabled
            else ["快速删除已设置为仅管理员可用"]
        )

    # ---- 群管理 -----------------------------------------------------
    async def _cmd_add(self, args: str, ctx: CommandContext) -> list[str]:
        kind, rest = split_command(args)
        if kind == "tag":
            return await self._add_tag(rest)
        groups = parse_id_list(rest)
        if not groups:
            return ["参数错误,群号为空"]
        if kind == "learning":
            self.config.add_groups("learning", groups)
            self.config.save()
            return ["添加完毕"]
        if kind == "learnings":
            self.config.add_groups("learning", groups)
            self.config.add_groups("reply", groups)
            self.config.save()
            return ["添加完毕 已同时加入回复列表"]
        if kind == "reply":
            self.config.add_groups("reply", groups)
            self.config.save()
            return ["添加完毕"]
        if kind == "unmerge":
            self.config.add_groups("unmerge", groups)
            self.config.save()
            return ["添加完毕"]
        if kind == "subadmin":
            return await self._add_subadmin(rest, ctx)
        return ["参数错误，支持：add learning|learnings|reply|tag|unmerge|subadmin"]

    async def _add_tag(self, rest: str) -> list[str]:
        parts = rest.split(maxsplit=1)
        if len(parts) < 2:
            return ["参数错误,群号为空"]
        tag = parts[0].strip()
        groups = parse_id_list(parts[1])
        if not tag or not groups:
            return ["参数错误,群号为空"]
        tags = self.config.tag_dict()
        for group in groups:
            values = tags.get(group, [])
            if tag not in values:
                values.append(tag)
            tags[group] = values
        self.config.set("tag", tags)
        self.config.save()
        rebuild_all(self.config, self.stocks)
        return [f"添加完毕，群{'、'.join(groups)} 已添加标签 {tag}"]

    async def _add_subadmin(self, rest: str, ctx: CommandContext) -> list[str]:
        parts = rest.split(maxsplit=1)
        group = parts[0].strip()
        if not group:
            return ["参数错误,群号为空"]
        members = parse_id_list(parts[1]) if len(parts) > 1 else []
        if not members and self.resolve_group_admins is not None:
            members = await self.resolve_group_admins(group, ctx)
        if not members:
            return ["未能获取该群管理员列表，请手动指定：add subadmin <群号> <QQ...>"]
        table = dict(self.config.get("subadmin") or {})
        current = [str(item) for item in (table.get(group) or [])]
        for member in members:
            if member not in current:
                current.append(member)
        table[group] = current
        self.config.set("subadmin", table)
        self.config.save()
        return [f"添加完毕，群{group} 子管理员：{'、'.join(current)}"]

    async def _cmd_remove(self, args: str, ctx: CommandContext) -> list[str]:
        kind, rest = split_command(args)
        groups = parse_id_list(rest)
        if kind == "tag":
            return await self._remove_tag(groups)
        if kind == "subadmin":
            return await self._remove_subadmin(groups)
        if not groups:
            return ["参数错误,群号为空"]
        mapping = {
            "learning": "learning",
            "reply": "reply",
            "unmerge": "unmerge",
        }
        target = mapping.get(kind)
        if target is None:
            return ["参数错误，支持：remove learning|reply|tag|unmerge|subadmin"]
        removed = self.config.remove_groups(target, groups)
        self.config.save()
        missing = [group for group in groups if group not in removed]
        lines = [f"群{group}\n不存在" for group in missing]
        lines.append("移除完毕")
        return lines

    async def _remove_tag(self, groups: list[str]) -> list[str]:
        if not groups:
            return ["参数错误,群号为空"]
        tags = self.config.tag_dict()
        if not tags:
            return ["请先添加！"]
        removed = []
        for group in groups:
            if group in tags:
                tags.pop(group)
                removed.append(group)
        self.config.set("tag", tags)
        self.config.save()
        rebuild_all(self.config, self.stocks)
        missing = [group for group in groups if group not in removed]
        lines = [f"群{group}\n不存在" for group in missing]
        lines.append("移除完毕")
        return lines

    async def _remove_subadmin(self, groups: list[str]) -> list[str]:
        if not groups:
            return ["参数错误,群号为空"]
        table = dict(self.config.get("subadmin") or {})
        if not table:
            return ["请先添加！"]
        removed = []
        for group in groups:
            if group in table:
                table.pop(group)
                removed.append(group)
        self.config.set("subadmin", table)
        self.config.save()
        missing = [group for group in groups if group not in removed]
        lines = [f"群{group}\n不存在" for group in missing]
        lines.append("移除完毕")
        return lines
