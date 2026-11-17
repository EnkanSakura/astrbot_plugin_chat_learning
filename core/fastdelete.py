"""快速删除：!d / !delete / 引用回复删除机器人刚发的答案。"""

from __future__ import annotations

from collections import OrderedDict

from .config import PluginConfig
from .models import Answer, Question, content_key
from .storage import MERGE_STOCK, StockManager


class ReplyCache:
    """记录机器人最近发送的答案，便于快速删除。"""

    def __init__(self, config: PluginConfig) -> None:
        self.config = config
        self._data: dict[str, OrderedDict[str, list[list[dict]]]] = {}

    def remember(
        self,
        group_id: str,
        message_id: str,
        question_content: list[dict],
        answer_content: list[dict],
    ) -> None:
        if not message_id:
            return
        bucket = self._data.setdefault(str(group_id), OrderedDict())
        bucket[str(message_id)] = [question_content, answer_content]
        limit = max(1, self.config.as_int("tempmessagenum"))
        while len(bucket) > limit:
            bucket.popitem(last=False)

    def get(self, group_id: str, message_id: str):
        bucket = self._data.get(str(group_id))
        if not bucket:
            return None
        return bucket.get(str(message_id))

    def remove(self, group_id: str, message_id: str) -> None:
        bucket = self._data.get(str(group_id))
        if bucket:
            bucket.pop(str(message_id), None)

    def nth_last(self, group_id: str, index: int):
        """index=1 表示最近一条。返回 (message_id, entry)。"""
        bucket = self._data.get(str(group_id))
        if not bucket or index <= 0:
            return None
        items = list(bucket.items())
        if index > len(items):
            return None
        return items[-index]

    def clear(self, group_id: str | None = None) -> None:
        if group_id is None:
            self._data.clear()
        else:
            self._data.pop(str(group_id), None)


class FastDeleteService:
    def __init__(
        self,
        config: PluginConfig,
        stocks: StockManager,
        cache: ReplyCache,
    ) -> None:
        self.config = config
        self.stocks = stocks
        self.cache = cache

    def allowed(self, sender_id: str) -> bool:
        if self.config.as_int("fastdelete") == 1:
            return True
        return str(sender_id) in self.config.admins()

    def _remove_from_stocks(
        self, question_content: list[dict], answer_content: list[dict]
    ) -> bool:
        question_key = content_key(question_content)
        answer_key = content_key(answer_content)
        tags = set(self.config.all_tags())
        found = False
        for name in self.stocks.names():
            if name == MERGE_STOCK or name in tags:
                continue
            stock = self.stocks.get(name)
            question: Question | None = stock.get(question_key)
            if question is None:
                continue
            answer: Answer | None = next(
                (item for item in question.answers if item.key == answer_key), None
            )
            if answer is None:
                continue
            stock.remove_answer(question_key, answer)
            stock.save()
            found = True
        return found

    def delete(self, group_id: str, message_id: str) -> tuple[bool, str]:
        entry = self.cache.get(group_id, message_id)
        if entry is None:
            return False, self.config.text("deletetemperror")
        if not self._remove_from_stocks(entry[0], entry[1]):
            return False, self.config.text("deletefinderror")
        self.cache.remove(group_id, message_id)
        return True, self.config.text("deletesuccess")

    def delete_nth(
        self, group_id: str, index: int
    ) -> tuple[bool, str, str | None]:
        hit = self.cache.nth_last(group_id, index)
        if hit is None:
            return False, self.config.text("deletetemperror"), None
        message_id, _ = hit
        ok, text = self.delete(group_id, message_id)
        return ok, text, message_id
