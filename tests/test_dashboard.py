"""可视化视图后端服务测试：不依赖 astrbot，直接运行 python tests/test_dashboard.py。"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.config import PluginConfig
from core.dashboard import DashboardError, DashboardService
from core.filtering import FilterStore
from core.models import content_key, text_segment
from core.storage import StockManager

PASSED: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if not condition:
        raise AssertionError(f"{name} 失败 {detail}")
    PASSED.append(name)


def text(value: str) -> list[dict]:
    return [text_segment(value)]


def expect_error(name: str, func, *args, contains: str = "") -> None:
    try:
        func(*args)
    except DashboardError as exc:
        if contains and contains not in str(exc):
            raise AssertionError(f"{name} 错误信息不符：{exc}") from exc
        PASSED.append(name)
        return
    raise AssertionError(f"{name} 未抛出 DashboardError")


def setup(tmp: str):
    root = Path(tmp)
    config = PluginConfig(root / "config.json")
    stocks = StockManager(root)
    filters = FilterStore(root / "filter.json")
    first = stocks.get("1001")
    first.record_question(text("今天天气如何"))
    first.record_answer(content_key(text("今天天气如何")), text("晴"))
    first.record_answer(content_key(text("今天天气如何")), text("多云"))
    first.record_question(text("^正则.*"), regular=True)
    first.record_answer(content_key(text("^正则.*")), text("匹配"))
    first.save()
    second = stocks.get("1002")
    second.record_question(text("中午吃什么"))
    second.record_answer(content_key(text("中午吃什么")), text("面条"))
    second.save()
    config.set("learning", 1)
    config.set("reply", 1)
    config.add_groups("learning", ["1001", "1002"])
    config.add_groups("reply", ["1001"])
    config.set("tag", {"1002": ["游戏"]})
    filters.add_filter(text("广告"), accurate=False)
    filters.add_sensitive(text("敏感"))
    filters.add_black(["42"])
    return config, stocks, filters, DashboardService(config, stocks, filters)


def test_overview(config, stocks, service):
    payload = service.overview()
    check("状态：总数", payload["totals"]["questions"] == 3)
    check("状态：答案总数", payload["totals"]["answers"] == 4)
    check("状态：群数量", payload["totals"]["groups"] == 2)
    check("状态：开关", payload["status"]["learning"] is True)
    check("状态：记录群", payload["status"]["learning_groups"] == ["1001", "1002"])
    check("状态：标签", payload["status"]["tags"] == {"1002": ["游戏"]})
    check("状态：过滤计数", payload["filters"]["filter"] == 2)
    check("状态：黑名单计数", payload["filters"]["blacklist"] == 1)
    check("状态：运行信息", "started_at" in payload["runtime"])
    check("状态：词库条目", any(item["name"] == "1001" for item in payload["stocks"]))


def test_wordstock(service):
    payload = service.wordstock({})
    check("词库：默认第一个群", payload["group"] == "1001")
    check("词库：分页信息", payload["page"] == 1 and payload["page_size"] == 20)
    check("词库：问题数", payload["total"] == 2)
    check("词库：答案键", bool(payload["items"][0]["answers"][0]["key"]))
    found = service.wordstock({"group": "1001", "q": "多云"})
    check("词库：按答案搜索", found["total"] == 1)
    check(
        "词库：搜索命中预览",
        any("多云" in answer["preview"] for answer in found["items"][0]["answers"]),
    )
    missing = service.wordstock({"group": "1001", "q": "不存在的内容"})
    check("词库：无结果", missing["total"] == 0 and missing["pages"] == 0)
    page = service.wordstock({"group": "1001", "page_size": 5, "page": 2})
    check("词库：页码回退", page["page"] == 1)


def test_wordstock_mutations(service):
    payload = service.wordstock({"group": "1001", "q": "天气"})
    item = payload["items"][0]
    answer = item["answers"][0]
    result = service.update_weight(
        {
            "group": "1001",
            "question_key": item["key"],
            "answer_key": answer["key"],
            "same": 5,
        }
    )
    check("词库：调整权重", result["same"] == 5)
    expect_error(
        "词库：权重上下限",
        service.update_weight,
        {
            "group": "1001",
            "question_key": item["key"],
            "answer_key": answer["key"],
            "same": -1,
        },
        contains="权重",
    )
    result = service.delete_entry(
        {"group": "1001", "question_key": item["key"], "answer_key": answer["key"]}
    )
    check("词库：删除单个答案", result["deleted"] == 1)
    remaining = service.wordstock({"group": "1001", "q": "天气"})
    check("词库：删除后答案减少", len(remaining["items"][0]["answers"]) == 1)
    result = service.delete_entry({"group": "1001", "question_key": item["key"]})
    check("词库：删除整个问题", result["question_removed"] is True)
    expect_error(
        "词库：重复删除",
        service.delete_entry,
        {"group": "1001", "question_key": item["key"]},
        contains="不存在",
    )
    expect_error(
        "词库：非法词库名",
        service.delete_entry,
        {"group": "../etc", "question_key": "x"},
        contains="词库名",
    )


def test_filters(service):
    payload = service.filters_payload()
    check("过滤：读取过滤词", payload["filter"][0]["preview"] == "广告")
    check("过滤：读取敏感词", payload["sensitive"][0]["preview"] == "敏感")
    check("过滤：读取黑名单", payload["blacklist"][0]["user"] == "42")
    service.add_filter({"kind": "filter", "text": "新过滤词", "accurate": True})
    check("过滤：新增过滤词", len(service.filters_payload()["filter"]) == 3)
    service.add_filter({"kind": "sensitive", "text": "新敏感词"})
    check("过滤：新增敏感词", len(service.filters_payload()["sensitive"]) == 2)
    service.add_filter({"kind": "black", "text": "77 88"})
    check("过滤：新增黑名单", len(service.filters_payload()["blacklist"]) == 3)
    service.remove_filter({"kind": "filter", "index": 1})
    check(
        "过滤：删除过滤词",
        all(item["preview"] != "敏感" for item in service.filters_payload()["filter"]),
    )
    service.remove_filter({"kind": "black", "user": "42"})
    check(
        "过滤：删除黑名单",
        all(item["user"] != "42" for item in service.filters_payload()["blacklist"]),
    )
    expect_error(
        "过滤：非法类型",
        service.add_filter,
        {"kind": "filter", "text": ""},
        contains="长度",
    )
    expect_error(
        "过滤：非法黑名单",
        service.add_filter,
        {"kind": "black", "text": "abc"},
        contains="QQ",
    )
    service.set_types({"types": ["Quote", "unknown"]})
    check("过滤：类型过滤", service.filters_payload()["types"] == ["Quote"])


def test_config(service):
    payload = service.config_payload()
    check(
        "配置：字段存在",
        any(field["key"] == "replychance" for field in payload["fields"]),
    )
    check(
        "配置：默认值",
        next(item for item in payload["fields"] if item["key"] == "sendmode")["value"]
        is False,
    )
    service.save_config(
        {
            "values": {
                "replychance": 30,
                "learning": True,
                "replywait": [1.5, 0.5],
                "botname": "小助手 助手",
            },
            "typefreq": {"Plain": 2},
        }
    )
    check("配置：保存布尔", service.config.as_bool("learning") is True)
    check("配置：保存整数", service.config.as_int("replychance") == 30)
    check("配置：保存等待", service.config.get("replywait") == [1.5, 0.5])
    check("配置：保存昵称", service.config.get("botname") == ["小助手", "助手"])
    check("配置：保存阈值", service.config.get("typefreq")["Plain"] == 2)
    expect_error(
        "配置：数值越界",
        service.save_config,
        {"values": {"replychance": 500}},
        contains="不能大于",
    )
    expect_error(
        "配置：浮点越界",
        service.save_config,
        {"values": {"cosmatching": 2}},
        contains="不能大于",
    )
    expect_error(
        "配置：等待时间",
        service.save_config,
        {"values": {"replywait": [1, 2]}},
        contains="浮动",
    )
    expect_error(
        "配置：管理员QQ",
        service.save_config,
        {"values": {"Administrator": ["abc"]}},
        contains="QQ",
    )
    expect_error(
        "配置：类型阈值",
        service.save_config,
        {"typefreq": {"Unknown": 1}},
        contains="消息类型",
    )
    check(
        "配置：失败不改值",
        service.config.as_int("replychance") == 30,
    )


def test_backup_restore(config, stocks, service):
    stock = stocks.get("1001")
    stock.record_question(text("备份专用问题"))
    stock.record_answer(content_key(text("备份专用问题")), text("备份答案"))
    stock.save()
    backup = service.backup_payload()
    check("备份：格式", backup["format"].endswith("backup"))
    check("备份：包含词库", "1001" in backup["wordstock"])
    path = service.write_backup_file()
    check("备份：写入文件", path.exists() and path.parent.name == "backups")
    service.delete_entry(
        {
            "group": "1001",
            "question_key": content_key(text("备份专用问题")),
            "answer_key": content_key(text("备份答案")),
        }
    )
    service.config.set("replychance", 90)
    result = service.restore_backup(
        {
            "backup": backup,
            "restore": {
                "config": True,
                "filters": True,
                "wordstock": True,
                "wordstock_mode": "merge",
            },
        }
    )
    check("恢复：返回结果", "wordstock" in result["restored"])
    check("恢复：自动备份", bool(result["pre_backup"]))
    check("恢复：配置回滚", service.config.as_int("replychance") == 30)
    restored = service.wordstock({"group": "1001", "q": "备份专用问题"})
    check("恢复：问题回来", restored["total"] == 1)
    answers = restored["items"][0]["answers"]
    check("恢复：答案合并", any(item["preview"] == "备份答案" for item in answers))
    replace = service.restore_backup(
        {"backup": backup, "restore": {"wordstock": True, "wordstock_mode": "replace"}}
    )
    check("恢复：覆盖模式", "wordstock" in replace["restored"])
    expect_error(
        "恢复：格式错误",
        service.restore_backup,
        {"backup": {"format": "bad"}, "restore": {"config": True}},
        contains="格式",
    )
    expect_error(
        "恢复：未选内容",
        service.restore_backup,
        {"backup": backup, "restore": {}},
        contains="至少",
    )


def test_maintenance(service):
    expect_error(
        "维护：清理需确认",
        service.autodelete_now,
        {},
        contains="确认",
    )
    logs = service.autodelete_now({"confirm": True})
    check("维护：清理返回日志", isinstance(logs["logs"], list) and bool(logs["logs"]))
    merged = service.merge_now()
    check("维护：立即合并", bool(merged["logs"]))
    check("维护：记录合并时间", service.last_merge_at is not None)


def test_groups(config, stocks, service):
    stock = stocks.get("1001")
    stock.record_question(text("群列表专用问题"))
    stock.save()
    payload = service.groups_payload()
    names = {item["group"] for item in payload["groups"]}
    check("群列表：包含已配置群", {"1001", "1002"} <= names)
    entry = next(item for item in payload["groups"] if item["group"] == "1001")
    check("群列表：记录状态", entry["learning"] is True)
    check("群列表：词库统计", entry["has_wordstock"] and entry["questions"] >= 1)
    check("群列表：总数", payload["totals"]["groups"] == len(payload["groups"]))
    service.save_group(
        {
            "group": "1003",
            "learning": True,
            "reply": False,
            "unmerge": False,
            "tags": ["新标签"],
            "subadmins": [],
        }
    )
    check("群列表：新增记录群", config.has_group("learning", "1003"))
    check("群列表：新增标签", config.tag_dict().get("1003") == ["新标签"])
    check("群列表：标签建议", "新标签" in service.groups_payload()["tags"])
    service.save_group({"group": "1003", "reply": True})
    check(
        "群列表：局部更新保留字段",
        config.has_group("learning", "1003") and config.has_group("reply", "1003"),
    )
    overview = service.overview()
    overview_entry = next(
        (item for item in overview["stocks"] if item["name"] == "1003"), None
    )
    check(
        "状态：新增群同步到词库表",
        overview_entry is not None
        and overview_entry["kind"] == "group"
        and overview_entry["tags"] == ["新标签"],
    )
    check("状态：新增群同步记录列表", "1003" in overview["status"]["learning_groups"])
    check("状态：新增群同步回复列表", "1003" in overview["status"]["reply_groups"])
    service.save_group({"group": "1001", "tags": ["同步标签"], "reply": False})
    overview = service.overview()
    existing = next(item for item in overview["stocks"] if item["name"] == "1001")
    check("状态：已有群标签同步", existing["tags"] == ["同步标签"])
    check("状态：已有群回复同步", "1001" not in overview["status"]["reply_groups"])
    service.save_group({"group": "1003", "subadmins": ["20001", "20002"]})
    check("群列表：子管理员", config.get("subadmin").get("1003") == ["20001", "20002"])
    service.save_group(
        {
            "group": "1003",
            "tags": [],
            "subadmins": [],
            "learning": False,
            "reply": True,
        }
    )
    check("群列表：清空标签", "1003" not in config.tag_dict())
    expect_error(
        "群列表：至少保留一项",
        service.save_group,
        {
            "group": "1003",
            "learning": False,
            "reply": False,
            "unmerge": False,
            "tags": [],
            "subadmins": [],
        },
        contains="至少",
    )
    expect_error(
        "群列表：非法群号",
        service.save_group,
        {"group": "abc", "learning": True},
        contains="数字",
    )
    expect_error(
        "群列表：非法标签",
        service.save_group,
        {"group": "1003", "tags": ["a/b"]},
        contains="文件名",
    )
    expect_error(
        "群列表：非法子管理员",
        service.save_group,
        {"group": "1003", "subadmins": ["abc"]},
        contains="子管理员",
    )
    expect_error(
        "群列表：数字标签",
        service.save_group,
        {"group": "1003", "tags": ["12345"]},
        contains="纯数字",
    )
    service.save_group({"group": "1001", "tags": ["摸鱼"]})
    check("群列表：标签立即重建", stocks.path_for("摸鱼").exists())
    service.save_group({"group": "1001", "tags": []})
    check("群列表：取消标签重建", not stocks.path_for("摸鱼").exists())
    service.save_group({"group": "1004", "learning": True})
    removed = service.remove_group({"group": "1004"})
    check(
        "群列表：移除配置",
        removed["removed"] == "1004"
        and not config.has_group("learning", "1004"),
    )
    check("群列表：删除词库前存在", stocks.path_for("1002").exists())
    result = service.remove_group({"group": "1002", "delete_wordstock": True})
    check(
        "群列表：删除词库",
        result["wordstock_deleted"] and not stocks.path_for("1002").exists(),
    )
    check(
        "群列表：删除后隐藏",
        all(item["group"] != "1002" for item in service.groups_payload()["groups"]),
    )


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        config, stocks, filters, service = setup(tmp)
        test_overview(config, stocks, service)
        test_wordstock(service)
        test_wordstock_mutations(service)
        test_filters(service)
        test_config(service)
        test_backup_restore(config, stocks, service)
        test_maintenance(service)
        test_groups(config, stocks, service)
    print(f"全部通过：{len(PASSED)} 项")
    for name in PASSED:
        print("  ✓", name)


if __name__ == "__main__":
    main()
