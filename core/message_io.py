"""消息与内部内容片段之间的转换（含图片落盘缓存）。"""

from __future__ import annotations

import asyncio
import base64
import binascii
from collections.abc import Iterable
from pathlib import Path
from urllib.parse import unquote, urlparse

import astrbot.api.message_components as Comp
from astrbot.api import logger
from astrbot.core.message.message_event_result import MessageChain

from .models import (
    IMAGE,
    TEXT,
    image_segment,
    normalize_content,
    text_segment,
)

# AstrBot 组件类名 -> 配置里使用的消息类型名
_TYPE_ALIASES = {
    "Reply": "Quote",
    "Record": "Voice",
    "AtAll": "AtAll",
}

_EXT_BY_MAGIC = (
    (b"\xff\xd8\xff", ".jpg"),
    (b"\x89PNG\r\n\x1a\n", ".png"),
    (b"GIF8", ".gif"),
    (b"BM", ".bmp"),
)

MAX_IMAGE_BYTES = 8 * 1024 * 1024
DOWNLOAD_TIMEOUT = 15


def _guess_extension(data: bytes) -> str:
    for magic, ext in _EXT_BY_MAGIC:
        if data.startswith(magic):
            return ext
    if len(data) > 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return ".jpg"


class ImageStore:
    """把图片片段保存到 data 目录，并用 sha1 去重。"""

    def __init__(self, directory: Path) -> None:
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)

    def path_for(self, relative: str | None, digest: str | None) -> Path | None:
        """根据相对路径或摘要定位已保存的图片。"""
        if relative:
            candidate = self.directory.parent / relative
            if candidate.exists():
                return candidate
        if digest:
            for path in self.directory.glob(f"{digest}.*"):
                return path
        return None

    async def save_component(self, component) -> dict | None:
        """把图片组件落盘并返回图片片段。"""
        try:
            data = await self._read_bytes(component)
        except Exception as exc:  # noqa: BLE001 - 图片失败不影响文字记录
            logger.debug("ChatLearning: 读取图片失败: %s", exc)
            return None
        if not data:
            return None
        if len(data) > MAX_IMAGE_BYTES:
            logger.debug("ChatLearning: 图片过大，已跳过")
            return None
        from .utils import sha1_hex

        digest = sha1_hex(data)
        extension = _guess_extension(data)
        filename = f"{digest}{extension}"
        target = self.directory / filename
        if not target.exists():
            await asyncio.to_thread(target.write_bytes, data)
        return image_segment(digest, f"images/{filename}")

    async def _read_bytes(self, component) -> bytes | None:
        local_path = getattr(component, "path", "") or ""
        file_value = getattr(component, "file", "") or ""
        url = getattr(component, "url", "") or ""

        if local_path and Path(local_path).exists():
            return await asyncio.to_thread(Path(local_path).read_bytes)

        if isinstance(file_value, str):
            if file_value.startswith("file://"):
                path = Path(unquote(urlparse(file_value).path.lstrip("/")))
                if path.exists():
                    return await asyncio.to_thread(path.read_bytes)
            elif file_value.startswith("base64://"):
                try:
                    return base64.b64decode(file_value[len("base64://") :])
                except (binascii.Error, ValueError):
                    return None
            elif file_value.startswith(("http://", "https://")):
                return await self._download(file_value)
            elif file_value and Path(file_value).exists():
                return await asyncio.to_thread(Path(file_value).read_bytes)

        if url:
            return await self._download(url)

        # 最后尝试让组件自行解析成文件
        converter = getattr(component, "convert_to_file_path", None)
        if converter is not None:
            try:
                path = Path(await converter())
                if path.exists():
                    return await asyncio.to_thread(path.read_bytes)
            except Exception:  # noqa: BLE001
                return None
        return None

    async def _download(self, url: str) -> bytes | None:
        import aiohttp

        try:
            timeout = aiohttp.ClientTimeout(total=DOWNLOAD_TIMEOUT)
            headers = {"User-Agent": "Mozilla/5.0 (ChatLearning AstrBot Plugin)"}
            async with aiohttp.ClientSession(
                timeout=timeout, headers=headers
            ) as session:
                async with session.get(url) as response:
                    if response.status != 200:
                        return None
                    content_length = response.content_length or 0
                    if content_length > MAX_IMAGE_BYTES:
                        return None
                    return await response.read()
        except Exception as exc:  # noqa: BLE001
            logger.debug("ChatLearning: 下载图片失败 %s: %s", url, exc)
            return None


class MessageCodec:
    """负责 event <-> 内容片段列表。"""

    def __init__(self, images: ImageStore) -> None:
        self.images = images

    async def content_from_event(self, event) -> tuple[list[dict], set[str]]:
        """把事件消息转换成（内容片段，原始类型集合）。"""
        segments: list[dict] = []
        raw_types: set[str] = set()
        for component in event.get_messages() or []:
            class_name = type(component).__name__
            raw_types.add(_TYPE_ALIASES.get(class_name, class_name))
            if isinstance(component, Comp.Plain):
                segments.append(text_segment(component.text))
            elif isinstance(component, Comp.Image):
                segment = await self.images.save_component(component)
                if segment is not None:
                    segments.append(segment)
        return normalize_content(segments), raw_types

    def build_chain(self, content: Iterable[dict]) -> MessageChain:
        """把内容片段重建为可发送的消息链。"""
        components = []
        for segment in content:
            kind = segment.get("t")
            if kind == TEXT:
                components.append(Comp.Plain(str(segment.get("s") or "")))
            elif kind == IMAGE:
                path = self.images.path_for(segment.get("p"), segment.get("h"))
                if path is None:
                    components.append(Comp.Plain("[图片]"))
                else:
                    components.append(Comp.Image.fromFileSystem(str(path)))
        return MessageChain(components)

    @staticmethod
    def detect_at_bot(event) -> bool:
        """判断消息是否 @ 了机器人。"""
        self_id = str(event.get_self_id() or "")
        if not self_id:
            return False
        for component in event.get_messages() or []:
            if isinstance(component, Comp.At) and str(component.qq) == self_id:
                return True
        return False

    @staticmethod
    def detect_at_all(event) -> bool:
        """判断消息是否 @ 全体成员。"""
        for component in event.get_messages() or []:
            if isinstance(component, Comp.AtAll):
                return True
            if isinstance(component, Comp.At) and str(component.qq) == "all":
                return True
        return False

    @staticmethod
    def message_timestamp(event, fallback: int) -> int:
        """读取消息时间戳，缺失时使用回退值。"""
        raw = getattr(event.message_obj, "raw_message", None)
        try:
            value = (
                raw.get("time") if hasattr(raw, "get") else getattr(raw, "time", None)
            )
            if value is not None:
                return int(value)
        except Exception:  # noqa: BLE001
            pass
        return fallback
