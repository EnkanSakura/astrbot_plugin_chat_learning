"""通用工具函数。"""

from __future__ import annotations

import hashlib
import json
import re
import time
from typing import Any

_SPLIT_RE = re.compile(r"[,\s，、;；]+")


def now_ts() -> int:
    return int(time.time())


def fmt_ts(ts: Any) -> str:
    """把时间戳格式化为年月日时分秒；兼容秒/毫秒。"""
    try:
        value = float(ts)
    except (TypeError, ValueError):
        return "-"
    if value > 1e12:
        value /= 1000.0
    if value <= 0:
        return "-"
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(value))


def to_int(value: Any, default: int | None = None) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def to_float(value: Any, default: float | None = None) -> float | None:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return default


def parse_id_list(text: str) -> list[str]:
    """把 "123 456，789" 这类输入切成 ID 列表。"""
    return [part for part in _SPLIT_RE.split((text or "").strip()) if part]


def compact_json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def stable_json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True)


def sha1_hex(data: bytes | str) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha1(data).hexdigest()


def shorten(text: str, limit: int = 30) -> str:
    text = " ".join(str(text or "").split())
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 1)] + "…"


def split_command(text: str) -> tuple[str, str]:
    """拆分首词与剩余参数，首词转小写。"""
    text = (text or "").strip()
    if not text:
        return "", ""
    parts = text.split(maxsplit=1)
    if len(parts) == 1:
        return parts[0].lower(), ""
    return parts[0].lower(), parts[1].strip()


def strip_command_prefix(text: str) -> str:
    """去掉聊天指令前缀 ! 或 ！。"""
    text = (text or "").strip()
    if text[:1] in ("!", "！"):
        return text[1:].strip()
    return text


def contains_cjk(text: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fa5" for ch in text or "")
