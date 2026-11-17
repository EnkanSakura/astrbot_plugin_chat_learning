"""ChatLearning：让 bot 学会你所在群的说话方式。

移植自 MiraiConsole 插件 ChatLearning（https://github.com/JHue58/ChatLearning），
适配 AstrBot + aiocqhttp：记录群里的问答对，在合适的时候用学到的答案回复。
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import At, Plain
from astrbot.api.star import Context, Star, StarTools
from astrbot.core.message.message_event_result import MessageChain

from .core.admin import GROUP_MENU_TEXT, MENU_TEXT, AdminSession
from .core.commands import CommandContext, CommandService
from .core.config import DEFAULTS, PluginConfig
from .core.fastdelete import FastDeleteService, ReplyCache
from .core.filtering import FilterStore
from .core.learning import LearningEngine
from .core.merge import rebuild_all
from .core.message_io import ImageStore, MessageCodec
from .core.models import content_preview
from .core.reply import ReplyEngine
from .core.storage import StockManager
from .core.utils import split_command, strip_command_prefix, to_int

PLUGIN_NAME = "astrbot_plugin_chat_learning"
COMMAND_PREFIXES = ("!", "！")


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
        logger.info("ChatLearning 已启动，数据目录：%s", self.data_dir)
        logger.info("ChatLearning 相似度分词后端：%s", self._similarity_backend())
        if not self.config.admins():
            logger.warning(
                "ChatLearning 尚未设置管理员，请在 WebUI 插件配置的 Administrator 中填写，"
                "或私聊 bot 发送 !setadmin <QQ号> 完成初始化"
            )
        self._merge_task = asyncio.create_task(self._merge_loop())

    async def terminate(self) -> None:
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

    # ---- 事件入口 ---------------------------------------------------
    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_message(self, event: AstrMessageEvent) -> None:
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

    # ---- 群消息 -----------------------------------------------------
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

        subadmins = [
            str(item)
            for item in ((self.config.get("subadmin") or {}).get(group_id) or [])
        ]
        is_subadmin = sender_id in subadmins

        if text.startswith(COMMAND_PREFIXES):
            name, args = split_command(strip_command_prefix(text))
            if name in ("d", "delete"):
                return await self._handle_fast_delete(event, group_id, sender_id, args)
            if is_subadmin and name == "admin":
                self.sessions[session_key] = AdminSession(
                    self.config,
                    self.stocks,
                    self.filters,
                    self.commands,
                    scope_group=group_id,
                )
                return [Delivery(self._text_chain(GROUP_MENU_TEXT))]
            if is_subadmin and name in ("learning", "reply"):
                outputs = await self.commands.handle_group(text, group_id, sender_id)
                return [Delivery(self._text_chain(line)) for line in outputs if line]
            return []

        await self.learning.on_group_message(
            group_id, content, raw_types, sender_id, timestamp
        )
        return await self._maybe_reply(event, group_id, sender_id, content)

    async def _handle_fast_delete(
        self,
        event: AstrMessageEvent,
        group_id: str,
        sender_id: str,
        args: str,
    ) -> list[Delivery]:
        if not self.fastdelete.allowed(sender_id):
            return []
        target: str | None = None
        index = to_int(args)
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
        return [Delivery(self._text_chain(message))]

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

    # ---- 私聊 -------------------------------------------------------
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

        if not text.startswith(COMMAND_PREFIXES):
            return []
        body = strip_command_prefix(text)
        name, _args = split_command(body)
        is_admin = self._is_admin(sender_id)

        if name == "setadmin" and not self.config.admins():
            logger.warning("ChatLearning: 首次初始化管理员 %s", sender_id)
            outputs = await self.commands.handle(
                body, self._command_context(event, sender_id, is_admin=True)
            )
            return [Delivery(self._text_chain(line)) for line in outputs if line]

        if not is_admin:
            return []

        if name == "admin":
            self.sessions[session_key] = AdminSession(
                self.config, self.stocks, self.filters, self.commands
            )
            return [Delivery(self._text_chain(MENU_TEXT))]

        outputs = await self.commands.handle(
            body, self._command_context(event, sender_id, is_admin=True)
        )
        return [Delivery(self._text_chain(line)) for line in outputs if line]

    def _command_context(
        self, event: AstrMessageEvent, sender_id: str, is_admin: bool
    ) -> CommandContext:
        return CommandContext(
            sender_id=sender_id,
            sender_name=event.get_sender_name() or "",
            is_admin=is_admin,
            is_private=True,
            event=event,
        )

    # ---- 工具 -------------------------------------------------------
    def _seed(self) -> dict[str, Any]:
        return {
            key: value
            for key, value in self.settings.items()
            if key in DEFAULTS and value is not None
        }

    def _resolve_data_dir(self) -> Path:
        try:
            return Path(StarTools.get_data_dir(PLUGIN_NAME))
        except Exception:  # noqa: BLE001 - 回退到常规数据目录
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
