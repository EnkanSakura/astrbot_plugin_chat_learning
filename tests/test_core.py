"""核心逻辑离线测试：不依赖 astrbot，直接运行 python tests/test_core.py。"""

from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.admin import AdminSession
from core.commands import CommandContext, CommandService
from core.config import PluginConfig
from core.fastdelete import FastDeleteService, ReplyCache
from core.filtering import FilterStore
from core.learning import LearningEngine
from core.maintenance import autodelete
from core.merge import rebuild_all
from core.models import content_key, text_segment
from core.reply import ReplyEngine
from core.storage import MERGE_STOCK, StockManager

PASSED: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if not condition:
        raise AssertionError(f"{name} 失败 {detail}")
    PASSED.append(name)


def text(value: str) -> list[dict]:
    return [text_segment(value)]


def setup(tmp: str):
    root = Path(tmp)
    config = PluginConfig(root / "config.json")
    stocks = StockManager(root)
    filters = FilterStore(root / "filter.json")
    learning = LearningEngine(config, stocks, filters)
    reply = ReplyEngine(config, stocks, filters)
    cmds = CommandService(
        config,
        stocks,
        filters,
        learning,
        reply,
        FastDeleteService(config, stocks, ReplyCache(config)),
    )
    return config, stocks, filters, learning, reply, cmds


async def test_learning() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        config, stocks, _filters, learning, _reply, _cmds = setup(tmp)
        config.set("learning", 1)
        config.add_groups("learning", ["100"])
        await learning.on_group_message("100", text("你好"), {"Plain"}, "1", 1000)
        await learning.on_group_message("100", text("早上好"), {"Plain"}, "2", 1010)
        stock = stocks.get("100")
        check("学习：记录了两个问题", len(stock.questions) == 2)
        first = stock.get(content_key(text("你好")))
        check("学习：第一个问题有答案", first is not None and len(first.answers) == 1)
        check("学习：答案是第二条消息", first.answers[0].content == text("早上好"))
        # 间隔超过 interval，成为新问题而不是答案
        await learning.on_group_message("100", text("无关"), {"Plain"}, "3", 5000)
        first = stocks.get("100").get(content_key(text("你好")))
        check("学习：超时后不再追加答案", len(first.answers) == 1)
        # 未加入记录列表的群
        await learning.on_group_message("200", text("忽略"), {"Plain"}, "4", 5000)
        check("学习：未开启的群不记录", not stocks.exists("200"))
        # 过滤词
        _filters.add_filter(text("广告"))
        await learning.on_group_message("100", text("这是广告"), {"Plain"}, "5", 5001)
        check(
            "学习：过滤词命中不记录",
            stocks.get("100").get(content_key(text("这是广告"))) is None,
        )


async def test_reply_engine() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        config, stocks, filters, _learning, reply, _cmds = setup(tmp)
        stock = stocks.get("100")
        stock.record_question(text("你好"))
        stock.record_answer(content_key(text("你好")), text("你好呀"))
        stock.record_answer(content_key(text("你好")), text("嗨"))
        stock.save()
        plan = reply.lookup("100", text("你好"))
        check("回复：精确命中", plan is not None)
        check("回复：候选答案权重", plan.answer.same == 1)
        check("回复：未命中返回 None", reply.lookup("100", text("不存在")) is None)
        # 正则问题
        stock.record_question(text("^天气.*"), regular=True)
        stock.record_answer(content_key(text("^天气.*")), text("今天晴"))
        stock.save()
        plan = reply.lookup("100", text("天气怎么样"))
        check(
            "回复：正则命中",
            plan is not None and plan.question is not None and plan.question.regular,
        )
        # 余弦相似度
        config.set("cosmatch", 1)
        config.set("cosmatching", 0.5)
        plan = reply.lookup("100", text("你好呀"))
        check("回复：余弦兜底命中", plan is not None)
        # 过滤答案
        filters.add_filter(text("嗨"), accurate=True)
        check("回复：命中过滤规则的答案被拦截", reply.is_answer_filtered(text("嗨")))
        check("回复：正常答案放行", not reply.is_answer_filtered(text("你好呀")))
        # 占位符与长度
        rendered = reply.render_content(text("{name}说{me}很好{segment}"), "小明")
        check(
            "回复：占位符渲染",
            rendered[0]["s"].startswith("小明说") and "\n" in rendered[0]["s"],
        )
        config.set("replylength", 3)
        check("回复：长度超限", not reply.answer_within_length(text("太长了吧")))
        # 概率
        config.set("replychance", 100)
        check("回复：100% 必中", all(reply.roll_reply_chance("100") for _ in range(20)))


async def test_filters() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        config, _stocks, filters, _learning, _reply, _cmds = setup(tmp)
        config.set("blackfreq", 2)
        filters.add_filter(text("坏"))
        check("过滤：模糊命中", not filters.check_content(text("你是坏人"), {"Plain"}))
        check("过滤：正常放行", filters.check_content(text("你好"), {"Plain"}))
        filters.add_filter(text("精确词"), accurate=True)
        check("过滤：精确命中", not filters.check_content(text("精确词"), {"Plain"}))
        check("过滤：精确不误伤", filters.check_content(text("精确词啊"), {"Plain"}))
        check(
            "过滤：指令不记录", not filters.check_content(text("!learning"), {"Plain"})
        )
        check("过滤：类型命中", not filters.check_content(text("x"), {"Quote"}))
        filters.add_sensitive(text("敏感"))
        check("敏感：命中", filters.sensitive_hit(text("很敏感的话")))
        check("敏感：未命中", not filters.sensitive_hit(text("普通的话")))
        filters.register_black("42")
        check("黑名单：未达阈值", not filters.is_blacklisted("42", 2))
        filters.register_black("42")
        check("黑名单：达到阈值", filters.is_blacklisted("42", 2))


async def test_merge_and_autodelete() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        config, stocks, filters, _learning, _reply, _cmds = setup(tmp)
        for group, answer in (("100", "答案A"), ("200", "答案B"), ("300", "答案C")):
            stock = stocks.get(group)
            stock.record_question(text("共同问题"))
            stock.record_answer(content_key(text("共同问题")), text(answer))
            stock.record_question(text(f"{group}独有"))
            stock.save()
        config.set("tag", {"200": ["游戏"]})
        logs = rebuild_all(config, stocks)
        check("合并：产生日志", any("总词库" in line for line in logs))
        merged = stocks.get(MERGE_STOCK)
        question = merged.get(content_key(text("共同问题")))
        check("合并：权重累加", question is not None and question.freq == 2)
        check("合并：答案合并", question is not None and len(question.answers) == 2)
        check(
            "合并：带标签的群不进总词库",
            merged.get(content_key(text("200独有"))) is None,
        )
        check("合并：生成标签词库", stocks.exists("游戏"))

        # 自动清理：只出现一次的问题会被删除
        logs = autodelete(config, stocks, filters)
        check("清理：有日志", bool(logs))
        remaining = stocks.get("100")
        check("清理：删除低频条目", remaining.get(content_key(text("100独有"))) is None)
        # 共同问题在 100 的 freq 只有 1，但合并后为 2，应保留
        check(
            "清理：保留跨群高频问题",
            remaining.get(content_key(text("共同问题"))) is not None,
        )


async def test_fastdelete() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        config, stocks, _filters, _learning, _reply, _cmds = setup(tmp)
        stock = stocks.get("100")
        stock.record_question(text("问题"))
        stock.record_answer(content_key(text("问题")), text("答案"))
        stock.save()
        cache = ReplyCache(config)
        service = FastDeleteService(config, stocks, cache)
        cache.remember("100", "999", text("问题"), text("答案"))
        ok, message = service.delete("100", "999")
        check("快删：删除成功", ok and message)
        stock = stocks.get("100")
        check("快删：答案已移除", stock.get(content_key(text("问题"))) is None)
        ok, _message, message_id = service.delete_nth("100", 1)
        check("快删：缓存为空时报错", not ok and message_id is None)


async def test_admin_session() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        config, stocks, filters, learning, reply, cmds = setup(tmp)
        stock = stocks.get("100")
        stock.record_question(text("今天吃什么"))
        stock.record_answer(content_key(text("今天吃什么")), text("吃火锅"))
        stock.save()
        session = AdminSession(config, stocks, filters, cmds)
        outputs = await session.feed("1", [])
        check("管理：进入全局搜索", any("查找" in line for line in outputs))
        outputs = await session.feed("吃什么", [])
        check("管理：搜索结果列出标记", any("标记0" in line for line in outputs))
        outputs = await session.feed("0", [])
        check("管理：按标记删除", any("删除" in line for line in outputs))
        check(
            "管理：答案确实被删除",
            stocks.get("100").get(content_key(text("今天吃什么"))) is None,
        )
        # 过滤流程
        await session.feed("3", [])
        await session.feed("1", [])
        outputs = await session.feed("hello", [])
        check("管理：添加过滤词", any("添加成功" in line for line in outputs))
        check("管理：过滤生效", not filters.check_content(text("hello world")))
        # 查看并删除过滤词（标记 0）
        await session.feed("exit", [])
        await session.feed("3", [])
        await session.feed("5", [])
        await session.feed("1", [])
        outputs = await session.feed("0", [])
        check(
            "管理：按标记删除过滤词",
            any("删除" in line for line in outputs) and not filters.filters,
        )
        # 自定义回复
        await session.feed("5", [])
        outputs = await session.feed("100", [])
        check("管理：自定义回复要求问题", bool(outputs))
        await session.feed("新问题", text("新问题"))
        await session.feed("0", [])
        await session.feed("新答案", text("新答案"))
        outputs = await session.feed("3", [])
        check("管理：自定义回复添加成功", any("添加完毕" in line for line in outputs))
        question = stocks.get("100").get(content_key(text("新问题")))
        check(
            "管理：自定义问答入库", question is not None and len(question.answers) == 1
        )
        # 退出：先回到主菜单，再退出
        await session.feed("0", [])
        outputs = await session.feed("0", [])
        check("管理：退出", session.closed and outputs == ["退出管理模式"])


async def test_commands() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        config, stocks, filters, learning, reply, cmds = setup(tmp)
        ctx = CommandContext(sender_id="1", is_admin=True)
        outputs = await cmds.handle("add learnings 100 200", ctx)
        check("指令：添加记录+回复群", outputs == ["添加完毕 已同时加入回复列表"])
        check(
            "指令：群列表生效",
            config.has_group("reply", "100") and config.has_group("learning", "200"),
        )
        outputs = await cmds.handle("grouplist", ctx)
        check("指令：grouplist 输出", len(outputs) == 5)
        outputs = await cmds.handle("learning 60", ctx)
        check("指令：设置间隔", config.as_int("interval") == 60)
        outputs = await cmds.handle("reply 30", ctx)
        check("指令：设置概率", config.as_int("replychance") == 30)
        outputs = await cmds.handle("reply -s 60 100", ctx)
        check("指令：单群概率", config.reply_chance_for("100") == 60)
        outputs = await cmds.handle("replywait 5 0", ctx)
        check(
            "指令：等待时间校验",
            outputs == ["基准时间不得大于4秒，浮动时间不得大于2秒！"],
        )
        outputs = await cmds.handle("remove reply 100", ctx)
        check(
            "指令：移除群",
            not config.has_group("reply", "100") and "移除完毕" in outputs,
        )
        outputs = await cmds.handle("add tag 游戏 100", ctx)
        check("指令：加标签", config.tag_dict().get("100") == ["游戏"])
        outputs = await cmds.handle("add subadmin 100 42 43", ctx)
        check(
            "指令：子管理员", (config.get("subadmin") or {}).get("100") == ["42", "43"]
        )
        outputs = await cmds.handle("check", ctx)
        check("指令：check 输出", any("回复触发概率" in line for line in outputs))
        outputs = await cmds.handle("nonsense", ctx)
        check("指令：未知指令", "未知指令" in outputs[0])


async def main() -> None:
    await test_learning()
    await test_reply_engine()
    await test_filters()
    await test_merge_and_autodelete()
    await test_fastdelete()
    await test_admin_session()
    await test_commands()
    print(f"全部通过：{len(PASSED)} 项")
    for name in PASSED:
        print("  ✓", name)


if __name__ == "__main__":
    asyncio.run(main())
