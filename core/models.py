"""词库数据模型：文本/图片片段、问题、答案与词库文件。"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from .utils import compact_json, now_ts

TEXT = "text"
IMAGE = "image"

# 内部片段类型 -> 原版/配置里使用的消息类型名
TYPE_NAMES = {TEXT: "Plain", IMAGE: "Image"}
IMAGE_PLACEHOLDER = "[图片]"


def text_segment(text: str) -> dict:
    """构造一个文本片段。"""
    return {"t": TEXT, "s": text}


def image_segment(digest: str, path: str) -> dict:
    """构造一个图片片段（sha1 + 相对路径）。"""
    return {"t": IMAGE, "h": digest, "p": path}


def normalize_content(segments: Iterable[dict]) -> list[dict]:
    """规整片段列表：合并相邻文本、丢弃空片段。"""
    """规整片段列表：合并相邻文本、去掉空文本、图片保留摘要与路径。"""
    result: list[dict] = []
    for seg in segments:
        kind = seg.get("t")
        if kind == TEXT:
            text = str(seg.get("s") or "").strip()
            if not text:
                continue
            if result and result[-1]["t"] == TEXT:
                merged = result[-1]["s"] + " " + text
                result[-1] = text_segment(merged)
            else:
                result.append(text_segment(text))
        elif kind == IMAGE:
            digest = seg.get("h")
            if not digest:
                continue
            result.append(image_segment(str(digest), str(seg.get("p") or "")))
    return result


def content_key(content: list[dict]) -> str:
    """生成问题/答案的唯一键。"""
    """问题/答案的唯一键：稳定的 JSON 字符串。"""
    return compact_json(content)


def content_plain_text(content: list[dict]) -> str:
    """拼接片段中的纯文本。"""
    return "".join(str(seg.get("s") or "") for seg in content if seg.get("t") == TEXT)


def content_type_names(content: list[dict]) -> set[str]:
    """返回片段涉及的配置类型名（Plain/Image）。"""
    return {TYPE_NAMES[seg["t"]] for seg in content if seg.get("t") in TYPE_NAMES}


def content_preview(content: list[dict], limit: int = 60) -> str:
    """把片段列表渲染成单行预览。"""
    """把片段列表渲染成便于阅读的单行预览。"""
    parts: list[str] = []
    for seg in content:
        if seg.get("t") == TEXT:
            parts.append(str(seg.get("s") or ""))
        elif seg.get("t") == IMAGE:
            parts.append(IMAGE_PLACEHOLDER)
    text = " ".join(part for part in parts if part).strip()
    if not text:
        text = IMAGE_PLACEHOLDER
    if len(text) > limit:
        text = text[: max(1, limit - 1)] + "…"
    return text


@dataclass
class Answer:
    """某条问题下的一个答案，same 表示被重复收集/设定的权重。"""

    content: list[dict]
    same: int = 1
    time: int = 0

    @classmethod
    def from_dict(cls, data: dict) -> Answer:
        """从词库 JSON 还原一个答案。"""
        return cls(
            content=normalize_content(data.get("content") or []),
            same=int(data.get("same", 1) or 0),
            time=int(data.get("time", 0) or 0),
        )

    def to_dict(self) -> dict:
        """序列化为词库 JSON 中的答案结构。"""
        return {"content": self.content, "same": int(self.same), "time": int(self.time)}

    @property
    def key(self) -> str:
        """答案的内容键。"""
        return content_key(self.content)


@dataclass
class Question:
    """一条问题及其答案列表。regular 为真时 content 中的文本是正则表达式。"""

    key: str
    content: list[dict]
    freq: int = 1
    time: int = 0
    regular: bool = False
    answers: list[Answer] = field(default_factory=list)

    @classmethod
    def from_dict(cls, key: str, data: dict) -> Question:
        """从词库 JSON 还原一个问题。"""
        answers = [Answer.from_dict(item) for item in data.get("answers") or []]
        content = normalize_content(data.get("content") or [])
        if not content:
            try:
                content = normalize_content(json.loads(key))
            except Exception:
                content = []
        return cls(
            key=key,
            content=content,
            freq=int(data.get("freq", 1) or 1),
            time=int(data.get("time", 0) or 0),
            regular=bool(data.get("regular", False)),
            answers=answers,
        )

    def to_dict(self) -> dict:
        """序列化为词库 JSON 中的问题结构。"""
        return {
            "content": self.content,
            "freq": int(self.freq),
            "time": int(self.time),
            "regular": bool(self.regular),
            "answers": [answer.to_dict() for answer in self.answers],
        }

    @property
    def plain_text(self) -> str:
        """问题片段的纯文本。"""
        return content_plain_text(self.content)


class WordStock:
    """单个词库文件（分群 / 合并 / 标签）。"""

    VERSION = 1

    def __init__(
        self, path: Path, questions: dict[str, Question] | None = None
    ) -> None:
        self.path = Path(path)
        self.questions: dict[str, Question] = questions or {}
        self.dirty = False

    @classmethod
    def load(cls, path: Path) -> WordStock:
        """从 JSON 文件加载词库。"""
        path = Path(path)
        if not path.exists():
            return cls(path)
        try:
            with path.open("r", encoding="utf-8") as handle:
                raw = json.load(handle)
        except (OSError, json.JSONDecodeError):
            return cls(path)
        questions: dict[str, Question] = {}
        for key, data in (raw.get("questions") or {}).items():
            try:
                questions[key] = Question.from_dict(key, data)
            except Exception:
                continue
        return cls(path, questions)

    def save(self) -> None:
        """原子写回词库 JSON。"""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": self.VERSION,
            "questions": {key: item.to_dict() for key, item in self.questions.items()},
        }
        fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=1)
            os.replace(tmp, self.path)
        finally:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass
        self.dirty = False

    def record_question(self, content: list[dict], regular: bool = False) -> Question:
        """记录一次问题出现（已存在则累加频次）。"""
        content = normalize_content(content)
        key = content_key(content)
        question = self.questions.get(key)
        if question is None:
            question = Question(
                key=key, content=content, freq=1, time=now_ts(), regular=regular
            )
            self.questions[key] = question
        else:
            question.freq += 1
            question.time = now_ts()
            question.regular = question.regular or regular
        self.dirty = True
        return question

    def record_answer(self, question_key: str, content: list[dict]) -> bool:
        """记录问题的答案（已存在则累加权重）。"""
        question = self.questions.get(question_key)
        if question is None:
            return False
        content = normalize_content(content)
        key = content_key(content)
        for answer in question.answers:
            if answer.key == key:
                answer.same += 1
                answer.time = now_ts()
                self.dirty = True
                return True
        question.answers.append(Answer(content=content, same=1, time=now_ts()))
        self.dirty = True
        return True

    def get(self, key: str) -> Question | None:
        """按键取问题。"""
        return self.questions.get(key)

    def remove_question(self, key: str) -> bool:
        """删除整条问题。"""
        if key in self.questions:
            del self.questions[key]
            self.dirty = True
            return True
        return False

    def remove_answer(self, question_key: str, answer: Answer) -> bool:
        """删除指定答案，答案为空时同时删除问题。"""
        question = self.questions.get(question_key)
        if question is None:
            return False
        for index, item in enumerate(question.answers):
            if item.key == answer.key:
                del question.answers[index]
                self.dirty = True
                if not question.answers:
                    del self.questions[question_key]
                return True
        return False

    def answer_count(self) -> int:
        """统计答案总数。"""
        return sum(len(question.answers) for question in self.questions.values())

    def stats(self) -> tuple[int, int]:
        """返回（问题数，答案数）。"""
        return len(self.questions), self.answer_count()

    def iter_questions(self):
        """遍历所有问题。"""
        return self.questions.items()
