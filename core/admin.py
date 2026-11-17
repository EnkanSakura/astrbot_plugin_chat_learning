"""交互式管理模式：词库搜索/删除、过滤与黑名单、自动清理、自定义回复。"""

from __future__ import annotations

import re
from typing import Any

from .commands import CommandService
from .config import PluginConfig
from .filtering import FilterStore
from .maintenance import autodelete
from .models import Answer, Question, content_key, content_plain_text, content_preview, normalize_content
from .storage import MERGE_STOCK, StockManager
from .utils import parse_id_list, to_int

MENU_TEXT = """请发送需要操作的序号
0.退出管理模式
1.在所有群内查找
2.在指定群内查找
3.过滤,黑名单设置
4.自动清理词库
5.添加自定义回复"""

GROUP_MENU_TEXT = """本群管理模式
0.退出管理模式
1.查找本群词库
2.添加自定义回复"""

FILTER_MENU_TEXT = """请选择你的操作
0.返回
1.添加需过滤的关键字(模糊)
2.添加需过滤的关键字(精确)
3.添加敏感关键字
4.添加黑名单账号
5.查看,删除"""

EXIT_ADMIN_TEXT = "退出管理模式"
MAX_RESULTS = 40


class AdminSession:
    """一个交互式会话，由 main 在收到私聊消息时驱动。"""

    def __init__(
        self,
        config: PluginConfig,
        stocks: StockManager,
        filters: FilterStore,
        commands: CommandService,
        scope_group: str = "",
    ) -> None:
        self.config = config
        self.stocks = stocks
        self.filters = filters
        self.commands = commands
        self.scope_group = scope_group
        self.state = "menu"
        self.closed = False
        self.results: list[dict[str, Any]] = []
        self.pending: dict[str, Any] = {}

    # ---- 入口 -------------------------------------------------------
    async def feed(self, text: str, content: list[dict]) -> list[str]:
        text = (text or "").strip()
        content = normalize_content(content or [])
        handler = getattr(self, f"_state_{self.state}", None)
        if handler is None:
            self.state = "menu"
            return [self._menu()]
        return await handler(text, content)

    def _menu(self) -> str:
        return GROUP_MENU_TEXT if self.scope_group else MENU_TEXT

    def _back_to_menu(self, prefix: str = "") -> list[str]:
        self.state = "menu"
        lines = [prefix] if prefix else []
        lines.append(self._menu())
        return lines

    # ---- 主菜单 -----------------------------------------------------
    async def _state_menu(self, text: str, content: list[dict]) -> list[str]:
        if not text.isdigit():
            return ["参数错误"] + [self._menu()]
        option = int(text)
        if option == 0:
            self.closed = True
            return [EXIT_ADMIN_TEXT]
        if self.scope_group:
            if option == 1:
                self.state = "search_keyword"
                self.pending = {"scope": self.scope_group}
                return ["请发送需要查找的内容（问题或答案）"]
            if option == 2:
                self.pending = {"scope": self.scope_group}
                return await self._start_custom(0)
            return ["参数错误"] + [self._menu()]
        if option == 1:
            self.state = "search_keyword"
            self.pending = {"scope": "all"}
            return ["请发送需要查找的内容（问题或答案）"]
        if option == 2:
            groups = self._group_names()
            if not groups:
                return self._back_to_menu("还没有任何群词库")
            self.state = "search_group"
            return ["拥有词库的群号\n" + "、".join(groups) + "\n请发送需要选择的群号"]
        if option == 3:
            self.state = "filter_menu"
            return [FILTER_MENU_TEXT]
        if option == 4:
            self.state = "autodelete_confirm"
            return [
                "此操作将会删除词库中仅出现过单次的条目和被过滤的条目，且操作不可逆！！",
                "请输入“确定”确认删除",
            ]
        if option == 5:
            self.state = "custom_group"
            groups = self._group_names()
            tip = "请输入要写入词库的群号（不存在的群号会新建词库）"
            if groups:
                tip += "\n现有群词库：" + "、".join(groups)
            return [tip]
        return ["参数错误"] + [self._menu()]

    def _group_names(self) -> list[str]:
        tags = set(self.config.all_tags())
        return [
            name
            for name in self.stocks.names()
            if name != MERGE_STOCK and name not in tags
        ]

    # ---- 搜索 -------------------------------------------------------
    async def _state_search_group(self, text: str, content: list[dict]) -> list[str]:
        group = text.strip()
        if not group.isdigit():
            return ["参数错误，请发送群号"]
        if not self.stocks.exists(group):
            return self._back_to_menu(f"群{group}的词库不存在")
        self.pending = {"scope": group}
        self.state = "search_keyword"
        return [f"已选择群{group}，请发送需要查找的内容（问题或答案）"]

    async def _state_search_keyword(self, text: str, content: list[dict]) -> list[str]:
        keyword = text.strip() or content_plain_text(content).strip()
        if not keyword:
            return ["请发送需要查找的内容"]
        scope = self.pending.get("scope", "all")
        names = [scope] if scope != "all" else self._group_names()
        self.results = self._search(names, keyword)
        if not self.results:
            return self._back_to_menu("未找到相关内容")
        self.state = "delete_marker"
        lines = [f"共找到 {len(self.results)} 条结果"]
        lines.extend(self._render_results())
        lines.append(
            "请输入需删除的标记\n(输入-1取消,all清空所有,多个用空格隔开,前加add可同时加入过滤列表)"
        )
        return lines

    def _search(self, names: list[str], keyword: str) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        pattern = None
        try:
            pattern = re.compile(keyword)
        except re.error:
            pattern = None
        for name in names:
            stock = self.stocks.get(name)
            for key, question in stock.iter_questions():
                qtext = question.plain_text
                q_hit = keyword in qtext or (pattern is not None and pattern.search(qtext))
                answers = question.answers
                a_hit = [
                    answer
                    for answer in answers
                    if keyword in content_plain_text(answer.content)
                    or (
                        pattern is not None
                        and pattern.search(content_plain_text(answer.content))
                    )
                ]
                if not q_hit and not a_hit:
                    continue
                if not answers:
                    results.append(
                        {
                            "stock": name,
                            "qkey": key,
                            "akey": None,
                            "q": content_preview(question.content),
                            "a": "",
                            "same": 0,
                        }
                    )
                else:
                    chosen = a_hit or answers
                    for answer in chosen:
                        results.append(
                            {
                                "stock": name,
                                "qkey": key,
                                "akey": answer.key,
                                "q": content_preview(question.content),
                                "a": content_preview(answer.content),
                                "same": answer.same,
                            }
                        )
                if len(results) >= MAX_RESULTS:
                    return results
        return results

    def _render_results(self) -> list[str]:
        lines: list[str] = []
        for index, item in enumerate(self.results):
            lines.append(f"标记{index} [群{item['stock']}] 问题：{item['q']}")
            if item["akey"] is not None:
                lines.append(f"    答案(权重{item['same']})：{item['a']}")
        return lines

    async def _state_delete_marker(self, text: str, content: list[dict]) -> list[str]:
        text = text.strip()
        if text in ("-1", "–1"):
            return self._back_to_menu("取消删除")
        if text == "all":
            removed = 0
            for item in self.results:
                stock = self.stocks.get(item["stock"])
                if item["akey"] is None:
                    if stock.remove_question(item["qkey"]):
                        removed += 1
                else:
                    question = stock.get(item["qkey"])
                    if question is not None:
                        answer = next(
                            (a for a in question.answers if a.key == item["akey"]), None
                        )
                        if answer is not None and stock.remove_answer(item["qkey"], answer):
                            removed += 1
            for name in {item["stock"] for item in self.results}:
                self.stocks.save(name)
            return self._back_to_menu(f"删除{removed}个答案成功！")

        also_filter = False
        tokens = text.split()
        if tokens and tokens[0].lower() == "add":
            also_filter = True
            tokens = tokens[1:]
        if not tokens:
            return ["参数错误"]
        removed = 0
        missing: list[int] = []
        filters_to_add: list[list[dict]] = []
        for token in tokens:
            index = to_int(token)
            if index is None or index < 0 or index >= len(self.results):
                missing.append(token)  # type: ignore[arg-type]
                continue
            item = self.results[index]
            stock = self.stocks.get(item["stock"])
            if item["akey"] is None:
                question = stock.get(item["qkey"])
                if question is not None and stock.remove_question(item["qkey"]):
                    removed += 1
                    if also_filter:
                        filters_to_add.append(question.content)
            else:
                question = stock.get(item["qkey"])
                if question is None:
                    continue
                answer = next(
                    (a for a in question.answers if a.key == item["akey"]), None
                )
                if answer is not None:
                    filters_to_add.append(answer.content)
                    if stock.remove_answer(item["qkey"], answer):
                        removed += 1
        for item in self.results:
            self.stocks.save(item["stock"])
        if also_filter:
            for entry in filters_to_add:
                self.filters.add_filter(entry, accurate=True)
        lines = []
        if missing:
            lines.append("标记为" + " ".join(str(item) for item in missing) + " 的答案不存在")
        if removed:
            lines.append(f"删除{removed}个答案成功！")
            if also_filter:
                lines.append("已添加入过滤列表")
        else:
            lines.append("删除失败")
        return self._back_to_menu("\n".join(lines))

    # ---- 过滤与黑名单 -----------------------------------------------
    async def _state_filter_menu(self, text: str, content: list[dict]) -> list[str]:
        if not text.isdigit():
            return ["参数错误"] + [FILTER_MENU_TEXT]
        option = int(text)
        if option == 0:
            return self._back_to_menu()
        if option in (1, 2, 3):
            self.pending = {"mode": option}
            self.state = "filter_add"
            tip = {1: "请发送需要过滤的关键字（模糊匹配）", 2: "请发送需要过滤的内容（精确匹配）", 3: "请发送敏感的关键字"}
            return [tip[option] + "，发送 exit 结束"]
        if option == 4:
            self.state = "filter_black_add"
            return ["请输入需要添加黑名单的账号（多个用空格隔开）"]
        if option == 5:
            self.state = "filter_view_menu"
            return ["请输入需要查看的内容\n0.返回\n1.过滤的关键字\n2.敏感的关键字\n3.黑名单"]
        return ["参数错误"] + [FILTER_MENU_TEXT]

    async def _state_filter_add(self, text: str, content: list[dict]) -> list[str]:
        if text.strip().lower() == "exit":
            return self._back_to_menu()
        mode = self.pending.get("mode", 1)
        entry = content or ([{"t": "text", "s": text}] if text else [])
        if not entry:
            return ["请发送需要过滤的内容"]
        if mode == 3:
            self.filters.add_sensitive(entry)
        else:
            self.filters.add_filter(entry, accurate=(mode == 2))
        return ["添加成功！" + "\n继续发送，或发送 exit 结束"]

    async def _state_filter_black_add(self, text: str, content: list[dict]) -> list[str]:
        ids = parse_id_list(text)
        if not ids:
            return ["参数错误"]
        self.filters.add_black(ids)
        return self._back_to_menu("添加完毕")

    async def _state_filter_view_menu(self, text: str, content: list[dict]) -> list[str]:
        if not text.isdigit():
            return ["参数错误"]
        option = int(text)
        if option == 0:
            return self._back_to_menu()
        if option == 1:
            lines = ["==== 过滤关键字 ===="]
            for index, entry in enumerate(self.filters.filters):
                flag = "精确" if entry.get("accurate") else "模糊"
                lines.append(
                    f"标记{index} [{flag}] {content_preview(entry.get('content') or [])}"
                )
            if len(lines) == 1:
                lines.append("暂无过滤词")
            self.pending = {"delete_kind": "filter"}
            self.state = "filter_delete"
            lines.append("请输入需删除的标记（-1取消，all清空所有，多个用空格隔开）")
            return lines
        if option == 2:
            lines = ["==== 敏感关键字 ===="]
            for index, entry in enumerate(self.filters.sensitive):
                lines.append(f"标记{index} {content_preview(entry.get('content') or [])}")
            if len(lines) == 1:
                lines.append("暂无敏感词")
            self.pending = {"delete_kind": "sensitive"}
            self.state = "filter_delete"
            lines.append("请输入需删除的标记（-1取消，all清空所有，多个用空格隔开）")
            return lines
        if option == 3:
            lines = ["==== 黑名单 ===="]
            for user_id, count in self.filters.blackdict.items():
                lines.append(f"{user_id} 已触发敏感词次数:{count}")
            if len(lines) == 1:
                lines.append("暂无黑名单")
            self.pending = {"delete_kind": "black"}
            self.state = "filter_delete"
            lines.append("请输入需删除的账号（-1取消，all清空所有，多个用空格隔开）")
            return lines
        return ["参数错误"]

    async def _state_filter_delete(self, text: str, content: list[dict]) -> list[str]:
        text = text.strip()
        kind = self.pending.get("delete_kind", "filter")
        if text in ("-1", "–1"):
            return self._back_to_menu("取消删除")
        if text == "all":
            if kind == "filter":
                self.filters.clear_filters()
            elif kind == "sensitive":
                self.filters.clear_sensitive()
            else:
                self.filters.clear_black()
            return self._back_to_menu("已清空")
        tokens = parse_id_list(text)
        if not tokens:
            return ["参数错误"]
        indexes = []
        for item in tokens:
            index = to_int(item)
            if index is not None:
                indexes.append(index)
        if not indexes:
            return ["参数错误"]
        removed = 0
        if kind == "filter":
            for index in sorted(indexes, reverse=True):
                if 0 <= index < len(self.filters.filters):
                    del self.filters.filters[index]
                    removed += 1
            self.filters.save()
            return self._back_to_menu(f"删除{removed}个条目成功！")
        if kind == "sensitive":
            for index in sorted(indexes, reverse=True):
                if 0 <= index < len(self.filters.sensitive):
                    del self.filters.sensitive[index]
                    removed += 1
            self.filters.save()
            return self._back_to_menu(f"删除{removed}个条目成功！")
        missing = self.filters.remove_black(tokens)
        if missing:
            return self._back_to_menu("删除完毕！\n账号" + " ".join(missing) + "不存在")
        return self._back_to_menu("删除成功！")

    # ---- 自动清理 ---------------------------------------------------
    async def _state_autodelete_confirm(self, text: str, content: list[dict]) -> list[str]:
        if text.strip() != "确定":
            return self._back_to_menu("已取消")
        logs = autodelete(self.config, self.stocks, self.filters)
        return self._back_to_menu("\n".join(logs))

    # ---- 自定义回复 -------------------------------------------------
    async def _state_custom_group(self, text: str, content: list[dict]) -> list[str]:
        group = text.strip()
        if not group.isdigit():
            return ["参数错误，请发送群号"]
        self.pending = {"scope": group}
        return await self._start_custom(0)

    async def _start_custom(self, option: int) -> list[str]:
        if option == 0:
            self.pending["question"] = None
            self.pending["regular"] = False
            self.state = "custom_question"
            return ["请输入问题（可包含图片），发送后将作为词库问题"]
        if option == 1:
            self.state = "custom_answer"
            return ["请输入答案（可包含图片）"]
        if option == 2:
            self.state = "custom_question"
            return ["请输入新的问题"]
        return [self._menu()]

    async def _state_custom_question(self, text: str, content: list[dict]) -> list[str]:
        content = normalize_content(content)
        if not content:
            return ["问题不能为空，请发送文字或图片"]
        self.pending["question"] = content
        self.state = "custom_regular"
        return ["该问题是否为正则表达式？\n0.否\n1.是"]

    async def _state_custom_regular(self, text: str, content: list[dict]) -> list[str]:
        value = to_int(text)
        if value not in (0, 1):
            return ["请输入 0 或 1"]
        self.pending["regular"] = bool(value)
        self.state = "custom_answer"
        tip = "问题将被判定为正则表达式" if value else "问题将被判定为非正则表达式"
        return [tip, "请输入答案（可包含图片）"]

    async def _state_custom_answer(self, text: str, content: list[dict]) -> list[str]:
        content = normalize_content(content)
        if not content:
            return ["答案不能为空，请发送文字或图片"]
        self.pending["answer"] = content
        self.state = "custom_weight"
        return ["请输入答案的权重(这是一个非负的整数,越大表明抽中该答案的概率越大)"]

    async def _state_custom_weight(self, text: str, content: list[dict]) -> list[str]:
        weight = to_int(text)
        if weight is None or weight < 0:
            return ["权重必须是一个非负的整数，请重新输入"]
        scope = str(self.pending.get("scope") or "")
        question_content = self.pending.get("question")
        answer_content = self.pending.get("answer")
        if not scope or not question_content or not answer_content:
            return self._back_to_menu("会话数据丢失，请重新开始")
        stock = self.stocks.get(scope)
        question: Question = stock.record_question(
            question_content, regular=bool(self.pending.get("regular"))
        )
        existing: Answer | None = next(
            (a for a in question.answers if a.key == content_key(answer_content)), None
        )
        if existing is not None:
            existing.same = weight
            message = "问题中已存在该答案，已将权重修改为新值"
        else:
            stock.record_answer(question.key, answer_content)
            target = stock.get(question.key)
            if target is not None:
                for answer in target.answers:
                    if answer.key == content_key(answer_content):
                        answer.same = weight
            message = "添加完毕！"
        stock.save()
        self.state = "custom_more"
        return [
            message,
            "请选择：\n0.退出\n1.为该问题继续添加答案\n2.为该词库添加新的问答",
        ]

    async def _state_custom_more(self, text: str, content: list[dict]) -> list[str]:
        option = to_int(text)
        if option is None:
            return ["请输入 0、1 或 2"]
        if option == 0:
            return self._back_to_menu()
        return await self._start_custom(option)
