"""带 astrbot 桩模块的端到端测试：验证事件路由与指令/回复链路。

不依赖真实 AstrBot 依赖安装，直接运行 python tests/test_plugin_integration.py。
"""

from __future__ import annotations

import asyncio
import logging
import sys
import tempfile
import types
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parent.parent
PLUGINS_DIR = PLUGIN_DIR.parent


def _module(name: str, **attrs):
    mod = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(mod, key, value)
    sys.modules[name] = mod
    return mod


def install_stubs() -> None:
    astrbot = _module("astrbot")
    api = _module("astrbot.api", logger=logging.getLogger("test"))
    astrbot.api = api

    class Star:
        def __init__(self, context):
            self.context = context

    class StarTools:
        data_root = None

        @staticmethod
        def get_data_dir(name=None):
            return Path(StarTools.data_root)

    class Context:
        def get_config(self, umo=None):
            return {}

    star_mod = _module("astrbot.api.star", Star=Star, Context=Context, StarTools=StarTools)
    api.star = star_mod

    class EventMessageType:
        ALL = "ALL"
        GROUP_MESSAGE = "GROUP"
        PRIVATE_MESSAGE = "PRIVATE"

    def event_message_type(kind):
        def decorator(func):
            return func

        return decorator

    filter_mod = _module(
        "astrbot.api.event.filter",
        event_message_type=event_message_type,
        EventMessageType=EventMessageType,
    )

    class AstrMessageEvent:
        pass

    event_mod = _module(
        "astrbot.api.event", filter=filter_mod, AstrMessageEvent=AstrMessageEvent
    )
    api.event = event_mod

    class Plain:
        def __init__(self, text="", **kwargs):
            self.text = text

    class At:
        def __init__(self, qq="", **kwargs):
            self.qq = qq

    class AtAll(At):
        pass

    class Reply:
        def __init__(self, id="", **kwargs):
            self.id = id

    class Image:
        def __init__(self, file="", **kwargs):
            self.file = file
            self.path = kwargs.get("path", "")
            self.url = kwargs.get("url", "")

        @staticmethod
        def fromFileSystem(path, **kwargs):
            return Image(file=str(path))

    comp_mod = _module(
        "astrbot.api.message_components",
        Plain=Plain,
        At=At,
        AtAll=AtAll,
        Reply=Reply,
        Image=Image,
    )
    api.message_components = comp_mod

    core_mod = _module("astrbot.core")
    astrbot.core = core_mod
    message_mod = _module("astrbot.core.message")
    core_mod.message = message_mod

    class MessageChain:
        def __init__(self, chain=None):
            self.chain = list(chain or [])

        def __iter__(self):
            return iter(self.chain)

    mer = _module("astrbot.core.message.message_event_result", MessageChain=MessageChain)
    message_mod.message_event_result = mer
    astrbot.api.message_components = comp_mod


class FakeMessageObj:
    def __init__(self, timestamp):
        self.raw_message = {"time": timestamp}
        self.message_id = "mid-1"


class FakeEvent:
    def __init__(
        self,
        text,
        *,
        group_id="",
        sender_id="42",
        self_id="999",
        timestamp=1000,
        components=None,
    ):
        self.message_str = text
        self._group = group_id
        self._sender = sender_id
        self._self = self_id
        self.message_obj = FakeMessageObj(timestamp)
        self._components = (
            components
            if components is not None
            else ([sys.modules["astrbot.api.message_components"].Plain(text)] if text else [])
        )
        self.sent = []
        self.stopped = False

    def get_sender_id(self):
        return self._sender

    def get_self_id(self):
        return self._self

    def get_group_id(self):
        return self._group

    def get_sender_name(self):
        return "测试用户"

    def get_messages(self):
        return self._components

    async def send(self, chain):
        self.sent.append(chain)

    def stop_event(self):
        self.stopped = True


def chain_text(chain) -> str:
    return "".join(
        getattr(component, "text", "") for component in chain.chain
    )


PASSED: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if not condition:
        raise AssertionError(f"{name} 失败 {detail}")
    PASSED.append(name)


async def scenario(plugin) -> None:
    # 1. 首次 setadmin 引导
    event = FakeEvent("!setadmin 42")
    await plugin.on_message(event)
    check("引导：管理员已设置", plugin.config.admins() == ["42"])
    check("引导：有反馈消息", bool(event.sent))

    # 2. 群管理
    event = FakeEvent("!add learnings 100")
    await plugin.on_message(event)
    check("群管理：记录群", plugin.config.has_group("learning", "100"))
    check("群管理：回复群", plugin.config.has_group("reply", "100"))

    # 3. 打开记录并拒绝非管理员
    await plugin.on_message(FakeEvent("!learning on"))
    check("开关：记录已开启", plugin.config.as_bool("learning"))
    stranger = FakeEvent("!reply on", sender_id="77")
    await plugin.on_message(stranger)
    check("权限：非管理员被忽略", not stranger.sent and not plugin.config.as_bool("reply"))

    # 4. 群内学习两条消息
    await plugin.on_message(FakeEvent("你好", group_id="100", sender_id="1", timestamp=1000))
    await plugin.on_message(FakeEvent("你好呀", group_id="100", sender_id="2", timestamp=1010))
    stock = plugin.stocks.get("100")
    check("学习：词库落盘", stock.stats() == (2, 1))

    # 4b. 图片问答（间隔超过 interval，图片成为新的"问"）
    image_source = Path(plugin.data_dir) / "src.png"
    image_source.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
    image_component = sys.modules["astrbot.api.message_components"].Image(
        file="", path=str(image_source)
    )
    await plugin.on_message(
        FakeEvent("", group_id="100", sender_id="5", timestamp=3000, components=[image_component])
    )
    await plugin.on_message(FakeEvent("图片答案", group_id="100", sender_id="6", timestamp=3010))
    image_questions = [
        question
        for question in plugin.stocks.get("100").questions.values()
        if any(seg.get("t") == "image" for seg in question.content)
    ]
    check(
        "学习：图片问题已记录",
        len(image_questions) == 1 and len(image_questions[0].answers) == 1,
    )

    # 5. 关闭记录（避免待回答问题影响结果），打开回复并命中问题
    await plugin.on_message(FakeEvent("!learning off"))
    await plugin.on_message(FakeEvent("!reply on"))
    await plugin.on_message(FakeEvent("!reply 100"))
    event = FakeEvent("你好", group_id="100", sender_id="3", timestamp=1100)
    await plugin.on_message(event)
    check("回复：发送了答案", len(event.sent) == 1 and chain_text(event.sent[0]) == "你好呀")
    check("回复：阻止后续 LLM 处理", event.stopped)

    # 5b. 图片问题命中（同一张图片按 sha1 匹配；先等冷却）
    await plugin.on_message(FakeEvent("!replycd 1"))
    await asyncio.sleep(1.05)
    image_copy = Path(plugin.data_dir) / "copy.png"
    image_copy.write_bytes(image_source.read_bytes())
    image_event = FakeEvent(
        "",
        group_id="100",
        sender_id="7",
        timestamp=3100,
        components=[
            sys.modules["astrbot.api.message_components"].Image(file="", path=str(image_copy))
        ],
    )
    await plugin.on_message(image_event)
    check(
        "回复：图片问题命中",
        len(image_event.sent) == 1 and chain_text(image_event.sent[0]) == "图片答案",
    )

    # 6. 不匹配的问题不回复
    event = FakeEvent("完全无关的内容", group_id="100", sender_id="4", timestamp=1200)
    await plugin.on_message(event)
    check("回复：未命中不发送", not event.sent and not event.stopped)

    # 7. check 指令
    event = FakeEvent("!check")
    await plugin.on_message(event)
    text = "\n".join(chain_text(chain) for chain in event.sent)
    check("指令：check 输出统计", "词库100" in text and "回复触发概率" in text)

    # 8. 管理模式：搜索并删除
    event = FakeEvent("!admin")
    await plugin.on_message(event)
    check("管理：进入模式", any("管理模式" in chain_text(chain) for chain in event.sent))
    await plugin.on_message(FakeEvent("1"))
    event = FakeEvent("你好")
    await plugin.on_message(event)
    check("管理：搜索到结果", any("标记0" in chain_text(chain) for chain in event.sent))
    event = FakeEvent("0")
    await plugin.on_message(event)
    check("管理：删除条目", any("删除" in chain_text(chain) for chain in event.sent))
    remaining = [
        question
        for question in plugin.stocks.get("100").questions.values()
        if question.plain_text == "你好"
    ]
    check("管理：词库已变化", not remaining)

    # 9. 先退出管理模式，再执行指令
    await plugin.on_message(FakeEvent("0"))
    event = FakeEvent("!blackfreq 7")
    await plugin.on_message(event)
    check("指令：blackfreq 生效", plugin.config.as_int("blackfreq") == 7)


async def main() -> None:
    install_stubs()
    sys.path.insert(0, str(PLUGINS_DIR))
    with tempfile.TemporaryDirectory() as tmp:
        sys.modules["astrbot.api.star"].StarTools.data_root = tmp
        import astrbot_plugin_chat_learning.main as main_mod

        plugin = main_mod.ChatLearningPlugin(
            sys.modules["astrbot.api.star"].Context(), config={}
        )
        await scenario(plugin)
    print(f"全部通过：{len(PASSED)} 项")
    for name in PASSED:
        print("  ✓", name)


if __name__ == "__main__":
    asyncio.run(main())
