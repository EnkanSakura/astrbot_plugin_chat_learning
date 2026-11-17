"""ChatLearning：让 bot 学会你所在群的说话方式。

移植自 MiraiConsole 插件 ChatLearning（https://github.com/JHue58/ChatLearning），
适配 AstrBot + aiocqhttp：记录群里的问答对，在合适的时候用学到的答案回复。

管理指令统一使用 AstrBot 标准指令组：/cl <子指令>（别名 /chatlearning）。

本项目以 GNU AGPL-3.0 发布（见 LICENSE）；功能与交互移植自 ChatLearning
（https://github.com/JHue58/ChatLearning，AGPL-3.0），原项目版权归其作者所有。
"""

from __future__ import annotations

import asyncio
import functools
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import At, Plain
from astrbot.api.star import Context, Star, StarTools
from astrbot.core.message.message_event_result import MessageChain
from astrbot.core.star.filter.command import GreedyStr

from .core.admin import GROUP_MENU_TEXT, MENU_TEXT, AdminSession
from .core.commands import HELP_TEXT, CommandContext, CommandService
from .core.config import DEFAULTS, PluginConfig
from .core.fastdelete import FastDeleteService, ReplyCache
from .core.filtering import FilterStore
from .core.learning import LearningEngine
from .core.merge import rebuild_all
from .core.message_io import ImageStore, MessageCodec
from .core.models import content_preview
from .core.reply import ReplyEngine
from .core.storage import StockManager
from .core.utils import split_command, to_int

PLUGIN_NAME = "astrbot_plugin_chat_learning"
COMMAND_GROUP_NAMES = ("cl", "chatlearning")
NO_PERMISSION_TEXT = (
    "该指令仅 bot 管理员可用。可在插件配置的 Administrator 中填写，"
    "或私聊 bot 发送 /cl setadmin <你的QQ>"
)
GROUP_ONLY_TEXT = "该指令只能在群聊中使用"


def _safe_command(func):
    """包装指令实现：异常只记录日志并回复提示，避免单个错误影响 bot 运行。"""

    @functools.wraps(func)
    async def wrapper(self, *args, **kwargs):
        """执行被包装的指令方法，异常时返回可读提示。"""
        try:
            return await func(self, *args, **kwargs)
        except Exception:  # noqa: BLE001 - 指令失败不应中断 bot
            logger.exception("ChatLearning: 指令 %s 执行失败", func.__name__)
            return ["指令执行失败，详情见 AstrBot 日志"]

    return wrapper


@dataclass
class Delivery:
    """一条待发送的消息；cache 非空时发送成功后登记进快速删除缓存。"""

    chain: MessageChain
    cache: tuple[str, list[dict], list[dict]] | None = None


class ChatLearningPlugin(Star):
    def __init__(self, context: Context, config: Any = None) -> None:
        super().__init__(context)
        self.settings = dict(config) if config is not None else {}
        self.data_dir = self._resolve_data_dir()
        self.data_dir.mkdir(parents=True, exist_ok=True)

        self.config = PluginConfig(self.data_dir / "config.json", seed=self._seed())
        self.stocks = StockManager(self.data_dir)
        self.filters = FilterStore(self.data_dir / "filter.json")
        self.images = ImageStore(self.stocks.images_dir)
        self.codec = MessageCodec(self.images)

        self.learning = LearningEngine(self.config, self.stocks, self.filters)
        self.reply_engine = ReplyEngine(self.config, self.stocks, self.filters)
        self.reply_cache = ReplyCache(self.config)
        self.fastdelete = FastDeleteService(self.config, self.stocks, self.reply_cache)
        self.commands = CommandService(
            self.config,
            self.stocks,
            self.filters,
            self.learning,
            self.reply_engine,
            self.fastdelete,
            resolve_group_admins=self._resolve_group_admins,
        )

        self.sessions: dict[str, AdminSession] = {}
        self._cooldown: dict[str, float] = {}
        self._merge_task: asyncio.Task | None = None

    # ---- 生命周期 ---------------------------------------------------
    async def initialize(self) -> None:
        """插件加载：记录数据目录，并启动总词库合并任务。"""
        logger.info("ChatLearning 已启动，数据目录：%s", self.data_dir)
        logger.info("ChatLearning 相似度分词后端：%s", self._similarity_backend())
        if not self.config.admins():
            logger.warning(
                "ChatLearning 尚未设置管理员，请在 WebUI 插件配置的 Administrator 中填写，"
                "或私聊 bot 发送 /cl setadmin <QQ号> 完成初始化"
            )
        self._merge_task = asyncio.create_task(self._merge_loop())

    async def terminate(self) -> None:
        """插件卸载：取消后台任务并把内存中的词库刷盘。"""
        task = self._merge_task
        self._merge_task = None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        self.sessions.clear()
        try:
            self.learning.flush()
        except Exception:  # noqa: BLE001 - 停用时尽量保存
            logger.exception("ChatLearning: 保存词库失败")
        logger.info("ChatLearning 已停止")

    async def _merge_loop(self) -> None:
        await asyncio.sleep(8)
        while True:
            try:
                await asyncio.to_thread(self.learning.flush)
                logs = await asyncio.to_thread(rebuild_all, self.config, self.stocks)
                for line in logs:
                    logger.debug("ChatLearning: %s", line)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - 后台任务不能崩
                logger.exception("ChatLearning: 词库合并失败")
            interval = max(60, self.config.as_int("mergetime") or 900)
            try:
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                raise

    # ---- 标准指令组 -------------------------------------------------
    @filter.command_group("cl", alias={"chatlearning"})
    def cl(self) -> None:
        """ChatLearning 群学习：记录群问答并在合适的时候接话"""

    @cl.command("help", alias={"?"})
    async def cl_help(self, event: AstrMessageEvent):
        """查看指令表"""
        yield event.plain_result(HELP_TEXT)

    @cl.command("check")
    async def cl_check(self, event: AstrMessageEvent):
        """查看词库统计与全部设置"""
        for line in await self._admin_command("check", event):
            yield event.plain_result(line)

    @cl.command("grouplist")
    async def cl_grouplist(self, event: AstrMessageEvent):
        """查看各群开关状态"""
        for line in await self._admin_command("grouplist", event):
            yield event.plain_result(line)

    @cl.command("learning")
    async def cl_learning(self, event: AstrMessageEvent, args: GreedyStr):
        """开启/关闭记录；带秒数则设置词库链间隔；群内用于开关本群"""
        for line in await self._learning_command(str(args), event):
            yield event.plain_result(line)

    @cl.command("reply")
    async def cl_reply(self, event: AstrMessageEvent, args: GreedyStr):
        """开启/关闭回复；带百分比设置概率；群内设置本群概率"""
        for line in await self._reply_command(str(args), event):
            yield event.plain_result(line)

    @cl.command("replywait")
    async def cl_replywait(self, event: AstrMessageEvent, args: GreedyStr):
        """设置回复前的随机等待：<基准秒> <浮动秒>"""
        for line in await self._admin_command(f"replywait {args}", event):
            yield event.plain_result(line)

    @cl.command("replycd")
    async def cl_replycd(self, event: AstrMessageEvent, args: GreedyStr):
        """设置每个群的回复冷却时间（秒）"""
        for line in await self._admin_command(f"replycd {args}", event):
            yield event.plain_result(line)

    @cl.command("replylength")
    async def cl_replylength(self, event: AstrMessageEvent, args: GreedyStr):
        """设置单条答案文本的最大字数"""
        for line in await self._admin_command(f"replylength {args}", event):
            yield event.plain_result(line)

    @cl.command("cosmatch")
    async def cl_cosmatch(self, event: AstrMessageEvent, args: GreedyStr):
        """开启/关闭余弦相似度匹配；带 0-1 数值则设置阈值"""
        for line in await self._admin_command(f"cosmatch {args}", event):
            yield event.plain_result(line)

    @cl.command("globe")
    async def cl_globe(self, event: AstrMessageEvent):
        """开启/关闭全局模式"""
        for line in await self._admin_command("globe", event):
            yield event.plain_result(line)

    @cl.command("merge")
    async def cl_merge(self, event: AstrMessageEvent, args: GreedyStr):
        """设置总词库合成间隔（秒）"""
        for line in await self._admin_command(f"merge {args}", event):
            yield event.plain_result(line)

    @cl.command("typefreq")
    async def cl_typefreq(self, event: AstrMessageEvent, args: GreedyStr):
        """设置某类消息的回复阈值：<类型> <次数>"""
        for line in await self._admin_command(f"typefreq {args}", event):
            yield event.plain_result(line)

    @cl.command("settemp")
    async def cl_settemp(self, event: AstrMessageEvent, args: GreedyStr):
        """设置单群消息缓存条数"""
        for line in await self._admin_command(f"settemp {args}", event):
            yield event.plain_result(line)

    @cl.command("blackfreq")
    async def cl_blackfreq(self, event: AstrMessageEvent, args: GreedyStr):
        """设置黑名单容错次数"""
        for line in await self._admin_command(f"blackfreq {args}", event):
            yield event.plain_result(line)

    @cl.command("setadmin")
    async def cl_setadmin(self, event: AstrMessageEvent, args: GreedyStr):
        """设置管理员 QQ（多个用空格隔开）"""
        for line in await self._admin_command(f"setadmin {args}", event):
            yield event.plain_result(line)

    @cl.command("setbotname")
    async def cl_setbotname(self, event: AstrMessageEvent, args: GreedyStr):
        """设置 bot 昵称（答案中的 {me} 会使用）"""
        for line in await self._admin_command(f"setbotname {args}", event):
            yield event.plain_result(line)

    @cl.command("atreply")
    async def cl_atreply(self, event: AstrMessageEvent):
        """开启/关闭艾特回复"""
        for line in await self._admin_command("atreply", event):
            yield event.plain_result(line)

    @cl.command("fastdelete")
    async def cl_fastdelete(self, event: AstrMessageEvent):
        """快速删除权限：所有人 / 仅管理员"""
        for line in await self._admin_command("fastdelete", event):
            yield event.plain_result(line)

    @cl.command("add")
    async def cl_add(self, event: AstrMessageEvent, args: GreedyStr):
        """添加记录/回复群、标签、不录入总词库的群、子管理员"""
        for line in await self._admin_command(f"add {args}", event):
            yield event.plain_result(line)

    @cl.command("remove")
    async def cl_remove(self, event: AstrMessageEvent, args: GreedyStr):
        """移除记录/回复群、标签、不录入总词库的群、子管理员"""
        for line in await self._admin_command(f"remove {args}", event):
            yield event.plain_result(line)

    @cl.command("admin")
    async def cl_admin(self, event: AstrMessageEvent):
        """进入交互式管理模式"""
        for line in await self._admin_session_command(event):
            yield event.plain_result(line)

    @cl.command("delete", alias={"d"})
    async def cl_delete(self, event: AstrMessageEvent, args: GreedyStr):
        """删除机器人刚发的回复；可带序号或引用该消息"""
        for line in await self._fast_delete_command(str(args), event):
            yield event.plain_result(line)

    # ---- 消息监听（学习与回复） -------------------------------------
    @filter.event_message_type(filter.EventMessageType.ALL, priority=-1)
    async def on_message(self, event: AstrMessageEvent) -> None:
        """监听所有消息：管理模式会话、学习与自动回复；标准指令交给指令处理器。"""
        if self._is_command_event(event):
            return
        if self._is_unknown_group_command(event):
            if not getattr(event, "_has_send_oper", False):
                await self._send_chain(
                    event, self._text_chain("未知子指令，发送 /cl help 查看指令表")
                )
            event.stop_event()
            return
        try:
            deliveries = await self._dispatch(event)
        except Exception:  # noqa: BLE001 - 单条消息失败不影响后续
            logger.exception("ChatLearning: 处理消息失败")
            return
        if not deliveries:
            return
        for delivery in deliveries:
            message_id = await self._send_chain(event, delivery.chain)
            if delivery.cache and message_id:
                group_id, question_content, answer_content = delivery.cache
                self.reply_cache.remember(
                    group_id, message_id, question_content, answer_content
                )
        event.stop_event()

    async def _dispatch(self, event: AstrMessageEvent) -> list[Delivery]:
        text = (event.message_str or "").strip()
        sender_id = str(event.get_sender_id() or "")
        self_id = str(event.get_self_id() or "")
        if not sender_id or (self_id and sender_id == self_id):
            return []
        group_id = str(event.get_group_id() or "")
        content, raw_types = await self.codec.content_from_event(event)
        timestamp = self.codec.message_timestamp(event, int(time.time()))
        if group_id:
            return await self._handle_group(
                event, group_id, sender_id, text, content, raw_types, timestamp
            )
        return await self._handle_private(event, sender_id, text, content)

    async def _handle_group(
        self,
        event: AstrMessageEvent,
        group_id: str,
        sender_id: str,
        text: str,
        content: list[dict],
        raw_types: set[str],
        timestamp: int,
    ) -> list[Delivery]:
        session_key = f"group:{group_id}:{sender_id}"
        session = self.sessions.get(session_key)
        if session is not None:
            if session.closed:
                self.sessions.pop(session_key, None)
            else:
                outputs = await session.feed(text, content)
                if session.closed:
                    self.sessions.pop(session_key, None)
                return [Delivery(self._text_chain(line)) for line in outputs if line]

        await self.learning.on_group_message(
            group_id, content, raw_types, sender_id, timestamp
        )
        return await self._maybe_reply(event, group_id, sender_id, content)

    async def _handle_private(
        self,
        event: AstrMessageEvent,
        sender_id: str,
        text: str,
        content: list[dict],
    ) -> list[Delivery]:
        session_key = f"private:{sender_id}"
        session = self.sessions.get(session_key)
        if session is not None:
            if session.closed:
                self.sessions.pop(session_key, None)
            else:
                outputs = await session.feed(text, content)
                if session.closed:
                    self.sessions.pop(session_key, None)
                return [Delivery(self._text_chain(line)) for line in outputs if line]
        return []

    async def _maybe_reply(
        self,
        event: AstrMessageEvent,
        group_id: str,
        sender_id: str,
        content: list[dict],
    ) -> list[Delivery]:
        if not content:
            return []
        if not self.config.as_bool("reply"):
            return []
        if not self.config.has_group("reply", group_id):
            return []
        now = time.time()
        cooldown = self.config.as_int("replycd")
        if cooldown > 0 and now - self._cooldown.get(group_id, 0.0) < cooldown:
            logger.debug("ChatLearning: 群%s 处于回复冷却中", group_id)
            return []

        at_bot = self.codec.detect_at_bot(event)
        if at_bot and not self.config.as_bool("atreply"):
            return []

        plan = self.reply_engine.lookup(group_id, content)
        if plan is None:
            return []
        if not at_bot and not self.reply_engine.roll_reply_chance(group_id):
            return []

        rendered = self.reply_engine.render_content(
            plan.answer.content, event.get_sender_name() or sender_id
        )
        if self.reply_engine.is_answer_filtered(rendered):
            logger.debug("ChatLearning: 答案命中过滤规则，取消发送")
            return []
        if not self.reply_engine.answer_within_length(rendered):
            logger.info("ChatLearning: 答案字数超过所设定字数,取消发送")
            return []

        wait = self.reply_engine.wait_seconds()
        if wait > 0:
            await asyncio.sleep(wait)

        chain = self.codec.build_chain(rendered)
        components = list(chain.chain)
        if at_bot:
            components = [At(qq=sender_id), Plain("\n")] + components

        question_content = plan.question.content if plan.question else content
        self._cooldown[group_id] = time.time()
        logger.info(
            "ChatLearning: 回复群%s，问题=%s",
            group_id,
            content_preview(question_content),
        )
        return [
            Delivery(
                MessageChain(components),
                cache=(group_id, question_content, rendered),
            )
        ]

    # ---- 指令实现 ---------------------------------------------------
    @_safe_command
    async def _admin_command(self, line: str, event: AstrMessageEvent) -> list[str]:
        """执行管理员指令；未配置管理员时允许首个 setadmin 作为初始化。"""
        sender_id = str(event.get_sender_id() or "")
        line = (line or "").strip()
        if not self._is_admin(sender_id):
            command_name = split_command(line)[0]
            if command_name == "setadmin" and not self.config.admins():
                logger.warning("ChatLearning: 首次初始化管理员 %s", sender_id)
            else:
                return [NO_PERMISSION_TEXT]
        if not line:
            return [HELP_TEXT]
        return await self.commands.handle(
            line, self._command_context(event, sender_id, is_admin=True)
        )

    @_safe_command
    async def _learning_command(self, args: str, event: AstrMessageEvent) -> list[str]:
        """处理 /cl learning：群内开关本群，管理员可设置全局间隔。"""
        sender_id = str(event.get_sender_id() or "")
        group_id = str(event.get_group_id() or "")
        arg = (args or "").strip()
        if group_id:
            if not self._can_manage_group(group_id, sender_id):
                return [NO_PERMISSION_TEXT]
            if not arg:
                enabled = self.config.toggle_group("learning", group_id)
                self.config.save()
                return ["已开启本群记录" if enabled else "已关闭本群记录"]
            if not self._is_admin(sender_id):
                return ["只有 bot 管理员可以设置全局词库链间隔"]
            return await self._admin_command(f"learning {arg}", event)
        return await self._admin_command(f"learning {arg}".strip(), event)

    @_safe_command
    async def _reply_command(self, args: str, event: AstrMessageEvent) -> list[str]:
        """处理 /cl reply：群内开关本群并设置本群概率，管理员可改全局配置。"""
        sender_id = str(event.get_sender_id() or "")
        group_id = str(event.get_group_id() or "")
        arg = (args or "").strip()
        if group_id:
            if not self._can_manage_group(group_id, sender_id):
                return [NO_PERMISSION_TEXT]
            if not arg:
                enabled = self.config.toggle_group("reply", group_id)
                self.config.save()
                return ["已开启本群回复" if enabled else "已关闭本群回复"]
            first = arg.split()[0]
            if not first.startswith("-"):
                chance = to_int(first)
                if chance is None or chance < 0 or chance > 100:
                    return ["参数错误，回复概率应为 0-100"]
                table = dict(self.config.get("singlereplychance") or {})
                table[str(group_id)] = chance
                self.config.set("singlereplychance", table)
                self.config.save()
                return [f"已设置本群回复概率{chance}%"]
            if self._is_admin(sender_id):
                return await self._admin_command(f"reply {arg}", event)
            return ["只有 bot 管理员可以修改全局回复配置"]
        return await self._admin_command(f"reply {arg}".strip(), event)

    @_safe_command
    async def _admin_session_command(self, event: AstrMessageEvent) -> list[str]:
        """处理 /cl admin：为管理员或群子管理员创建交互式管理会话。"""
        sender_id = str(event.get_sender_id() or "")
        group_id = str(event.get_group_id() or "")
        if group_id:
            if not self._can_manage_group(group_id, sender_id):
                return [NO_PERMISSION_TEXT]
            self.sessions[f"group:{group_id}:{sender_id}"] = AdminSession(
                self.config,
                self.stocks,
                self.filters,
                self.commands,
                scope_group=group_id,
            )
            return [GROUP_MENU_TEXT]
        if not self._is_admin(sender_id):
            return [NO_PERMISSION_TEXT]
        self.sessions[f"private:{sender_id}"] = AdminSession(
            self.config, self.stocks, self.filters, self.commands
        )
        return [MENU_TEXT]

    @_safe_command
    async def _fast_delete_command(
        self, args: str, event: AstrMessageEvent
    ) -> list[str]:
        """处理 /cl d：从词库删除机器人刚发的答案，并撤回该消息。"""
        group_id = str(event.get_group_id() or "")
        sender_id = str(event.get_sender_id() or "")
        if not group_id:
            return [GROUP_ONLY_TEXT]
        if not self.fastdelete.allowed(sender_id):
            return ["快速删除仅管理员可用（可在插件配置中放开所有人）"]
        target: str | None = None
        index = to_int((args or "").strip())
        if index is not None and index > 0:
            ok, message, target = self.fastdelete.delete_nth(group_id, index)
        else:
            quote_id = self._quote_id(event)
            if quote_id:
                ok, message = self.fastdelete.delete(group_id, quote_id)
                target = quote_id
            else:
                ok, message, target = self.fastdelete.delete_nth(group_id, 1)
        if ok and target:
            await self._recall(event, target)
        return [message]

    # ---- 工具 -------------------------------------------------------
    @staticmethod
    def _is_unknown_group_command(event: AstrMessageEvent) -> bool:
        """唤醒前缀后跟指令组名但没有匹配到子指令的消息。"""
        if not getattr(event, "is_at_or_wake_command", False):
            return False
        text = (getattr(event, "message_str", "") or "").strip()
        if not text:
            return False
        return text.split(maxsplit=1)[0] in COMMAND_GROUP_NAMES

    @staticmethod
    def _is_command_event(event: AstrMessageEvent) -> bool:
        """判断这条消息是否命中了某个标准指令（命中时不再当作聊天学习）。"""
        getter = getattr(event, "get_extra", None)
        if not callable(getter):
            return False
        try:
            return bool(getter("handlers_parsed_params"))
        except Exception:  # noqa: BLE001 - 事件实现差异时按普通消息处理
            return False

    def _seed(self) -> dict[str, Any]:
        """只把 WebUI 配置中与插件同名的项作为默认值种子。"""
        return {
            key: value
            for key, value in self.settings.items()
            if key in DEFAULTS and value is not None
        }

    def _resolve_data_dir(self) -> Path:
        """数据目录固定在 AstrBot 的 data 目录，插件更新/重装不会覆盖数据。"""
        try:
            return Path(StarTools.get_data_dir(PLUGIN_NAME))
        except Exception:  # noqa: BLE001 - 回退到显式拼接的 data 目录
            logger.exception("ChatLearning: 获取插件数据目录失败，尝试回退")
            try:
                from astrbot.api.util import get_astrbot_data_path

                return Path(get_astrbot_data_path()) / "plugin_data" / PLUGIN_NAME
            except Exception:  # noqa: BLE001
                return Path("data") / "plugin_data" / PLUGIN_NAME

    @staticmethod
    def _similarity_backend() -> str:
        from .core import similarity

        return similarity.backend_name()

    def _is_admin(self, sender_id: str) -> bool:
        if str(sender_id) in self.config.admins():
            return True
        try:
            admins = self.context.get_config().get("admins_id") or []
            return str(sender_id) in [str(item) for item in admins]
        except Exception:  # noqa: BLE001
            return False

    def _is_subadmin(self, group_id: str, sender_id: str) -> bool:
        table = self.config.get("subadmin") or {}
        members = table.get(str(group_id)) or []
        return str(sender_id) in [str(item) for item in members]

    def _can_manage_group(self, group_id: str, sender_id: str) -> bool:
        return self._is_admin(sender_id) or self._is_subadmin(group_id, sender_id)

    def _command_context(
        self, event: AstrMessageEvent, sender_id: str, is_admin: bool
    ) -> CommandContext:
        group_id = str(event.get_group_id() or "")
        return CommandContext(
            sender_id=sender_id,
            sender_name=event.get_sender_name() or "",
            group_id=group_id,
            is_admin=is_admin,
            is_private=not group_id,
            event=event,
        )

    @staticmethod
    def _text_chain(text: str) -> MessageChain:
        return MessageChain([Plain(str(text))])

    @staticmethod
    def _quote_id(event: AstrMessageEvent) -> str | None:
        import astrbot.api.message_components as Comp

        for component in event.get_messages() or []:
            if isinstance(component, Comp.Reply):
                return str(component.id)
        return None

    async def _send_chain(
        self, event: AstrMessageEvent, chain: MessageChain
    ) -> str | None:
        target = str(event.get_group_id() or event.get_sender_id() or "")
        bot = getattr(event, "bot", None)
        if bot is not None and target.isdigit():
            message_id = await self._send_via_onebot(event, bot, target, chain)
            if message_id is not None:
                return message_id
        try:
            await event.send(chain)
        except Exception:  # noqa: BLE001
            logger.exception("ChatLearning: 发送消息失败")
        return None

    async def _send_via_onebot(
        self, event: AstrMessageEvent, bot: Any, target: str, chain: MessageChain
    ) -> str | None:
        try:
            from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import (
                AiocqhttpMessageEvent,
            )

            segments = await AiocqhttpMessageEvent._parse_onebot_json(chain)
            if not segments:
                return None
            is_group = bool(event.get_group_id())
            action = "send_group_msg" if is_group else "send_private_msg"
            payload: dict[str, Any] = (
                {"group_id": int(target)} if is_group else {"user_id": int(target)}
            )
            payload["message"] = segments
            ret = await bot.call_action(action, **payload)
            if isinstance(ret, dict):
                message_id = ret.get("message_id")
                if message_id is not None:
                    return str(message_id)
        except Exception as exc:  # noqa: BLE001 - 回退到框架发送
            logger.debug("ChatLearning: 直接发送失败，回退到框架发送: %s", exc)
        return None

    async def _recall(self, event: AstrMessageEvent, message_id: str) -> None:
        bot = getattr(event, "bot", None)
        if bot is None or not str(message_id).isdigit():
            return
        try:
            await bot.call_action("delete_msg", message_id=int(message_id))
        except Exception as exc:  # noqa: BLE001
            logger.debug("ChatLearning: 撤回消息失败: %s", exc)

    async def _resolve_group_admins(
        self, group_id: str, ctx: CommandContext
    ) -> list[str]:
        event = getattr(ctx, "event", None)
        bot = getattr(event, "bot", None) if event is not None else None
        if bot is None or not str(group_id).isdigit():
            return []
        try:
            ret = await bot.call_action("get_group_member_list", group_id=int(group_id))
        except Exception as exc:  # noqa: BLE001
            logger.debug("ChatLearning: 获取群成员失败: %s", exc)
            return []
        members = ret.get("data") if isinstance(ret, dict) else ret
        result: list[str] = []
        for member in members or []:
            try:
                if str(member.get("role")) in ("owner", "admin"):
                    result.append(str(member.get("user_id")))
            except AttributeError:
                continue
        return result
