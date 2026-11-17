"""问题相似度匹配：分词 + 词频余弦相似度。

优先使用 jieba 分词（与原版一致）；未安装时退化为中文字符二元组，
保证插件在没有第三方依赖时也能正常工作。
"""

from __future__ import annotations

import math
import re
from collections import Counter

_PUNCTUATION = set(
    "。，、？?！!；：\"'“”‘’「」『』（）()[]〔〕【】——…-～·《》〈〉﹏﹏___.,;:"
)

_backend = "fallback"
_cut = None

try:  # pragma: no cover - 取决于运行环境
    import jieba  # type: ignore

    jieba.setLogLevel(20)

    def _jieba_cut(text: str) -> list[str]:
        return list(jieba.cut(text, cut_all=False))

    _cut = _jieba_cut
    _backend = "jieba"
except Exception:  # noqa: BLE001 - 没有 jieba 时退化
    _cut = None


def backend_name() -> str:
    return _backend


def _fallback_tokens(text: str) -> list[str]:
    tokens: list[str] = []
    for block in re.findall(r"[A-Za-z0-9]+|[\u4e00-\u9fff]+", text):
        if block.isascii():
            tokens.append(block.lower())
            continue
        if len(block) == 1:
            tokens.append(block)
        else:
            tokens.extend(block[index : index + 2] for index in range(len(block) - 1))
    return tokens


def tokenize(text: str) -> list[str]:
    text = (text or "").strip()
    if not text:
        return []
    raw = _cut(text) if _cut is not None else _fallback_tokens(text)
    tokens: list[str] = []
    for word in raw:
        word = word.strip()
        if not word:
            continue
        if word in _PUNCTUATION or all(ch in _PUNCTUATION or ch.isspace() for ch in word):
            continue
        tokens.append(word)
    return tokens


def cosine_similarity(left: str, right: str) -> float:
    left_tokens = tokenize(left)
    right_tokens = tokenize(right)
    if not left_tokens or not right_tokens:
        return 0.0
    left_counter = Counter(left_tokens)
    right_counter = Counter(right_tokens)
    dot = sum(count * right_counter.get(word, 0) for word, count in left_counter.items())
    if dot == 0:
        return 0.0
    left_norm = math.sqrt(sum(count * count for count in left_counter.values()))
    right_norm = math.sqrt(sum(count * count for count in right_counter.values()))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return dot / (left_norm * right_norm)
