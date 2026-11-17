"""插件配置：默认值来自 WebUI 配置，运行时改动写入 data/config.json。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .storage import read_json, write_json
from .utils import to_float, to_int

DEFAULT_TEXTS = {
    "deletesuccess": "已从词库内删除！",
    "deletetemperror": "删除失败，该消息已不在缓存内",
    "deletefinderror": "删除失败，词库中已无法找到该答案",
    "notlearning": "请先开启记录功能",
    "notreply": "请先开启回复功能",
}

DEFAULTS: dict[str, Any] = {
    # 开关
    "learning": 0,
    "reply": 0,
    # 记录/合并间隔（秒）
    "interval": 900,
    "mergetime": 900,
    # 0=分群词库，1=全局（总词库/标签词库）
    "sendmode": 0,
    # 回复概率与节流
    "replychance": 50,
    "replycd": 3,
    "replywait": [0.0, 0.0],
    "replylength": 100,
    # 相似度
    "cosmatch": 0,
    "cosmatching": 0.5,
    # 其他
    "atreply": 1,
    "botname": ["我"],
    "blackfreq": 5,
    "tempmessagenum": 32,
    "fastdelete": 0,
    # 消息类型 -> 回复阈值
    "typefreq": {},
    # 群相关
    "learninggrouplist": [],
    "replygrouplist": [],
    "unmergegrouplist": [],
    "subadmin": {},
    "tag": {},
    "singlereplychance": {},
    "Administrator": [],
    "texts": dict(DEFAULT_TEXTS),
}


def _merge_defaults(base: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in (extra or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            merged = dict(result[key])
            merged.update(value)
            result[key] = merged
        else:
            result[key] = value
    return result


class PluginConfig:
    """以 JSON 文件保存的动态配置。"""

    def __init__(self, path: Path, seed: dict[str, Any] | None = None) -> None:
        self.path = Path(path)
        data = dict(DEFAULTS)
        if seed:
            data = _merge_defaults(
                data, {k: v for k, v in seed.items() if v is not None}
            )
        stored = read_json(self.path, {})
        if isinstance(stored, dict):
            data = _merge_defaults(data, stored)
        self.data = data

    # ---- 基础访问 ---------------------------------------------------
    def get(self, key: str, default: Any = None) -> Any:
        """读取配置项，缺省时回退到内置默认值。"""
        return self.data.get(key, DEFAULTS.get(key, default))

    def set(self, key: str, value: Any) -> None:
        """设置配置项（调用 save 后才持久化）。"""
        self.data[key] = value

    def save(self) -> None:
        """只持久化与默认值不同的配置项。"""
        """只持久化与默认值不同的键，未改动的 WebUI 配置继续跟随配置页。"""
        changed = {
            key: value for key, value in self.data.items() if value != DEFAULTS.get(key)
        }
        write_json(self.path, changed)

    def as_dict(self) -> dict[str, Any]:
        """返回配置的浅拷贝。"""
        return dict(self.data)

    # ---- 数值/布尔便捷方法 ------------------------------------------
    def as_int(self, key: str) -> int:
        """以 int 读取配置项。"""
        return to_int(self.data.get(key), to_int(DEFAULTS.get(key), 0)) or 0

    def as_float(self, key: str) -> float:
        """以 float 读取配置项。"""
        return to_float(self.data.get(key), to_float(DEFAULTS.get(key), 0.0)) or 0.0

    def as_bool(self, key: str) -> bool:
        """以 bool 读取配置项。"""
        return bool(self.data.get(key))

    # ---- 群列表 -----------------------------------------------------
    _GROUP_KEYS = {
        "learning": "learninggrouplist",
        "reply": "replygrouplist",
        "unmerge": "unmergegrouplist",
    }

    def group_list(self, kind: str) -> list[str]:
        """读取群列表（learning/reply/unmerge）。"""
        key = self._GROUP_KEYS.get(kind, kind)
        values = self.data.get(key) or []
        return [str(item) for item in values]

    def has_group(self, kind: str, group_id: str) -> bool:
        """判断群是否在某个列表中。"""
        return str(group_id) in self.group_list(kind)

    def add_groups(self, kind: str, group_ids: list[str]) -> None:
        """把群加入某个列表（自动去重）。"""
        key = self._GROUP_KEYS.get(kind, kind)
        current = self.group_list(key)
        for group_id in group_ids:
            if str(group_id) not in current:
                current.append(str(group_id))
        self.data[key] = current

    def remove_groups(self, kind: str, group_ids: list[str]) -> list[str]:
        """把群从列表移除，返回移除成功的群。"""
        key = self._GROUP_KEYS.get(kind, kind)
        current = self.group_list(key)
        removed: list[str] = []
        for group_id in group_ids:
            group_id = str(group_id)
            if group_id in current:
                current.remove(group_id)
                removed.append(group_id)
        self.data[key] = current
        return removed

    def toggle_group(self, kind: str, group_id: str) -> bool:
        """在列表中切换群的开关状态，返回切换后的状态。"""
        group_id = str(group_id)
        if self.has_group(kind, group_id):
            self.remove_groups(kind, [group_id])
            return False
        self.add_groups(kind, [group_id])
        return True

    def tag_dict(self) -> dict[str, list[str]]:
        """返回规范化后的群标签字典。"""
        raw = self.data.get("tag") or {}
        result: dict[str, list[str]] = {}
        for group_id, tags in raw.items():
            if isinstance(tags, str):
                tags = [tags]
            result[str(group_id)] = [str(tag) for tag in tags or []]
        return result

    def groups_with_tag(self, tag: str) -> list[str]:
        """返回带有指定标签的群。"""
        return [gid for gid, tags in self.tag_dict().items() if tag in tags]

    def all_tags(self) -> list[str]:
        """返回全部标签。"""
        tags: list[str] = []
        for values in self.tag_dict().values():
            for tag in values:
                if tag not in tags:
                    tags.append(tag)
        return tags

    # ---- 概率 -------------------------------------------------------
    def reply_chance_for(self, group_id: str) -> int:
        """返回群专属回复概率，未设置时回退全局。"""
        table = self.data.get("singlereplychance") or {}
        value = table.get(str(group_id))
        if value is None:
            return self.as_int("replychance")
        return to_int(value, self.as_int("replychance")) or 0

    # ---- 文本模板 ---------------------------------------------------
    def text(self, key: str) -> str:
        """读取可配置的提示文本。"""
        texts = self.data.get("texts") or {}
        return str(texts.get(key, DEFAULT_TEXTS.get(key, "")))

    def admins(self) -> list[str]:
        """返回管理员 QQ 列表。"""
        return [str(item) for item in (self.data.get("Administrator") or [])]
