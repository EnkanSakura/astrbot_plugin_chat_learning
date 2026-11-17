"""配置参数对“记录/回复所需轮次”的模拟测试。

轮次定义：从开始发送群消息算起，到第一次形成问答对 / 第一次真正发送回复
所需的消息条数。记录场景直接驱动 LearningEngine，回复场景驱动 ReplyEngine；
冷却、@、发送链路等主流程行为放在 tests/test_plugin_integration.py。

运行：python tests/test_rounds.py
"""

from __future__ import annotations

import asyncio
import random
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.config import PluginConfig
from core.filtering import FilterStore
from core.learning import LearningEngine
from core.models import content_key, text_segment
from core.reply import ReplyEngine
from core.storage import MERGE_STOCK, StockManager

PASSED: list[str] = []
ROWS: list[tuple[str, str, str, str]] = []


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
    return config, stocks, filters, learning, reply


def add_row(scene: str, params: str, expected, actual) -> None:
    ROWS.append(
        (
            scene,
            params,
            "未触发" if expected is None else str(expected),
            "未触发" if actual is None else str(actual),
        )
    )


async def record_rounds(
    learning: LearningEngine,
    stocks: StockManager,
    group: str,
    messages: list[tuple[str, int]],
    sender: str = "1",
) -> int | None:
    """返回第一次形成问答对时的消息序号。"""
    for index, (value, timestamp) in enumerate(messages, 1):
        await learning.on_group_message(
            group, text(value), {"Plain"}, sender, timestamp
        )
        if any(question.answers for _, question in stocks.get(group).iter_questions()):
            return index
    return None


def reply_rounds(
    reply: ReplyEngine,
    group: str,
    messages: list[str],
) -> int | None:
    """返回第一次真正通过概率检查并发送回复时的消息序号。"""
    for index, value in enumerate(messages, 1):
        plan = reply.lookup(group, text(value))
        if plan is None:
            continue
        if not reply.roll_reply_chance(group):
            continue
        if not reply.answer_within_length(plan.answer.content):
            continue
        return index
    return None


def seed_qa(stock, question: str, answer: str, freq: int = 1, same: int = 1) -> None:
    stock.record_question(text(question))
    key = content_key(text(question))
    for _ in range(same):
        stock.record_answer(key, text(answer))
    current = stock.get(key)
    if current is not None:
        current.freq = freq
    stock.save()


async def test_record_matrix() -> None:
    cases = [
        ("记录-间隔内", "interval=5, gap=1", 5, True, [("问", 0), ("答", 1)], 2),
        (
            "记录-首条超时",
            "interval=5, gap=6",
            5,
            True,
            [("问", 0), ("答", 6), ("尾", 10)],
            3,
        ),
        ("记录-间隔1", "interval=1, gap=1", 1, True, [("问", 0), ("答", 1)], 2),
        (
            "记录-间隔1超时",
            "interval=1, gap=2",
            1,
            True,
            [("问", 0), ("答", 2), ("尾", 3)],
            3,
        ),
        ("记录-长间隔", "interval=60, gap=10", 60, True, [("问", 0), ("答", 10)], 2),
        (
            "记录-未加入群",
            "group=off, interval=5",
            5,
            False,
            [("问", 0), ("答", 1)],
            None,
        ),
    ]
    for scene, params, interval, enabled, messages, expected in cases:
        with tempfile.TemporaryDirectory() as tmp:
            config, stocks, _filters, learning, _reply = setup(tmp)
            config.set("learning", 1)
            config.set("interval", interval)
            if enabled:
                config.add_groups("learning", ["100"])
            actual = await record_rounds(learning, stocks, "100", messages)
            check(scene, actual == expected, f"期望 {expected}，实际 {actual}")
            add_row(scene, params, expected, actual)

    with tempfile.TemporaryDirectory() as tmp:
        config, stocks, _filters, learning, _reply = setup(tmp)
        config.set("learning", 0)
        config.set("interval", 5)
        config.add_groups("learning", ["100"])
        actual = await record_rounds(learning, stocks, "100", [("问", 0), ("答", 1)])
        check("记录-全局关闭", actual is None)
        add_row("记录-全局关闭", "learning=off", None, actual)

    # 配置交叉：间隔大小 x 是否启用群，验证轮次不会因参数组合错位
    cross = [
        (1, True, 1, 2),
        (1, True, 2, 3),
        (5, True, 1, 2),
        (5, False, 1, None),
    ]
    for interval, enabled, gap, expected in cross:
        with tempfile.TemporaryDirectory() as tmp:
            config, stocks, _filters, learning, _reply = setup(tmp)
            config.set("learning", 1)
            config.set("interval", interval)
            if enabled:
                config.add_groups("learning", ["100"])
            messages = [("问", 0), ("答", gap)]
            if gap > interval:
                messages.append(("尾", gap + 1))
            actual = await record_rounds(learning, stocks, "100", messages)
            name = f"记录交叉-i{interval}-{'on' if enabled else 'off'}-g{gap}"
            check(name, actual == expected, f"期望 {expected}，实际 {actual}")
            add_row(
                name,
                f"interval={interval}, enabled={enabled}, gap={gap}",
                expected,
                actual,
            )


async def test_blackfreq_rounds() -> None:
    for threshold, expected in ((2, 5), (3, 4)):
        with tempfile.TemporaryDirectory() as tmp:
            config, stocks, filters, learning, _reply = setup(tmp)
            config.set("learning", 1)
            config.set("interval", 60)
            config.set("blackfreq", threshold)
            config.add_groups("learning", ["100"])
            filters.add_sensitive(text("敏感"))
            messages = [
                ("问", 0),
                ("很敏感的话", 1),
                ("更敏感的话", 2),
                ("答", 3),
                ("尾", 4),
            ]
            actual = await record_rounds(learning, stocks, "100", messages)
            check(
                f"记录-敏感词容错{threshold}",
                actual == expected,
                f"期望 {expected}，实际 {actual}",
            )
            check(
                f"记录-敏感词计数{threshold}",
                filters.blackdict.get("1") == 2,
            )
            add_row(
                f"记录-敏感词容错{threshold}",
                f"blackfreq={threshold}, 2 条敏感消息",
                expected,
                actual,
            )


async def test_typefreq_rounds() -> None:
    expected_by_threshold = {0: 3, 1: 3, 2: 3, 3: 5, 4: 7}
    values = ["问", "答", "问", "答", "问", "答", "问", "答", "问"]
    for threshold, expected in expected_by_threshold.items():
        with tempfile.TemporaryDirectory() as tmp:
            config, _stocks, _filters, learning, reply = setup(tmp)
            config.set("learning", 1)
            config.set("reply", 1)
            config.set("interval", 60)
            config.set("replychance", 100)
            config.set("replycd", 0)
            config.set("typefreq", {"Plain": threshold})
            config.add_groups("learning", ["100"])
            config.add_groups("reply", ["100"])
            actual = None
            for index, (value, timestamp) in enumerate(zip(values, range(0, 18, 2)), 1):
                await learning.on_group_message(
                    "100", text(value), {"Plain"}, "1", timestamp
                )
                plan = reply.lookup("100", text(value))
                if plan is not None and reply.roll_reply_chance("100"):
                    actual = index
                    break
            check(
                f"回复-typefreq{threshold}",
                actual == expected,
                f"期望 {expected}，实际 {actual}",
            )
            add_row(
                f"回复-typefreq{threshold}",
                f"typefreq.Plain={threshold}",
                expected,
                actual,
            )


def test_reply_chance_statistics() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        config, _stocks, _filters, _learning, reply = setup(tmp)
        random.seed(20260717)
        expectations = {
            0: (None, None),
            25: (4.0, 0.25),
            50: (2.0, 0.50),
            100: (1.0, 1.00),
        }
        for chance, (expected_mean, expected_first) in expectations.items():
            config.set("replychance", chance)
            trials = 400 if chance in (0, 100) else 3000
            rounds: list[int] = []
            for _ in range(trials):
                for attempt in range(1, 101):
                    if reply.roll_reply_chance("100"):
                        rounds.append(attempt)
                        break
                else:
                    rounds.append(101)
            if chance == 0:
                check("概率-0% 不回复", all(item == 101 for item in rounds))
                add_row("回复-概率0%", "replychance=0", "永不", "未触发")
                continue
            if chance == 100:
                check("概率-100% 首轮必回", all(item == 1 for item in rounds))
                add_row("回复-概率100%", "replychance=100", 1, 1)
                continue
            mean = sum(rounds) / len(rounds)
            first = rounds.count(1) / len(rounds)
            tolerance = 0.25 if chance == 25 else 0.15
            check(
                f"概率-{chance}% 平均轮次",
                abs(mean - expected_mean) <= tolerance,
                f"均值 {mean:.3f}，期望 {expected_mean}±{tolerance}",
            )
            check(
                f"概率-{chance}% 首轮比例",
                abs(first - expected_first) <= 0.06,
                f"首轮比例 {first:.3f}，期望 {expected_first}±0.06",
            )
            add_row(
                f"回复-概率{chance}%",
                f"replychance={chance}",
                f"均值≈{expected_mean:g}, 首轮≈{expected_first:.0%}",
                f"均值{mean:.2f}, 首轮{first:.0%}",
            )


def test_reply_length_rounds() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        config, stocks, _filters, _learning, reply = setup(tmp)
        seed_qa(stocks.get("100"), "今天吃什么", "食堂二楼新开了一个窗口")
        config.set("reply", 1)
        config.set("replychance", 100)
        config.set("replycd", 0)
        config.add_groups("reply", ["100"])
        config.set("replylength", 3)
        short = reply_rounds(reply, "100", ["今天吃什么"])
        config.set("replylength", 100)
        long_ok = reply_rounds(reply, "100", ["今天吃什么"])
        check("回复-超长答案不发送", short is None)
        check("回复-提高长度上限后首轮发送", long_ok == 1)
        add_row("回复-答案超长", "replylength=3", None, short)
        add_row("回复-长度放宽", "replylength=100", 1, long_ok)


def test_match_matrix() -> None:
    # 精确匹配：分群词库
    with tempfile.TemporaryDirectory() as tmp:
        config, stocks, _filters, _learning, reply = setup(tmp)
        seed_qa(stocks.get("100"), "你好", "你好呀")
        config.set("reply", 1)
        config.set("replychance", 100)
        config.add_groups("reply", ["100"])
        actual = reply_rounds(reply, "100", ["你好"])
        check("匹配-分群精确首轮", actual == 1)
        add_row("匹配-分群精确", "sendmode=0", 1, actual)

    # 余弦关闭/低阈值/高阈值
    for threshold, expected in ((None, None), (0.1, 1), (0.99, None)):
        with tempfile.TemporaryDirectory() as tmp:
            config, stocks, _filters, _learning, reply = setup(tmp)
            seed_qa(stocks.get("100"), "今天天气", "晴")
            config.set("reply", 1)
            config.set("replychance", 100)
            config.add_groups("reply", ["100"])
            if threshold is not None:
                config.set("cosmatch", 1)
                config.set("cosmatching", threshold)
            actual = reply_rounds(reply, "100", ["天气怎么样"])
            name = "匹配-余弦关闭" if threshold is None else f"匹配-余弦阈值{threshold}"
            check(name, actual == expected)
            add_row(
                name,
                "cosmatch=0"
                if threshold is None
                else f"cosmatch=1, cosmatching={threshold}",
                expected,
                actual,
            )

    # 全局模式：有标签走标签词库，无标签走总词库
    with tempfile.TemporaryDirectory() as tmp:
        config, stocks, _filters, _learning, reply = setup(tmp)
        seed_qa(stocks.get("100"), "开黑吗", "群答案")
        seed_qa(stocks.get("游戏"), "开黑吗", "标签答案")
        config.set("reply", 1)
        config.set("replychance", 100)
        config.set("sendmode", 1)
        config.set("tag", {"100": ["游戏"]})
        config.add_groups("reply", ["100"])
        plan = reply.lookup("100", text("开黑吗"))
        check(
            "匹配-标签词库",
            plan is not None
            and plan.stock_name == "游戏"
            and plan.answer.content == text("标签答案"),
        )
        actual = reply_rounds(reply, "100", ["开黑吗"])
        check("匹配-标签词库首轮", actual == 1)
        add_row("匹配-标签词库", "sendmode=1, tag=游戏", 1, actual)

    with tempfile.TemporaryDirectory() as tmp:
        config, stocks, _filters, _learning, reply = setup(tmp)
        seed_qa(stocks.get(MERGE_STOCK), "开黑吗", "总词库答案")
        config.set("reply", 1)
        config.set("replychance", 100)
        config.set("sendmode", 1)
        config.add_groups("reply", ["100"])
        plan = reply.lookup("100", text("开黑吗"))
        check(
            "匹配-总词库",
            plan is not None
            and plan.stock_name == MERGE_STOCK
            and plan.answer.content == text("总词库答案"),
        )
        actual = reply_rounds(reply, "100", ["开黑吗"])
        check("匹配-总词库首轮", actual == 1)
        add_row("匹配-总词库", "sendmode=1, no tag", 1, actual)


def print_round_table() -> None:
    print("\n配置 -> 轮次对照")
    print(f"{'场景':<32}{'参数':<42}{'期望':<22}{'实际':<22}")
    print("-" * 118)
    for scene, params, expected, actual in ROWS:
        print(f"{scene:<32}{params:<42}{expected:<22}{actual:<22}")


async def main() -> None:
    await test_record_matrix()
    await test_blackfreq_rounds()
    await test_typefreq_rounds()
    test_reply_chance_statistics()
    test_reply_length_rounds()
    test_match_matrix()
    print_round_table()
    print(f"\n全部通过：{len(PASSED)} 项")
    for name in PASSED:
        print("  ✓", name)


if __name__ == "__main__":
    asyncio.run(main())
