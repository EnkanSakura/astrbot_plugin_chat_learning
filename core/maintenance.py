"""词库维护：自动清理低频与命中过滤词的条目。"""

from __future__ import annotations

from collections import Counter

from .config import PluginConfig
from .filtering import FilterStore
from .models import WordStock
from .storage import MERGE_STOCK, StockManager


def _group_stock_names(config: PluginConfig, stocks: StockManager) -> list[str]:
    tags = set(config.all_tags())
    return [name for name in stocks.names() if name != MERGE_STOCK and name not in tags]


def autodelete(
    config: PluginConfig,
    stocks: StockManager,
    filters: FilterStore,
) -> list[str]:
    """删除合并后频次 < 2 的问题，以及命中过滤规则的问题/答案。"""
    names = _group_stock_names(config, stocks)
    if not names:
        return ["没有可清理的词库"]

    total_freq: Counter[str] = Counter()
    for name in names:
        for key, question in stocks.get(name).iter_questions():
            total_freq[key] += question.freq

    logs: list[str] = []
    for name in names:
        stock: WordStock = stocks.get(name)
        removed_low = 0
        removed_filtered = 0
        for key in list(stock.questions.keys()):
            question = stock.questions[key]
            if total_freq.get(key, 0) < 2:
                del stock.questions[key]
                removed_low += 1
                continue
            if not filters.check_content(question.content):
                del stock.questions[key]
                removed_filtered += 1
                continue
            keep = [
                answer
                for answer in question.answers
                if filters.check_content(answer.content)
            ]
            if len(keep) != len(question.answers):
                removed_filtered += len(question.answers) - len(keep)
                question.answers = keep
                if not keep:
                    del stock.questions[key]
        if removed_low or removed_filtered:
            stock.save()
            parts = []
            if removed_low:
                parts.append(f"低频条目{removed_low}个")
            if removed_filtered:
                parts.append(f"过滤条目{removed_filtered}个")
            logs.append(f"已清理{name}：" + "，".join(parts))
    if not logs:
        logs.append("无需清理")
    return logs
