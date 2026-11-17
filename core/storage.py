"""数据目录、JSON 读写与词库缓存。"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from .models import WordStock


def read_json(path: Path, default: Any) -> Any:
    """读取 JSON 文件；不存在或损坏时返回默认值。"""
    path = Path(path)
    if not path.exists():
        return default
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError):
        return default


def write_json(path: Path, data: Any) -> None:
    """原子写入 JSON 文件（临时文件 + replace）。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


class StockManager:
    """词库文件管理：按名字缓存 WordStock，写入串行化。"""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.words_dir = self.root / "wordstock"
        self.images_dir = self.root / "images"
        self.words_dir.mkdir(parents=True, exist_ok=True)
        self.images_dir.mkdir(parents=True, exist_ok=True)
        self._stocks: dict[str, WordStock] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def path_for(self, name: str) -> Path:
        """返回词库名对应的 JSON 路径。"""
        safe = str(name).replace("/", "_").replace("\\", "_").strip()
        if not safe:
            raise ValueError("词库名不能为空")
        return self.words_dir / f"{safe}.json"

    def lock_for(self, name: str) -> asyncio.Lock:
        """返回某个词库的写入锁。"""
        if name not in self._locks:
            self._locks[name] = asyncio.Lock()
        return self._locks[name]

    def get(self, name: str) -> WordStock:
        """读取并缓存词库（不存在时返回空词库）。"""
        if name not in self._stocks:
            self._stocks[name] = WordStock.load(self.path_for(name))
        return self._stocks[name]

    def invalidate(self, name: str) -> None:
        """丢弃缓存，下次访问重新加载。"""
        self._stocks.pop(name, None)

    def save(self, name: str) -> None:
        """把指定词库写回磁盘。"""
        self.get(name).save()

    def flush_dirty(self) -> int:
        """把所有有改动但未落盘的词库写入磁盘。"""
        """把所有有改动但尚未落盘的词库写入磁盘。"""
        saved = 0
        for name, stock in list(self._stocks.items()):
            if stock.dirty:
                stock.save()
                saved += 1
        return saved

    def exists(self, name: str) -> bool:
        """判断词库文件是否存在。"""
        return self.path_for(name).exists()

    def names(self) -> list[str]:
        """列出所有词库名（不含扩展名）。"""
        if not self.words_dir.exists():
            return []
        return sorted(path.stem for path in self.words_dir.glob("*.json"))

    def delete(self, name: str) -> None:
        """删除词库文件并清理缓存。"""
        self._stocks.pop(name, None)
        path = self.path_for(name)
        if path.exists():
            path.unlink()


MERGE_STOCK = "Merge"
