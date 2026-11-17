"""问答收集：按“词库链间隔”把连续消息组织成 问 -> 答。"""

from __future__ import annotations

import time
from dataclasses import dataclass

from .config import PluginConfig
from .filtering import FilterStore
from .models import WordStock, content_key, content_preview
from .storage import StockManager


@dataclass
class _Pending:
    content: list[dict]
    time: int


@dataclass
class LearningOutcome:
    """一条群消息的学习结果，用于调试日志。"""

    action: str = ""
    reason: str = ""
    question: str = ""
    answer_to: str = ""

    def describe(self) -> str:
        """返回可读的记录结果。"""
        if self.action == "question":
            return f"作为新问题记录「{self.question}」"
        if self.action == "answer":
            return f"作为答案记录到「{self.answer_to}」"
        return self.reason


class LearningEngine:
    """每个群维护一个待回答问题，间隔内的后续消息都作为它的答案。"""

    def __init__(
        self,
        config: PluginConfig,
        stocks: StockManager,
        filters: FilterStore,
    ) -> None:
        self.config = config
        self.stocks = stocks
        self.filters = filters
        self._pending: dict[str, _Pending] = {}
        self._last_save: dict[str, float] = {}

    def reset(self, group_id: str | None = None) -> None:
        """清空等待配对的待回答问题。"""
        if group_id is None:
            self._pending.clear()
        else:
            self._pending.pop(str(group_id), None)

    def flush(self) -> int:
        """把内存中未落盘的词库写入磁盘。"""
        return self.stocks.flush_dirty()

    async def on_group_message(
        self,
        group_id: str,
        content: list[dict],
        raw_types: set[str],
        sender_id: str,
        timestamp: int,
    ) -> LearningOutcome:
        """处理一条群消息：过滤检查后按间隔配对记录问答。"""
        if not self.config.as_bool("learning"):
            return LearningOutcome(reason="记录功能已关闭")
        if not self.config.has_group("learning", group_id):
            return LearningOutcome(reason="群未加入记录列表")
        if not content:
            return LearningOutcome(reason="消息没有可记录内容")
        if self.filters.sensitive_hit(content):
            count = self.filters.register_black(sender_id)
            threshold = self.config.as_int("blackfreq")
            cleared = count >= threshold
            if cleared:
                self._pending.pop(str(group_id), None)
            suffix = "，已清空待回答问题" if cleared else ""
            return LearningOutcome(
                reason=f"命中敏感词（{count}/{threshold}）{suffix}"
            )
        if not self.filters.check_content(content, raw_types):
            return LearningOutcome(reason="命中过滤词或消息类型过滤")
        return await self._record(str(group_id), content, timestamp)

    async def _record(
        self, group_id: str, content: list[dict], timestamp: int
    ) -> LearningOutcome:
        interval = max(1, self.config.as_int("interval"))
        pending = self._pending.get(group_id)
        stock_name = group_id
        preview = content_preview(content, 50)
        async with self.stocks.lock_for(stock_name):
            stock: WordStock = self.stocks.get(stock_name)
            stock.record_question(content)
            if pending is not None and timestamp - pending.time <= interval:
                # 当前消息作为“上一条问题”的答案
                stock.record_answer(content_key(pending.content), content)
                outcome = LearningOutcome(
                    action="answer",
                    answer_to=content_preview(pending.content, 50),
                )
            else:
                self._pending[group_id] = _Pending(content=content, time=timestamp)
                outcome = LearningOutcome(action="question", question=preview)
            # 高频群聊时避免每条消息都整库写盘，最多每 2 秒落盘一次
            now = time.time()
            if now - self._last_save.get(stock_name, 0.0) >= 2.0:
                stock.save()
                self._last_save[stock_name] = now
        return outcome
