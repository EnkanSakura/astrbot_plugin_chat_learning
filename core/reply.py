"""回复决策：词库检索、概率/冷却、答案加权随机。"""

from __future__ import annotations

import random
from collections.abc import Iterable
from dataclasses import dataclass

from .config import PluginConfig
from .filtering import FilterStore
from .models import (
    Answer,
    Question,
    WordStock,
    content_key,
    content_plain_text,
    content_type_names,
)
from .similarity import cosine_similarity
from .storage import MERGE_STOCK, StockManager

MAX_COSINE_LENGTH = 35


@dataclass
class ReplyPlan:
    answer: Answer
    question: Question | None
    stock_name: str


class ReplyEngine:
    def __init__(
        self,
        config: PluginConfig,
        stocks: StockManager,
        filters: FilterStore,
    ) -> None:
        self.config = config
        self.stocks = stocks
        self.filters = filters

    # ---- 词库选择 ---------------------------------------------------
    def candidate_stocks(self, group_id: str) -> list[str]:
        """根据全局模式选择候选词库（分群/标签/总词库）。"""
        if not self.config.as_int("sendmode"):
            return [str(group_id)]
        tags = self.config.tag_dict().get(str(group_id)) or []
        if tags:
            return tags
        return [MERGE_STOCK]

    # ---- 阈值过滤 ---------------------------------------------------
    def _typefreq_keep(
        self, answers: Iterable[Answer], question_freq: int
    ) -> list[Answer]:
        typefreq = self.config.get("typefreq") or {}
        if not isinstance(typefreq, dict) or not typefreq:
            return list(answers)
        kept: list[Answer] = []
        for answer in answers:
            weight = max(1, answer.same + 1)
            drop = False
            for type_name in content_type_names(answer.content):
                try:
                    threshold = int(typefreq.get(type_name, 0) or 0)
                except (TypeError, ValueError):
                    continue
                if threshold > 0 and weight < threshold and question_freq < threshold:
                    drop = True
                    break
            if not drop:
                kept.append(answer)
        return kept

    # ---- 检索 -------------------------------------------------------
    def _exact(self, names: list[str], key: str) -> tuple[WordStock, Question] | None:
        for name in names:
            stock = self.stocks.get(name)
            question = stock.get(key)
            if question is not None:
                return stock, question
        return None

    def _regex(self, names: list[str], text: str) -> tuple[WordStock, Question] | None:
        import re

        for name in names:
            stock = self.stocks.get(name)
            for _, question in stock.iter_questions():
                if not question.regular:
                    continue
                pattern = question.plain_text
                if not pattern:
                    continue
                try:
                    if re.search(pattern, text) is not None:
                        return stock, question
                except re.error:
                    continue
        return None

    def _cosine(self, names: list[str], text: str) -> tuple[WordStock, Question] | None:
        threshold = self.config.as_float("cosmatching")
        best: tuple[float, WordStock, Question] | None = None
        for name in names:
            stock = self.stocks.get(name)
            for _, question in stock.iter_questions():
                if question.regular:
                    continue
                question_text = question.plain_text
                if not question_text or len(question_text) > MAX_COSINE_LENGTH:
                    continue
                score = cosine_similarity(question_text, text)
                if score < threshold or score <= 0:
                    continue
                if best is None or score > best[0]:
                    best = (score, stock, question)
        if best is None:
            return None
        return best[1], best[2]

    def lookup(self, group_id: str, content: list[dict]) -> ReplyPlan | None:
        """检索答案：精确 → 正则 → 余弦相似度。"""
        if not content:
            return None
        names = self.candidate_stocks(group_id)
        key = content_key(content)
        hit = self._exact(names, key)
        if hit is None:
            text = content_plain_text(content).strip()
            if text:
                hit = self._regex(names, text)
                if (
                    hit is None
                    and self.config.as_bool("cosmatch")
                    and len(text) <= MAX_COSINE_LENGTH
                ):
                    hit = self._cosine(names, text)
        if hit is None:
            return None
        stock, question = hit
        answers = question.answers
        if not answers:
            return None
        answers = self._typefreq_keep(answers, question.freq)
        if not answers:
            return None
        return ReplyPlan(
            answer=self._pick(answers),
            question=question,
            stock_name=stock.path.stem,
        )

    @staticmethod
    def _pick(answers: list[Answer]) -> Answer:
        weights = [max(1, answer.same + 1) for answer in answers]
        return random.choices(answers, weights=weights, k=1)[0]

    # ---- 概率 -------------------------------------------------------
    def roll_reply_chance(self, group_id: str) -> bool:
        """按概率决定是否回复。"""
        chance = self.config.reply_chance_for(group_id)
        if chance >= 100:
            return True
        if chance <= 0:
            return False
        return random.uniform(0, 1) <= chance * 0.01

    # ---- 文本渲染 ---------------------------------------------------
    def render_text(self, text: str, sender_name: str) -> str:
        """替换答案文本中的 {me}/{name}/{segment} 占位符。"""
        names = self.config.get("botname") or ["我"]
        if isinstance(names, str):
            names = [names]
        me = random.choice(list(names)) if names else "我"
        return (
            str(text)
            .replace("{me}", str(me))
            .replace("{name}", str(sender_name or ""))
            .replace("{segment}", "\n")
        )

    def render_content(self, content: list[dict], sender_name: str) -> list[dict]:
        """渲染答案片段列表。"""
        rendered: list[dict] = []
        for segment in content:
            if segment.get("t") == "text":
                rendered.append(
                    {
                        "t": "text",
                        "s": self.render_text(segment.get("s", ""), sender_name),
                    }
                )
            else:
                rendered.append(dict(segment))
        return rendered

    def answer_within_length(self, content: list[dict]) -> bool:
        """检查答案文本是否超过长度上限。"""
        limit = max(1, self.config.as_int("replylength"))
        for segment in content:
            if segment.get("t") == "text" and len(str(segment.get("s") or "")) > limit:
                return False
        return True

    def wait_seconds(self) -> float:
        """返回回复前的随机等待秒数。"""
        raw = self.config.get("replywait") or [0, 0]
        try:
            base, jitter = float(raw[0]), float(raw[1])
        except (TypeError, ValueError, IndexError):
            return 0.0
        value = base + random.uniform(-jitter, jitter)
        return max(0.0, min(value, 30.0))

    def is_answer_filtered(self, content: list[dict]) -> bool:
        """检查答案是否命中过滤规则。"""
        return not self.filters.check_content(content)
