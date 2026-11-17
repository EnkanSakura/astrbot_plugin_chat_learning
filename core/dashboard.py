"""可视化视图的后端业务逻辑。

本模块不依赖 AstrBot，方便离线测试；Web 路由注册见 dashboard_web.py。
提供运行状态、词库管理、过滤规则、配置读写、备份恢复与手动维护能力。
"""

from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import DEFAULTS, PluginConfig
from .filtering import DEFAULT_TYPES, FilterStore
from .maintenance import autodelete as run_autodelete
from .merge import rebuild_all
from .models import content_key, content_preview, normalize_content, text_segment
from .similarity import backend_name
from .storage import MERGE_STOCK, StockManager, read_json, write_json
from .utils import fmt_ts, now_ts, parse_id_list, to_float, to_int

PLUGIN_NAME = "astrbot_plugin_chat_learning"
BACKUP_FORMAT = "astrbot_plugin_chat_learning.backup"
BACKUP_VERSION = 1
BACKUP_KEEP = 5
MAX_PAGE_SIZE = 100
MAX_QUERY_LENGTH = 100
MAX_ANSWERS_PER_QUESTION = 50

# 可视化配置面板的字段定义（与 data/config.json 的键一致）
CONFIG_FIELDS: list[dict[str, Any]] = [
    {
        "key": "learning",
        "label": "开启记录",
        "type": "bool",
        "group": "开关",
        "hint": "记录群消息并学习问答对",
    },
    {
        "key": "reply",
        "label": "开启回复",
        "type": "bool",
        "group": "开关",
        "hint": "命中问题时自动接话",
    },
    {
        "key": "sendmode",
        "label": "全局模式",
        "type": "bool",
        "group": "开关",
        "hint": "使用标签词库或总词库回复",
    },
    {
        "key": "debug",
        "label": "Debug 日志",
        "type": "bool",
        "group": "开关",
        "hint": "在 AstrBot 日志中打印每条群消息的记录与回复决策",
    },
    {
        "key": "replychance",
        "label": "回复概率",
        "type": "int",
        "group": "回复",
        "min": 0,
        "max": 100,
        "unit": "%",
    },
    {
        "key": "replycd",
        "label": "回复冷却",
        "type": "int",
        "group": "回复",
        "min": 0,
        "max": 600,
        "unit": "秒",
    },
    {
        "key": "replywait",
        "label": "随机等待",
        "type": "pair",
        "group": "回复",
        "hint": "基准秒 + 浮动秒，发送前随机等待",
    },
    {
        "key": "replylength",
        "label": "答案最大字数",
        "type": "int",
        "group": "回复",
        "min": 1,
        "max": 2000,
        "unit": "字",
    },
    {"key": "atreply", "label": "被 @ 直接回复", "type": "bool", "group": "回复"},
    {
        "key": "interval",
        "label": "词库链间隔",
        "type": "int",
        "group": "学习",
        "min": 10,
        "max": 86400,
        "unit": "秒",
    },
    {
        "key": "mergetime",
        "label": "总词库合并间隔",
        "type": "int",
        "group": "学习",
        "min": 60,
        "max": 86400,
        "unit": "秒",
    },
    {"key": "cosmatch", "label": "余弦相似度匹配", "type": "bool", "group": "匹配"},
    {
        "key": "cosmatching",
        "label": "相似度阈值",
        "type": "float",
        "group": "匹配",
        "min": 0,
        "max": 1,
        "step": 0.05,
    },
    {
        "key": "blackfreq",
        "label": "黑名单容错次数",
        "type": "int",
        "group": "安全",
        "min": 1,
        "max": 100,
        "unit": "次",
    },
    {
        "key": "tempmessagenum",
        "label": "单群消息缓存",
        "type": "int",
        "group": "安全",
        "min": 1,
        "max": 500,
        "unit": "条",
    },
    {
        "key": "fastdelete",
        "label": "快速删除所有人可用",
        "type": "bool",
        "group": "安全",
    },
    {
        "key": "botname",
        "label": "bot 昵称",
        "type": "list",
        "group": "其它",
        "hint": "答案中的 {me} 会随机使用，空格或逗号分隔",
    },
    {
        "key": "Administrator",
        "label": "管理员 QQ",
        "type": "list",
        "group": "其它",
        "hint": "空格或逗号分隔",
    },
]
CONFIG_GROUPS = ["开关", "回复", "学习", "匹配", "安全", "其它"]
TYPEFREQ_TYPES = ["Plain", "Image"]
TYPEFREQ_ALLOWED = [
    "Plain",
    "Image",
    "Face",
    "Voice",
    "Record",
    "At",
    "AtAll",
    "Quote",
    "Poke",
    "Video",
    "File",
]
READONLY_LISTS = [
    "learninggrouplist",
    "replygrouplist",
    "unmergegrouplist",
    "tag",
    "subadmin",
    "singlereplychance",
]


class DashboardError(Exception):
    """可读的业务错误，Web 层会转成 400 响应。"""


def _stock_name_ok(name: Any) -> bool:
    if not isinstance(name, str):
        return False
    name = name.strip()
    if not name or len(name) > 64 or name in {".", ".."} or name.startswith("."):
        return False
    return all(char not in '/\\:*?"<>|' for char in name)


def _backup_filename(prefix: str) -> str:
    return prefix + time.strftime("%Y%m%d_%H%M%S") + ".json"


class DashboardService:
    """集中实现可视化视图需要的查询与操作。"""

    def __init__(
        self,
        config: PluginConfig,
        stocks: StockManager,
        filters: FilterStore,
    ) -> None:
        self.config = config
        self.stocks = stocks
        self.filters = filters
        self.started_at = now_ts()
        self.last_merge_at: int | None = None
        self.last_merge_logs: list[str] = []

    # ---- 公共状态 ---------------------------------------------------
    def mark_merged(self, logs: list[str]) -> None:
        """记录一次词库合并的时间与结果，供状态页展示。"""
        self.last_merge_at = now_ts()
        self.last_merge_logs = list(logs)[-8:]

    def _stock_entries(self) -> list[dict[str, Any]]:
        tags = set(self.config.all_tags())
        tag_dict = self.config.tag_dict()
        entries: list[dict[str, Any]] = []
        for name in self.stocks.names():
            stock = self.stocks.get(name)
            questions, answers = stock.stats()
            kind = (
                "merge" if name == MERGE_STOCK else ("tag" if name in tags else "group")
            )
            label = (
                "总词库"
                if kind == "merge"
                else (f"标签 · {name}" if kind == "tag" else f"群 {name}")
            )
            entries.append(
                {
                    "name": name,
                    "label": label,
                    "kind": kind,
                    "questions": questions,
                    "answers": answers,
                    "tags": tag_dict.get(name, []),
                }
            )
        order = {"group": 0, "tag": 1, "merge": 2}
        entries.sort(key=lambda item: (order[item["kind"]], item["name"]))
        return entries

    def _overview_stocks(self) -> list[dict[str, Any]]:
        """状态页词库表：包含已配置但还没有词库的群，以及标签/总词库。"""
        entries: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in self._group_entries():
            entries.append(
                {
                    "name": item["group"],
                    "label": "群 " + item["group"],
                    "kind": "group",
                    "questions": item["questions"],
                    "answers": item["answers"],
                    "tags": item["tags"],
                }
            )
            seen.add(item["group"])
        for item in self._stock_entries():
            if item["name"] in seen or item["kind"] not in {"tag", "merge"}:
                continue
            entries.append(item)

        def sort_key(item: dict[str, Any]) -> tuple[int, Any]:
            if item["kind"] == "group":
                return (0, int(item["name"]))
            return (1 if item["kind"] == "tag" else 2, item["name"])

        entries.sort(key=sort_key)
        return entries

    def overview(self) -> dict[str, Any]:
        """返回状态页所需的全部数据。"""
        entries = self._overview_stocks()
        group_entries = [item for item in entries if item["kind"] == "group"]
        total_questions = sum(item["questions"] for item in entries)
        total_answers = sum(item["answers"] for item in entries)
        typefreq = self.config.get("typefreq") or {}
        return {
            "plugin": {"name": PLUGIN_NAME},
            "runtime": {
                "started_at": self.started_at,
                "uptime_seconds": max(0, now_ts() - self.started_at),
                "data_dir": str(self.stocks.root),
                "similarity_backend": backend_name(),
                "last_merge_at": fmt_ts(self.last_merge_at)
                if self.last_merge_at
                else None,
                "last_merge_logs": self.last_merge_logs,
            },
            "status": {
                "learning": bool(self.config.as_bool("learning")),
                "reply": bool(self.config.as_bool("reply")),
                "sendmode": bool(self.config.as_int("sendmode")),
                "atreply": bool(self.config.as_bool("atreply")),
                "cosmatch": bool(self.config.as_bool("cosmatch")),
                "learning_groups": self.config.group_list("learning"),
                "reply_groups": self.config.group_list("reply"),
                "unmerge_groups": self.config.group_list("unmerge"),
                "tags": self.config.tag_dict(),
                "subadmin": self.config.get("subadmin") or {},
                "admins": self.config.admins(),
            },
            "stocks": entries,
            "totals": {
                "groups": len(group_entries),
                "questions": total_questions,
                "answers": total_answers,
                "merge_questions": next(
                    (item["questions"] for item in entries if item["kind"] == "merge"),
                    0,
                ),
                "tag_stocks": len([item for item in entries if item["kind"] == "tag"]),
            },
            "filters": {
                "filter": len(self.filters.filters),
                "sensitive": len(self.filters.sensitive),
                "blacklist": len(self.filters.blackdict),
                "types": list(self.filters.types),
            },
            "config_summary": {
                "replychance": self.config.as_int("replychance"),
                "replycd": self.config.as_int("replycd"),
                "interval": self.config.as_int("interval"),
                "mergetime": self.config.as_int("mergetime"),
                "typefreq": {
                    key: to_int(value, 0) for key, value in dict(typefreq).items()
                },
            },
        }

    # ---- 词库管理 ---------------------------------------------------
    def _require_stock(self, name: Any) -> str:
        if not _stock_name_ok(name):
            raise DashboardError("词库名不合法")
        if name not in self.stocks.names():
            raise DashboardError("词库不存在")
        return str(name)

    def wordstock(self, params: dict[str, Any]) -> dict[str, Any]:
        """分页查询某个词库的问题与答案，支持关键词过滤。"""
        entries = self._stock_entries()
        names = {item["name"] for item in entries}
        group = params.get("group")
        if not isinstance(group, str) or group not in names:
            group = entries[0]["name"] if entries else None
        if group is None:
            return {
                "groups": [],
                "group": None,
                "query": "",
                "page": 1,
                "page_size": 20,
                "pages": 0,
                "total": 0,
                "items": [],
            }
        query = str(params.get("q") or "").strip()[:MAX_QUERY_LENGTH]
        page = max(1, to_int(params.get("page"), 1) or 1)
        page_size = min(
            MAX_PAGE_SIZE, max(5, to_int(params.get("page_size"), 20) or 20)
        )
        stock = self.stocks.get(group)
        lowered = query.lower()
        matches: list[dict[str, Any]] = []
        for key, question in stock.iter_questions():
            question_text = question.plain_text
            hit = not query or lowered in question_text.lower()
            answers: list[dict[str, Any]] = []
            for answer in question.answers[:MAX_ANSWERS_PER_QUESTION]:
                preview = content_preview(answer.content, 120)
                if query and lowered in preview.lower():
                    hit = True
                answers.append(
                    {
                        "key": answer.key,
                        "preview": preview,
                        "same": int(answer.same),
                        "time": fmt_ts(answer.time),
                    }
                )
            if not hit:
                continue
            matches.append(
                {
                    "key": key,
                    "preview": content_preview(question.content, 120),
                    "regular": bool(question.regular),
                    "freq": int(question.freq),
                    "time": fmt_ts(question.time),
                    "sort_time": int(question.time),
                    "answers": answers,
                    "answer_count": len(question.answers),
                }
            )
        matches.sort(key=lambda item: (-item["freq"], -item["sort_time"]))
        total = len(matches)
        pages = (total + page_size - 1) // page_size if total else 0
        page = min(page, pages) if pages else 1
        start = (page - 1) * page_size
        for item in matches:
            item.pop("sort_time", None)
        return {
            "groups": entries,
            "group": group,
            "query": query,
            "page": page,
            "page_size": page_size,
            "pages": pages,
            "total": total,
            "items": matches[start : start + page_size],
        }

    def delete_entry(self, payload: dict[str, Any]) -> dict[str, Any]:
        """删除一个问题或一个问题下的单个答案。"""
        group = self._require_stock(payload.get("group"))
        question_key = payload.get("question_key")
        answer_key = payload.get("answer_key")
        if not isinstance(question_key, str):
            raise DashboardError("缺少问题标识")
        stock = self.stocks.get(group)
        question = stock.get(question_key)
        if question is None:
            raise DashboardError("问题不存在或已被删除")
        removed_question = False
        if isinstance(answer_key, str) and answer_key:
            answer = next(
                (item for item in question.answers if item.key == answer_key), None
            )
            if answer is None:
                raise DashboardError("答案不存在或已被删除")
            stock.remove_answer(question_key, answer)
            removed_question = stock.get(question_key) is None
        else:
            stock.remove_question(question_key)
            removed_question = True
        stock.save()
        return {"deleted": 1, "question_removed": removed_question}

    def update_weight(self, payload: dict[str, Any]) -> dict[str, Any]:
        """调整某个答案的权重（same）。"""
        group = self._require_stock(payload.get("group"))
        question_key = payload.get("question_key")
        answer_key = payload.get("answer_key")
        same = to_int(payload.get("same"))
        if not isinstance(question_key, str) or not isinstance(answer_key, str):
            raise DashboardError("缺少问题或答案标识")
        if same is None or same < 0 or same > 9999:
            raise DashboardError("权重必须是 0-9999 的整数")
        stock = self.stocks.get(group)
        question = stock.get(question_key)
        if question is None:
            raise DashboardError("问题不存在或已被删除")
        answer = next(
            (item for item in question.answers if item.key == answer_key), None
        )
        if answer is None:
            raise DashboardError("答案不存在或已被删除")
        answer.same = same
        stock.save()
        return {"same": same}

    # ---- 过滤与黑名单 -----------------------------------------------
    def filters_payload(self) -> dict[str, Any]:
        """返回过滤词、敏感词、黑名单与类型过滤配置。"""
        return {
            "filter": [
                {
                    "index": index,
                    "preview": content_preview(entry.get("content") or [], 80),
                    "accurate": bool(entry.get("accurate")),
                }
                for index, entry in enumerate(self.filters.filters)
            ],
            "sensitive": [
                {
                    "index": index,
                    "preview": content_preview(entry.get("content") or [], 80),
                }
                for index, entry in enumerate(self.filters.sensitive)
            ],
            "blacklist": [
                {"user": user, "count": count}
                for user, count in sorted(
                    self.filters.blackdict.items(), key=lambda item: -item[1]
                )
            ],
            "types": list(self.filters.types),
            "supported_types": list(DEFAULT_TYPES),
        }

    def add_filter(self, payload: dict[str, Any]) -> dict[str, Any]:
        """新增过滤词、敏感词或黑名单账号。"""
        kind = payload.get("kind")
        text = str(payload.get("text") or "").strip()
        if kind == "black":
            users = parse_id_list(text)
            if (
                not users
                or any(not user.isdigit() for user in users)
                or len(users) > 50
            ):
                raise DashboardError("请输入 1-50 个 QQ 号，多个用空格隔开")
            self.filters.add_black(users)
            return {"added": users}
        if kind not in {"filter", "sensitive"}:
            raise DashboardError("未知的过滤类型")
        if not text or len(text) > 200:
            raise DashboardError("内容长度需在 1-200 字之间")
        content = [text_segment(text)]
        if kind == "sensitive":
            self.filters.add_sensitive(content)
        else:
            self.filters.add_filter(content, accurate=bool(payload.get("accurate")))
        return {"added": text}

    def remove_filter(self, payload: dict[str, Any]) -> dict[str, Any]:
        """按标记删除过滤词/敏感词，或按账号移除黑名单。"""
        kind = payload.get("kind")
        if kind == "black":
            user = str(payload.get("user") or "").strip()
            if not user:
                raise DashboardError("缺少账号")
            missing = self.filters.remove_black([user])
            if missing:
                raise DashboardError("账号不在黑名单中")
            return {"removed": user}
        if kind not in {"filter", "sensitive"}:
            raise DashboardError("未知的过滤类型")
        index = to_int(payload.get("index"))
        entries = self.filters.filters if kind == "filter" else self.filters.sensitive
        if index is None or index < 0 or index >= len(entries):
            raise DashboardError("条目不存在，请刷新后重试")
        del entries[index]
        self.filters.save()
        return {"removed": index}

    def set_types(self, payload: dict[str, Any]) -> dict[str, Any]:
        """更新按消息类型过滤的列表。"""
        raw = payload.get("types")
        if not isinstance(raw, list):
            raise DashboardError("types 必须是数组")
        types = [str(item) for item in raw if str(item) in DEFAULT_TYPES]
        self.filters.set_types(types)
        return {"types": types}

    # ---- 群列表管理 -------------------------------------------------
    @staticmethod
    def _group_id(value: Any) -> str:
        if not isinstance(value, str) or not value.strip().isdigit():
            raise DashboardError("群号必须是纯数字")
        group = value.strip()
        if not 4 <= len(group) <= 12:
            raise DashboardError("群号长度应为 4-12 位")
        return group

    @staticmethod
    def _clean_tags(raw: Any) -> list[str]:
        if not isinstance(raw, list):
            raise DashboardError("标签必须是数组")
        tags: list[str] = []
        for item in raw:
            if not isinstance(item, str):
                raise DashboardError("标签必须是文本")
            tag = item.strip()
            if not tag:
                continue
            if len(tag) > 32:
                raise DashboardError("单个标签不能超过 32 个字符")
            if tag.startswith(".") or any(char in '/\\:*?"<>|' for char in tag):
                raise DashboardError(f"标签「{tag}」包含不能用于文件名的字符")
            if tag.isdigit():
                raise DashboardError("标签不能是纯数字，避免与群词库重名")
            if tag not in tags:
                tags.append(tag)
        if len(tags) > 10:
            raise DashboardError("每个群最多设置 10 个标签")
        return tags

    @staticmethod
    def _clean_member_ids(raw: Any, label: str, limit: int) -> list[str]:
        if not isinstance(raw, list):
            raise DashboardError(f"{label}必须是数组")
        members: list[str] = []
        for item in raw:
            text = str(item).strip()
            if not text:
                continue
            if not text.isdigit() or not 4 <= len(text) <= 12:
                raise DashboardError(f"{label}必须是 4-12 位数字")
            if text not in members:
                members.append(text)
        if len(members) > limit:
            raise DashboardError(f"{label}最多 {limit} 个")
        return members

    def _group_entries(self) -> list[dict[str, Any]]:
        learning = set(self.config.group_list("learning"))
        reply = set(self.config.group_list("reply"))
        unmerge = set(self.config.group_list("unmerge"))
        tags = self.config.tag_dict()
        subadmin = {
            str(key): [str(item) for item in (value or [])]
            for key, value in (self.config.get("subadmin") or {}).items()
        }
        stock_names = {name for name in self.stocks.names() if name.isdigit()}
        names = {
            name
            for name in (
                learning | reply | unmerge | set(tags) | set(subadmin) | stock_names
            )
            if name.isdigit()
        }
        entries: list[dict[str, Any]] = []
        for name in sorted(names, key=int):
            stock = self.stocks.get(name) if name in stock_names else None
            questions, answers = stock.stats() if stock is not None else (0, 0)
            entries.append(
                {
                    "group": name,
                    "learning": name in learning,
                    "reply": name in reply,
                    "unmerge": name in unmerge,
                    "tags": tags.get(name, []),
                    "subadmins": subadmin.get(name, []),
                    "questions": questions,
                    "answers": answers,
                    "has_wordstock": stock is not None,
                }
            )
        return entries

    def _cleanup_unused_tags(self, removed_tags: list[str]) -> None:
        """删除已没有任何群使用的标签词库，避免面板里留下幽灵标签。"""
        active = set(self.config.all_tags())
        group_names = {name for name in self.stocks.names() if name.isdigit()}
        for tag in removed_tags:
            if tag in active or tag in group_names or tag == MERGE_STOCK:
                continue
            self.stocks.delete(tag)

    def groups_payload(self) -> dict[str, Any]:
        """返回群列表与每个群的记录/回复/标签/子管理员配置。"""
        entries = self._group_entries()
        return {
            "groups": entries,
            "tags": self.config.all_tags(),
            "totals": {
                "groups": len(entries),
                "learning": sum(1 for item in entries if item["learning"]),
                "reply": sum(1 for item in entries if item["reply"]),
                "tagged": sum(1 for item in entries if item["tags"]),
                "unmerge": sum(1 for item in entries if item["unmerge"]),
                "subadmin": sum(1 for item in entries if item["subadmins"]),
            },
        }

    def save_group(self, payload: dict[str, Any]) -> dict[str, Any]:
        """新增或更新一个群的配置；未提供的字段保持原值。"""
        group = self._group_id(payload.get("group"))
        for key, label in (
            ("learning", "记录"),
            ("reply", "回复"),
            ("unmerge", "不合并"),
        ):
            if key in payload and not isinstance(payload[key], bool):
                raise DashboardError(f"{label}开关必须是布尔值")
        tags_supplied = "tags" in payload
        tags = self._clean_tags(payload.get("tags")) if tags_supplied else None
        members_supplied = "subadmins" in payload
        members = (
            self._clean_member_ids(payload.get("subadmins"), "子管理员", 20)
            if members_supplied
            else None
        )
        learning = (
            bool(payload["learning"])
            if "learning" in payload
            else self.config.has_group("learning", group)
        )
        reply = (
            bool(payload["reply"])
            if "reply" in payload
            else self.config.has_group("reply", group)
        )
        unmerge = (
            bool(payload["unmerge"])
            if "unmerge" in payload
            else self.config.has_group("unmerge", group)
        )
        old_tags = self.config.tag_dict().get(group, [])
        old_unmerge = self.config.has_group("unmerge", group)
        current_tags = old_tags if tags is None else tags
        subadmin_table = {
            str(key): [str(item) for item in (value or [])]
            for key, value in (self.config.get("subadmin") or {}).items()
        }
        current_members = (
            subadmin_table.get(group, []) if members is None else members
        )
        if not any((learning, reply, unmerge, current_tags, current_members)):
            raise DashboardError(
                "请至少保留记录、回复、标签、子管理员或不合并中的一项"
            )

        for kind, enabled in (
            ("learning", learning),
            ("reply", reply),
            ("unmerge", unmerge),
        ):
            if enabled:
                self.config.add_groups(kind, [group])
            else:
                self.config.remove_groups(kind, [group])

        tag_table = self.config.tag_dict()
        if current_tags:
            tag_table[group] = current_tags
        else:
            tag_table.pop(group, None)
        self.config.set("tag", tag_table)

        if current_members:
            subadmin_table[group] = current_members
        else:
            subadmin_table.pop(group, None)
        self.config.set("subadmin", subadmin_table)
        self.config.save()

        if current_tags != old_tags or unmerge != old_unmerge:
            logs = rebuild_all(self.config, self.stocks)
            self.mark_merged(logs)
            self._cleanup_unused_tags(
                [tag for tag in old_tags if tag not in current_tags]
            )
        return {"saved": group}

    def remove_group(self, payload: dict[str, Any]) -> dict[str, Any]:
        """移除群的全部管理配置；可选同时删除该群词库。"""
        group = self._group_id(payload.get("group"))
        delete_wordstock = bool(payload.get("delete_wordstock"))
        had_stock = group in self.stocks.names()
        tag_table = self.config.tag_dict()
        old_tags = tag_table.pop(group, None) or []
        removed_tag = bool(old_tags)
        removed_unmerge = group in self.config.group_list("unmerge")
        for kind in ("learning", "reply", "unmerge"):
            self.config.remove_groups(kind, [group])
        self.config.set("tag", tag_table)
        subadmin_table = dict(self.config.get("subadmin") or {})
        subadmin_table.pop(group, None)
        self.config.set("subadmin", subadmin_table)
        chances = dict(self.config.get("singlereplychance") or {})
        chances.pop(group, None)
        self.config.set("singlereplychance", chances)
        self.config.save()

        if delete_wordstock:
            self.stocks.delete(group)
        if removed_tag or removed_unmerge:
            logs = rebuild_all(self.config, self.stocks)
            self.mark_merged(logs)
            self._cleanup_unused_tags(old_tags)
        return {
            "removed": group,
            "wordstock_deleted": bool(delete_wordstock and had_stock),
        }

    # ---- 配置 -------------------------------------------------------
    def _config_value(self, field: dict[str, Any]) -> Any:
        value = self.config.get(field["key"], DEFAULTS.get(field["key"]))
        if field["type"] == "bool":
            return bool(value)
        if field["type"] == "pair":
            if isinstance(value, (list, tuple)) and len(value) == 2:
                return [to_float(value[0], 0.0), to_float(value[1], 0.0)]
            return [0.0, 0.0]
        if field["type"] == "int":
            return to_int(value, 0)
        if field["type"] == "float":
            return to_float(value, 0.0)
        if field["type"] == "list":
            return [str(item) for item in (value or [])]
        return value

    def config_payload(self) -> dict[str, Any]:
        """返回配置页的字段定义与当前值。"""
        fields = []
        for field in CONFIG_FIELDS:
            item = dict(field)
            item["value"] = self._config_value(field)
            fields.append(item)
        typefreq = self.config.get("typefreq") or {}
        readonly = {key: self.config.get(key) for key in READONLY_LISTS}
        return {
            "fields": fields,
            "groups": CONFIG_GROUPS,
            "typefreq": {
                "types": TYPEFREQ_TYPES,
                "values": {
                    item: to_int(typefreq.get(item), 0) for item in TYPEFREQ_TYPES
                },
            },
            "readonly": readonly,
        }

    def _validate_config_value(self, field: dict[str, Any], raw: Any) -> Any:
        key = field["key"]
        kind = field["type"]
        label = field["label"]
        if kind == "bool":
            if not isinstance(raw, bool):
                raise DashboardError(f"{label} 必须是布尔值")
            return 1 if raw else 0
        if kind in {"int", "float"}:
            number = to_int(raw) if kind == "int" else to_float(raw)
            if number is None:
                raise DashboardError(f"{label} 必须是数字")
            low = field.get("min")
            high = field.get("max")
            if low is not None and number < low:
                raise DashboardError(f"{label} 不能小于 {low}")
            if high is not None and number > high:
                raise DashboardError(f"{label} 不能大于 {high}")
            return int(number) if kind == "int" else round(float(number), 4)
        if kind == "pair":
            if not isinstance(raw, (list, tuple)) or len(raw) != 2:
                raise DashboardError(f"{label} 需要两个数字")
            base = to_float(raw[0])
            jitter = to_float(raw[1])
            if base is None or jitter is None:
                raise DashboardError(f"{label} 需要两个数字")
            if base < 0 or jitter < 0:
                raise DashboardError(f"{label} 不能为负数")
            if jitter > base:
                raise DashboardError(f"{label} 的浮动不能大于基准")
            if base > 4 or jitter > 2:
                raise DashboardError(f"{label} 的基准不能超过 4 秒、浮动不能超过 2 秒")
            return [round(base, 2), round(jitter, 2)]
        if kind == "list":
            if isinstance(raw, str):
                items = parse_id_list(raw)
            elif isinstance(raw, list):
                items = [str(item).strip() for item in raw]
            else:
                raise DashboardError(f"{label} 必须是文本列表")
            items = [item for item in items if item]
            if len(items) > 50:
                raise DashboardError(f"{label} 最多 50 项")
            if any(len(item) > 64 for item in items):
                raise DashboardError(f"{label} 的单项过长")
            if key == "Administrator" and any(not item.isdigit() for item in items):
                raise DashboardError("管理员必须是 QQ 号")
            return items
        raise DashboardError(f"{label} 的类型不受支持")

    def save_config(self, payload: dict[str, Any]) -> dict[str, Any]:
        """校验并保存配置；写入后立即对运行中的插件生效。"""
        values = payload.get("values")
        if values is not None and not isinstance(values, dict):
            raise DashboardError("values 必须是对象")
        known = {field["key"]: field for field in CONFIG_FIELDS}
        cleaned: dict[str, Any] = {}
        for key, raw in (values or {}).items():
            field = known.get(key)
            if field is None:
                continue
            cleaned[key] = self._validate_config_value(field, raw)
        typefreq_values = payload.get("typefreq")
        typefreq_clean: dict[str, int] | None = None
        if typefreq_values is not None:
            if not isinstance(typefreq_values, dict):
                raise DashboardError("typefreq 必须是对象")
            typefreq_clean = {}
            for type_name, raw in typefreq_values.items():
                if str(type_name) not in TYPEFREQ_ALLOWED:
                    raise DashboardError(f"未知消息类型：{type_name}")
                number = to_int(raw)
                if number is None or number < 0 or number > 9999:
                    raise DashboardError("回复阈值必须是 0-9999 的整数")
                typefreq_clean[str(type_name)] = number
        for key, value in cleaned.items():
            self.config.set(key, value)
        if typefreq_clean is not None:
            current = dict(self.config.get("typefreq") or {})
            current.update(typefreq_clean)
            self.config.set("typefreq", current)
        self.config.save()
        return {"saved": sorted(cleaned.keys())}

    # ---- 备份与恢复 -------------------------------------------------
    def backup_payload(self) -> dict[str, Any]:
        """收集配置、过滤规则与全部词库，生成备份结构。"""
        wordstock: dict[str, Any] = {}
        for name in self.stocks.names():
            data = read_json(self.stocks.path_for(name), None)
            if isinstance(data, dict):
                wordstock[name] = data
        return {
            "format": BACKUP_FORMAT,
            "version": BACKUP_VERSION,
            "plugin": PLUGIN_NAME,
            "exported_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "config": dict(self.config.data),
            "filters": {
                "filter": self.filters.filters,
                "sensitive": self.filters.sensitive,
                "blackdict": self.filters.blackdict,
                "type": list(self.filters.types),
            },
            "wordstock": wordstock,
        }

    def write_backup_file(self, prefix: str = "chat_learning_backup_") -> Path:
        """把备份写入 data 目录并返回文件路径。"""
        backups = self.stocks.root / "backups"
        backups.mkdir(parents=True, exist_ok=True)
        path = backups / _backup_filename(prefix)
        write_json(path, self.backup_payload())
        files = sorted(backups.glob("*.json"), key=lambda item: item.stat().st_mtime)
        for stale in files[:-BACKUP_KEEP]:
            try:
                stale.unlink()
            except OSError:
                continue
        return path

    def restore_backup(self, payload: dict[str, Any]) -> dict[str, Any]:
        """按选择恢复配置、过滤规则与词库，恢复前自动备份当前数据。"""
        backup = payload.get("backup")
        if not isinstance(backup, dict):
            raise DashboardError("缺少备份数据")
        if backup.get("format") != BACKUP_FORMAT:
            raise DashboardError("备份文件格式不正确")
        if to_int(backup.get("version")) != BACKUP_VERSION:
            raise DashboardError("备份版本不受支持")
        options = payload.get("restore")
        if not isinstance(options, dict):
            raise DashboardError("请选择需要恢复的内容")
        do_config = bool(options.get("config"))
        do_filters = bool(options.get("filters"))
        do_wordstock = bool(options.get("wordstock"))
        if not any((do_config, do_filters, do_wordstock)):
            raise DashboardError("请至少选择一项恢复内容")
        mode = str(options.get("wordstock_mode") or "merge")
        if mode not in {"merge", "replace"}:
            raise DashboardError("词库恢复方式不正确")
        pre_backup = self.write_backup_file("chat_learning_before_restore_")
        restored: list[str] = []
        if do_config:
            self._restore_config(backup.get("config"))
            restored.append("config")
        if do_filters:
            self._restore_filters(backup.get("filters"))
            restored.append("filters")
        if do_wordstock:
            self._restore_wordstock(backup.get("wordstock"), mode)
            restored.append("wordstock")
        return {"restored": restored, "pre_backup": pre_backup.name}

    def _restore_config(self, blob: Any) -> None:
        if blob is None:
            return
        if not isinstance(blob, dict):
            raise DashboardError("备份中的配置格式不正确")
        for key, value in blob.items():
            if key not in DEFAULTS:
                continue
            default = DEFAULTS.get(key)
            if isinstance(default, list) and not isinstance(value, list):
                continue
            if isinstance(default, dict) and not isinstance(value, dict):
                continue
            if isinstance(default, (int, float)) and not isinstance(
                value, (int, float, bool)
            ):
                continue
            if isinstance(default, str) and not isinstance(value, str):
                continue
            self.config.set(key, value)
        if not isinstance(self.config.get("Administrator"), list):
            self.config.set("Administrator", [])
        self.config.save()

    def _restore_filters(self, blob: Any) -> None:
        if blob is None:
            return
        if not isinstance(blob, dict):
            raise DashboardError("备份中的过滤规则格式不正确")
        filters = [
            self._coerce_filter_entry(item) for item in (blob.get("filter") or [])
        ]
        sensitive = [
            self._coerce_filter_entry(item) for item in (blob.get("sensitive") or [])
        ]
        blackdict: dict[str, int] = {}
        for key, value in (blob.get("blackdict") or {}).items():
            count = to_int(value)
            if count is not None:
                blackdict[str(key)] = count
        types = [
            str(item) for item in (blob.get("type") or []) if str(item) in DEFAULT_TYPES
        ]
        self.filters.filters = [item for item in filters if item["content"]][:5000]
        self.filters.sensitive = [item for item in sensitive if item["content"]][:5000]
        self.filters.blackdict = blackdict
        self.filters.types = types
        self.filters.save()

    @staticmethod
    def _coerce_filter_entry(item: Any) -> dict[str, Any]:
        if isinstance(item, dict):
            content = normalize_content(item.get("content") or [])
            return {"content": content, "accurate": bool(item.get("accurate"))}
        return {"content": [], "accurate": False}

    def _restore_wordstock(self, blob: Any, mode: str) -> None:
        if blob is None:
            raise DashboardError("备份中没有词库数据")
        if not isinstance(blob, dict):
            raise DashboardError("备份中的词库格式不正确")
        payloads: dict[str, dict[str, Any]] = {}
        for name, data in blob.items():
            if not _stock_name_ok(name) or name in {".", ".."}:
                continue
            if not isinstance(data, dict):
                continue
            questions = data.get("questions")
            if not isinstance(questions, dict):
                continue
            payloads[str(name)] = data
        if not payloads:
            raise DashboardError("备份中没有可恢复的词库")
        existing = self.stocks.names()
        if mode == "replace":
            for name in existing:
                self.stocks.delete(name)
        for name, data in payloads.items():
            target = self.stocks.path_for(name)
            if mode == "merge" and target.exists():
                current = read_json(target, {"version": 1, "questions": {}})
                merged = self._merge_question_payload(
                    current
                    if isinstance(current, dict)
                    else {"version": 1, "questions": {}},
                    data,
                )
                write_json(target, merged)
            else:
                write_json(target, data)
            self.stocks.invalidate(name)

    @staticmethod
    def _merge_question_payload(
        current: dict[str, Any], incoming: dict[str, Any]
    ) -> dict[str, Any]:
        questions = current.setdefault("questions", {})
        for key, question in (incoming.get("questions") or {}).items():
            if not isinstance(question, dict):
                continue
            content = normalize_content(question.get("content") or [])
            if not content:
                continue
            normalized: dict[str, Any] = {
                "content": content,
                "freq": max(1, to_int(question.get("freq"), 1) or 1),
                "time": to_int(question.get("time"), 0) or 0,
                "regular": bool(question.get("regular")),
                "answers": [],
            }
            for answer in question.get("answers") or []:
                if not isinstance(answer, dict):
                    continue
                answer_content = normalize_content(answer.get("content") or [])
                if not answer_content:
                    continue
                normalized["answers"].append(
                    {
                        "content": answer_content,
                        "same": max(1, to_int(answer.get("same"), 1) or 1),
                        "time": to_int(answer.get("time"), 0) or 0,
                    }
                )
            existing = questions.get(key)
            if existing is None:
                questions[key] = normalized
                continue
            existing["freq"] = int(existing.get("freq", 0)) + normalized["freq"]
            existing["time"] = max(int(existing.get("time", 0)), normalized["time"])
            existing["regular"] = bool(existing.get("regular")) or normalized["regular"]
            answers = existing.setdefault("answers", [])
            by_key = {
                content_key(item.get("content") or []): item
                for item in answers
                if isinstance(item, dict)
            }
            for answer in normalized["answers"]:
                key_name = content_key(answer["content"])
                if key_name in by_key:
                    by_key[key_name]["same"] = (
                        int(by_key[key_name].get("same", 0)) + answer["same"]
                    )
                else:
                    answers.append(answer)
                    by_key[key_name] = answer
        return current

    # ---- 手动维护 ---------------------------------------------------
    def merge_now(self) -> dict[str, Any]:
        """立即重建总词库与标签词库。"""
        logs = rebuild_all(self.config, self.stocks)
        self.mark_merged(logs)
        return {"logs": logs}

    def autodelete_now(self, payload: dict[str, Any]) -> dict[str, Any]:
        """清理低频与命中过滤规则的条目，需要确认参数。"""
        if not bool(payload.get("confirm")):
            raise DashboardError("需要确认后才会执行清理")
        logs = run_autodelete(self.config, self.stocks, self.filters)
        return {"logs": logs}
