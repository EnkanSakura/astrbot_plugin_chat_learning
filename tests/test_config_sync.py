"""插件配置与运行时配置双向同步测试。

覆盖首次接入同步、插件配置热重载、面板保存与回写，确保两边始终一致。
运行：python tests/test_config_sync.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.config import PluginConfig
from core.storage import read_json, write_json

PASSED: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if not condition:
        raise AssertionError(f"{name} 失败 {detail}")
    PASSED.append(name)


class FakeAstrBotConfig(dict):
    """模拟 AstrBotConfig：保留 default_config 并提供 save_config。"""

    def __init__(self, values, defaults, config_path):
        super().__init__(values)
        self.default_config = dict(defaults)
        self.config_path = str(config_path)
        self.saved = 0

    def save_config(self):
        self.saved += 1


def make_sync(fake):
    def sync(data):
        for key in list(fake.keys()):
            if key not in data:
                continue
            current = fake[key]
            if isinstance(current, bool):
                fake[key] = bool(data[key])
            elif isinstance(current, int):
                fake[key] = int(data[key])
            elif isinstance(current, float):
                fake[key] = float(data[key])
            else:
                fake[key] = data[key]
        fake.save_config()

    return sync


def mark_synced(root: Path) -> None:
    (root / ".config_sync").write_text("1", encoding="utf-8")


def make_fake(root: Path):
    return FakeAstrBotConfig(
        {"learning": False, "replychance": 50},
        {"learning": False, "replychance": 50},
        root / "plugin_config.json",
    )


def case_legacy_runtime_wins(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    runtime_path = root / "config.json"
    write_json(
        runtime_path,
        {"learning": 1, "replychance": 30, "tag": {"100": ["摸鱼"]}},
    )
    fake = make_fake(root)
    config = PluginConfig(
        runtime_path,
        seed={"learning": False, "replychance": 50},
        sync=make_sync(fake),
    )
    check("同步：首次升级保留运行时值", config.as_bool("learning") is True)
    check("同步：首次升级保留概率", config.as_int("replychance") == 30)
    check("同步：运行时私有键保留", config.get("tag") == {"100": ["摸鱼"]})
    check(
        "同步：插件配置写回运行时值",
        bool(fake["learning"]) is True and fake["replychance"] == 30,
    )
    check("同步：插件配置已保存", fake.saved >= 1)
    check("同步：同步标记已创建", (root / ".config_sync").exists())


def case_synced_plugin_config_wins(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    mark_synced(root)
    runtime_path = root / "config.json"
    write_json(runtime_path, {"learning": 0, "replychance": 30})
    fake = FakeAstrBotConfig(
        {"learning": True, "replychance": 80},
        {"learning": False, "replychance": 50},
        root / "plugin_config.json",
    )
    config = PluginConfig(
        runtime_path,
        seed={"learning": True, "replychance": 80},
        sync=make_sync(fake),
    )
    check("同步：同步后插件值优先", config.as_bool("learning") is True)
    check("同步：同步后插件概率优先", config.as_int("replychance") == 80)
    check("同步：运行时已更新", read_json(runtime_path, {})["replychance"] == 80)

    config.set("replychance", 70)
    config.save()
    check("同步：面板修改回写插件配置", fake["replychance"] == 70)


def case_plugin_non_default(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    mark_synced(root)
    runtime_path = root / "config.json"
    write_json(runtime_path, {"learning": 0, "replychance": 50})
    fake = FakeAstrBotConfig(
        {"learning": True, "replychance": 80},
        {"learning": False, "replychance": 50},
        root / "plugin_config.json",
    )
    config = PluginConfig(
        runtime_path,
        seed={"learning": True, "replychance": 80},
        sync=make_sync(fake),
    )
    check("同步：插件非默认值覆盖运行时", config.as_bool("learning") is True)
    check("同步：插件非默认概率覆盖运行时", config.as_int("replychance") == 80)


def case_plugin_default_resets_runtime(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    mark_synced(root)
    runtime_path = root / "config.json"
    write_json(runtime_path, {"learning": 1, "replychance": 30})
    fake = FakeAstrBotConfig(
        {"learning": False, "replychance": 50},
        {"learning": False, "replychance": 50},
        root / "plugin_config.json",
    )
    config = PluginConfig(
        runtime_path,
        seed={"learning": False, "replychance": 50},
        sync=make_sync(fake),
    )
    check("同步：插件默认值可重置运行时", config.as_bool("learning") is False)
    check("同步：插件默认概率可重置", config.as_int("replychance") == 50)


def case_hot_reload_reads_plugin_config(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    mark_synced(root)
    runtime_path = root / "config.json"
    fake = FakeAstrBotConfig(
        {"learning": True, "replychance": 90},
        {"learning": False, "replychance": 50},
        root / "plugin_config.json",
    )
    config = PluginConfig(
        runtime_path,
        seed={"learning": True, "replychance": 90},
        sync=make_sync(fake),
    )
    check("同步：热重载读取插件新值", config.as_int("replychance") == 90)
    check("同步：热重载后插件配置保持", fake["replychance"] == 90)


def case_runtime_extra_keys_survive(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    mark_synced(root)
    runtime_path = root / "config.json"
    write_json(runtime_path, {"tag": {"100": ["游戏"]}, "subadmin": {"100": ["1"]}})
    fake = make_fake(root)
    config = PluginConfig(
        runtime_path,
        seed={"learning": False, "replychance": 50},
        sync=make_sync(fake),
    )
    check("同步：标签配置保留", config.get("tag") == {"100": ["游戏"]})
    check("同步：子管理员配置保留", config.get("subadmin") == {"100": ["1"]})
    check("同步：插件配置不写入私有键", "tag" not in fake)


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        case_legacy_runtime_wins(Path(tmp) / "legacy")
    with tempfile.TemporaryDirectory() as tmp:
        case_synced_plugin_config_wins(Path(tmp) / "synced")
    with tempfile.TemporaryDirectory() as tmp:
        case_plugin_non_default(Path(tmp) / "nondefault")
    with tempfile.TemporaryDirectory() as tmp:
        case_plugin_default_resets_runtime(Path(tmp) / "default")
    with tempfile.TemporaryDirectory() as tmp:
        case_hot_reload_reads_plugin_config(Path(tmp) / "hot")
    with tempfile.TemporaryDirectory() as tmp:
        case_runtime_extra_keys_survive(Path(tmp) / "extra")
    print(f"全部通过：{len(PASSED)} 项")
    for name in PASSED:
        print("  ✓", name)


if __name__ == "__main__":
    main()
