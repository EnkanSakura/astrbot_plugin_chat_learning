"""总词库与标签词库的合成。"""

from __future__ import annotations

from typing import Any

from .config import PluginConfig
from .models import Question, WordStock
from .storage import MERGE_STOCK, StockManager, write_json
from .utils import fmt_ts, now_ts


def _empty_entry(question: Question) -> dict[str, Any]:
    return {
        "content": question.content,
        "freq": question.freq,
        "time": question.time,
        "regular": question.regular,
        "answers": {
            answer.key: {
                "content": answer.content,
                "same": answer.same,
                "time": answer.time,
            }
            for answer in question.answers
        },
    }


def merge_stocks(source: WordStock, target: dict[str, dict[str, Any]]) -> int:
    """把 source 合并进 target 字典，返回命中的重复问题数量。"""
    merged = 0
    for key, question in list(source.iter_questions()):
        entry = target.get(key)
        if entry is None:
            target[key] = _empty_entry(question)
            continue
        merged += 1
        entry["freq"] = int(entry.get("freq", 0)) + int(question.freq)
        entry["time"] = max(int(entry.get("time", 0)), int(question.time))
        for answer in question.answers:
            existing = entry["answers"].get(answer.key)
            if existing is None:
                entry["answers"][answer.key] = {
                    "content": answer.content,
                    "same": answer.same,
                    "time": answer.time,
                }
            else:
                existing["same"] = int(existing.get("same", 0)) + int(answer.same)
                existing["time"] = max(int(existing.get("time", 0)), int(answer.time))
    return merged


def _write_merged(stocks: StockManager, name: str, merged: dict[str, dict[str, Any]]) -> None:
    payload = {
        "version": WordStock.VERSION,
        "questions": {
            key: {
                "content": entry["content"],
                "freq": entry["freq"],
                "time": entry["time"],
                "regular": entry["regular"],
                "answers": list(entry["answers"].values()),
            }
            for key, entry in merged.items()
        },
    }
    write_json(stocks.path_for(name), payload)
    stocks.invalidate(name)


def rebuild_all(config: PluginConfig, stocks: StockManager) -> list[str]:
    """重建总词库与各标签词库，返回日志行。"""
    names = stocks.names()
    tag_dict = config.tag_dict()
    tags = config.all_tags()
    unmerge = set(config.group_list("unmerge"))
    tagged_groups = set(tag_dict.keys())

    group_names = [
        name
        for name in names
        if name != MERGE_STOCK
        and name not in tags
        and name not in unmerge
        and name not in tagged_groups
    ]

    merged: dict[str, dict[str, Any]] = {}
    for name in group_names:
        merge_stocks(stocks.get(name), merged)
    _write_merged(stocks, MERGE_STOCK, merged)

    logs = [f"{fmt_ts(now_ts())} 词库合并完成：总词库 {len(merged)} 个问题"]
    for tag in tags:
        members = [gid for gid, values in tag_dict.items() if tag in values]
        tag_merged: dict[str, dict[str, Any]] = {}
        for name in members:
            if stocks.exists(name):
                merge_stocks(stocks.get(name), tag_merged)
        _write_merged(stocks, tag, tag_merged)
        logs.append(f"标签词库 {tag}：{len(tag_merged)} 个问题（{len(members)} 个群）")
    return logs
