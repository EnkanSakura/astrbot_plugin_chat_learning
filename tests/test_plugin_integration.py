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

    star_mod = _module(
        "astrbot.api.star", Star=Star, Context=Context, StarTools=StarTools
    )
    api.star = star_mod

    class EventMessageType:
        ALL = "ALL"
        GROUP_MESSAGE = "GROUP"
        PRIVATE_MESSAGE = "PRIVATE"

    registered_commands: list[str] = []
    registered_groups: list[str] = []

    def event_message_type(kind, **kwargs):
        def decorator(func):
            return func

        return decorator

    class _Commandable:
        def __init__(self, name, alias=None):
            self.name = name
            self.alias = alias or set()

        def command(self, name=None, alias=None, **kwargs):
            def decorator(func):
                if name:
                    registered_commands.append(name)
                return func

            return decorator

    def command_group(name=None, alias=None, **kwargs):
        def decorator(func):
            if name:
                registered_groups.append(name)
            return _Commandable(name, alias)

        return decorator

    filter_mod = _module(
        "astrbot.api.event.filter",
        event_message_type=event_message_type,
        command_group=command_group,
        EventMessageType=EventMessageType,
        registered_commands=registered_commands,
        registered_groups=registered_groups,
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

    star_pkg = _module("astrbot.core.star")
    core_mod.star = star_pkg
    star_filter_pkg = _module("astrbot.core.star.filter")
    star_pkg.filter = star_filter_pkg

    class GreedyStr(str):
        pass

    command_filter_mod = _module(
        "astrbot.core.star.filter.command", GreedyStr=GreedyStr
    )
    star_filter_pkg.command = command_filter_mod

    class MessageChain:
        def __init__(self, chain=None):
            self.chain = list(chain or [])

        def __iter__(self):
            return iter(self.chain)

    mer = _module(
        "astrbot.core.message.message_event_result", MessageChain=MessageChain
    )
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
            else (
                [sys.modules["astrbot.api.message_components"].Plain(text)]
                if text
                else []
            )
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
    return "".join(getattr(component, "text", "") for component in chain.chain)


PASSED: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if not condition:
        raise AssertionError(f"{name} 失败 {detail}")
    PASSED.append(name)


async def scenario(plugin) -> None:
    # 1. 首次 setadmin 引导
    lines = await plugin._admin_command("setadmin 42", FakeEvent("cl setadmin 42"))
    check("引导：管理员已设置", plugin.config.admins() == ["42"])
    check("引导：有反馈消息", bool(lines))

    # 2. 群管理
    lines = await plugin._admin_command(
        "add learnings 100", FakeEvent("cl add learnings 100")
    )
    check("群管理：记录群", plugin.config.has_group("learning", "100"))
    check("群管理：回复群", plugin.config.has_group("reply", "100"))
    check("群管理：有反馈", bool(lines))

    # 3. 打开记录并拒绝非管理员
    await plugin._admin_command("learning on", FakeEvent("cl learning on"))
    check("开关：记录已开启", plugin.config.as_bool("learning"))
    lines = await plugin._admin_command(
        "reply on", FakeEvent("cl reply on", sender_id="77")
    )
    check(
        "权限：非管理员被拒绝",
        bool(lines) and "管理员" in lines[0] and not plugin.config.as_bool("reply"),
    )

    # 4. 群内学习两条消息
    await plugin.on_message(
        FakeEvent("你好", group_id="100", sender_id="1", timestamp=1000)
    )
    await plugin.on_message(
        FakeEvent("你好呀", group_id="100", sender_id="2", timestamp=1010)
    )
    stock = plugin.stocks.get("100")
    check("学习：词库落盘", stock.stats() == (2, 1))

    # 4b. 图片问答（间隔超过 interval，图片成为新的"问"）
    image_source = Path(plugin.data_dir) / "src.png"
    image_source.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
    image_component = sys.modules["astrbot.api.message_components"].Image(
        file="", path=str(image_source)
    )
    await plugin.on_message(
        FakeEvent(
            "",
            group_id="100",
            sender_id="5",
            timestamp=3000,
            components=[image_component],
        )
    )
    await plugin.on_message(
        FakeEvent("图片答案", group_id="100", sender_id="6", timestamp=3010)
    )
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
    await plugin._admin_command("learning off", FakeEvent("cl learning off"))
    await plugin._admin_command("reply on", FakeEvent("cl reply on"))
    await plugin._admin_command("reply 100", FakeEvent("cl reply 100"))
    event = FakeEvent("你好", group_id="100", sender_id="3", timestamp=1100)
    await plugin.on_message(event)
    check(
        "回复：发送了答案",
        len(event.sent) == 1 and chain_text(event.sent[0]) == "你好呀",
    )
    check("回复：阻止后续 LLM 处理", event.stopped)

    # 5b. 图片问题命中（同一张图片按 sha1 匹配；先等冷却）
    await plugin._admin_command("replycd 1", FakeEvent("cl replycd 1"))
    await asyncio.sleep(1.05)
    image_copy = Path(plugin.data_dir) / "copy.png"
    image_copy.write_bytes(image_source.read_bytes())
    image_event = FakeEvent(
        "",
        group_id="100",
        sender_id="7",
        timestamp=3100,
        components=[
            sys.modules["astrbot.api.message_components"].Image(
                file="", path=str(image_copy)
            )
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
    lines = await plugin._admin_command("check", FakeEvent("cl check"))
    text = "\n".join(lines)
    check("指令：check 输出统计", "词库100" in text and "回复触发概率" in text)

    # 8. 管理模式：/cl admin 进入，之后用普通消息交互
    lines = await plugin._admin_session_command(FakeEvent("cl admin"))
    check("管理：进入模式", any("管理模式" in line for line in lines))
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
    await plugin._admin_command("blackfreq 7", FakeEvent("cl blackfreq 7"))
    check("指令：blackfreq 生效", plugin.config.as_int("blackfreq") == 7)

    # 10. 标准指令组：所有子指令都已注册
    registered = set(sys.modules["astrbot.api.event.filter"].registered_commands)
    expected = {
        "help",
        "check",
        "grouplist",
        "learning",
        "reply",
        "replywait",
        "replycd",
        "replylength",
        "cosmatch",
        "globe",
        "merge",
        "typefreq",
        "settemp",
        "blackfreq",
        "setadmin",
        "setbotname",
        "atreply",
        "fastdelete",
        "add",
        "remove",
        "admin",
        "delete",
    }
    check("指令组：子指令全部注册", expected <= registered)

    filter_module = sys.modules["astrbot.api.event.filter"]
    check("指令组：注册为 cl 指令组", "cl" in filter_module.registered_groups)

    import inspect

    params = list(inspect.signature(plugin.cl_learning).parameters.values())
    check(
        "指令组：learning 使用 GreedyStr 且无默认值",
        params[-1].name == "args" and params[-1].default is inspect.Parameter.empty,
    )

    help_text = sys.modules["astrbot_plugin_chat_learning.core.commands"].HELP_TEXT
    check(
        "指令组：帮助文本为 /cl 格式",
        "/cl learning" in help_text and "!learning" not in help_text,
    )

    # 11. 错误处理：指令实现抛异常时被捕获并返回可读提示
    original_handle = plugin.commands.handle

    async def boom(*args, **kwargs):
        raise RuntimeError("boom")

    plugin.commands.handle = boom
    lines = await plugin._admin_command("check", FakeEvent("cl check"))
    plugin.commands.handle = original_handle
    check("容错：指令异常被捕获", bool(lines) and "失败" in lines[0])

    # 12. 错误处理：消息解析抛异常时不向外抛出
    original_codec = plugin.codec.content_from_event

    async def broken_content(event):
        raise RuntimeError("broken")

    plugin.codec.content_from_event = broken_content
    try:
        await plugin.on_message(FakeEvent("任意消息", group_id="100", sender_id="9"))
    finally:
        plugin.codec.content_from_event = original_codec
    check("容错：消息异常被捕获", True)

    # 13. 数据目录：默认为 StarTools 提供的 data 目录，失败时回退到 data/plugin_data
    star_tools = sys.modules["astrbot.api.star"].StarTools
    check(
        "数据目录：默认使用 StarTools 提供的目录",
        str(plugin.data_dir) == str(star_tools.data_root),
    )
    original_get_data_dir = star_tools.get_data_dir

    def fail_dir(name=None):
        raise RuntimeError("no data dir")

    star_tools.get_data_dir = staticmethod(fail_dir)
    try:
        ghost = object.__new__(type(plugin))
        fallback = ghost._resolve_data_dir()
    finally:
        star_tools.get_data_dir = staticmethod(original_get_data_dir)
    check(
        "数据目录：回退到 data/plugin_data",
        "plugin_data" in fallback.parts
        and fallback.name == "astrbot_plugin_chat_learning",
    )


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
