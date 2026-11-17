"""过滤词、敏感词、黑名单与消息类型过滤。"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from .models import (
    content_key,
    content_plain_text,
    normalize_content,
)
from .storage import read_json, write_json

DEFAULT_TYPES = ["At", "AtAll", "Quote", "Poke", "Face", "Record", "Video", "File"]

# 与指令重名的纯文本消息不进入词库（标准指令本身会在事件层被跳过）
COMMAND_GUARD = {
    "cl",
    "chatlearning",
    "learning",
    "reply",
    "admin",
    "!learning",
    "!reply",
    "!admin",
    "!d",
    "！d",
    "!delete",
    "！delete",
}


class FilterStore:
    """保存 Filter.json：过滤词、敏感词、黑名单、按消息类型过滤。"""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        raw = read_json(self.path, {})
        if not isinstance(raw, dict):
            raw = {}
        self.filters: list[dict] = [
            self._coerce_entry(item) for item in (raw.get("filter") or [])
        ]
        self.sensitive: list[dict] = [
            self._coerce_entry(item) for item in (raw.get("sensitive") or [])
        ]
        self.blackdict: dict[str, int] = {
            str(key): int(value) for key, value in (raw.get("blackdict") or {}).items()
        }
        types = raw.get("type")
        self.types: list[str] = list(types) if isinstance(types, list) else list(DEFAULT_TYPES)

    @staticmethod
    def _coerce_entry(item: dict) -> dict:
        if isinstance(item, dict) and "content" in item:
            return {
                "content": normalize_content(item.get("content") or []),
                "accurate": bool(item.get("accurate", False)),
            }
        return {"content": normalize_content(item or []), "accurate": False}

    def save(self) -> None:
        write_json(
            self.path,
            {
                "filter": self.filters,
                "sensitive": self.sensitive,
                "blackdict": self.blackdict,
                "type": self.types,
            },
        )

    # ---- 匹配 -------------------------------------------------------
    @staticmethod
    def _entry_text(entry: dict) -> str:
        return content_plain_text(entry.get("content") or [])

    def _match_entry(self, content: list[dict], entry: dict) -> bool:
        """模糊：任一段文本是条目文本的子串；精确：整体相等。"""
        if entry.get("accurate"):
            return content_key(content) == content_key(entry.get("content") or [])
        entry_text = self._entry_text(entry)
        if not entry_text:
            return content_key(content) == content_key(entry.get("content") or [])
        for seg in content:
            if seg.get("t") == "text" and entry_text in str(seg.get("s") or ""):
                return True
        return False

    def match(self, content: list[dict], entry: dict) -> bool:
        return self._match_entry(content, entry)

    def check_content(
        self, content: list[dict], raw_types: Iterable[str] = ()
    ) -> bool:
        """返回 True 表示放行，False 表示被过滤。"""
        content = normalize_content(content)
        for entry in self.filters:
            if self._match_entry(content, entry):
                return False
        text = content_plain_text(content).strip()
        if text in COMMAND_GUARD:
            return False
        raw_types = set(raw_types or ())
        if raw_types & set(self.types):
            return False
        return True

    def sensitive_hit(self, content: list[dict]) -> bool:
        content = normalize_content(content)
        for entry in self.sensitive:
            entry_content = entry.get("content") or []
            if content_key(content) == content_key(entry_content):
                return True
            entry_text = self._entry_text(entry)
            if not entry_text:
                continue
            for seg in content:
                if seg.get("t") == "text" and entry_text in str(seg.get("s") or ""):
                    return True
        return False

    # ---- 黑名单 -----------------------------------------------------
    def register_black(self, user_id: str) -> int:
        user_id = str(user_id)
        self.blackdict[user_id] = self.blackdict.get(user_id, 0) + 1
        self.save()
        return self.blackdict[user_id]

    def add_black(self, user_ids: Iterable[str], count: int = 99999) -> None:
        for user_id in user_ids:
            self.blackdict[str(user_id)] = count
        self.save()

    def remove_black(self, user_ids: Iterable[str]) -> list[str]:
        missing: list[str] = []
        for user_id in user_ids:
            user_id = str(user_id)
            if user_id in self.blackdict:
                del self.blackdict[user_id]
            else:
                missing.append(user_id)
        self.save()
        return missing

    def is_blacklisted(self, user_id: str, threshold: int) -> bool:
        return self.blackdict.get(str(user_id), 0) >= max(1, threshold)

    # ---- 增删 -------------------------------------------------------
    def add_filter(self, content: list[dict], accurate: bool = False) -> None:
        content = normalize_content(content)
        key = content_key(content)
        for entry in self.filters:
            if content_key(entry.get("content") or []) == key:
                entry["accurate"] = bool(accurate)
                self.save()
                return
        self.filters.append({"content": content, "accurate": bool(accurate)})
        self.save()

    def add_sensitive(self, content: list[dict]) -> None:
        content = normalize_content(content)
        key = content_key(content)
        if all(content_key(entry.get("content") or []) != key for entry in self.sensitive):
            self.sensitive.append({"content": content, "accurate": False})
        self.add_filter(content, accurate=False)

    def clear_filters(self) -> None:
        self.filters.clear()
        self.save()

    def clear_sensitive(self) -> None:
        self.sensitive.clear()
        self.save()

    def clear_black(self) -> None:
        self.blackdict.clear()
        self.save()

    def set_types(self, types: list[str]) -> None:
        self.types = list(types)
        self.save()
