"""可视化视图 Web API 层的桩模块测试：不启动 AstrBot，python tests/test_dashboard_web.py。"""

from __future__ import annotations

import logging
import sys
import tempfile
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

PLUGIN_ROOT = Path(__file__).resolve().parent.parent


class FakeQuery:
    def __init__(self, values):
        self.values = values

    def get(self, key, default=None, type=None):
        if key not in self.values or self.values[key] is None:
            return default
        value = self.values[key]
        if type is not None:
            try:
                return type(value)
            except (TypeError, ValueError):
                return default
        return value


class FakeRequest:
    def __init__(self, query=None, body=None):
        self.query = FakeQuery(query or {})
        self._body = body or {}

    async def json(self, default=None):
        return self._body


class FakeContext:
    def __init__(self):
        self.routes = []

    def register_web_api(self, route, handler, methods, desc):
        self.routes.append((route, handler, methods, desc))


def install_stubs():
    web = types.ModuleType("astrbot.api.web")
    web.request = FakeRequest()

    def json_response(data=None, *, status_code=200, headers=None):
        return {"kind": "json", "status": status_code, "data": data}

    def error_response(message, *, status_code=400, data=None, headers=None):
        return {
            "kind": "error",
            "status": status_code,
            "message": message,
            "data": data,
        }

    def file_response(path, *, filename=None, content_type=None, headers=None):
        return {
            "kind": "file",
            "path": str(path),
            "filename": filename,
            "content_type": content_type,
        }

    web.json_response = json_response
    web.error_response = error_response
    web.file_response = file_response
    web.stream_response = lambda content, **kwargs: {"kind": "stream"}
    web.PluginUploadFile = object
    sys.modules["astrbot"] = types.ModuleType("astrbot")
    sys.modules["astrbot.api"] = types.ModuleType("astrbot.api")
    sys.modules["astrbot.api"].logger = logging.getLogger("test")
    sys.modules["astrbot.api.web"] = web
    return web


PASSED = []


def check(name, condition, detail=""):
    if not condition:
        raise AssertionError(f"{name} 失败 {detail}")
    PASSED.append(name)


def build_service(tmp):
    from core.config import PluginConfig
    from core.dashboard import DashboardService
    from core.filtering import FilterStore
    from core.models import content_key, text_segment
    from core.storage import StockManager

    root = Path(tmp)
    config = PluginConfig(root / "config.json")
    stocks = StockManager(root)
    filters = FilterStore(root / "filter.json")
    stock = stocks.get("1001")
    stock.record_question([text_segment("今天天气如何")])
    stock.record_answer(
        content_key([text_segment("今天天气如何")]), [text_segment("晴")]
    )
    stock.save()
    config.set("learning", 1)
    config.set("reply", 1)
    filters.add_filter([text_segment("广告")])
    return config, stocks, filters, DashboardService(config, stocks, filters)


def main():
    web = install_stubs()
    from core.dashboard_web import register_dashboard_web_apis

    with tempfile.TemporaryDirectory() as tmp:
        config, stocks, filters, service = build_service(tmp)
        context = FakeContext()
        register_dashboard_web_apis(context, service)
        routes = {route: handler for route, handler, methods, desc in context.routes}
        check("注册：路由数量", len(context.routes) == 17)
        check(
            "注册：路由前缀",
            all(
                route.startswith("/astrbot_plugin_chat_learning/dashboard/")
                for route in routes
            ),
        )
        check("注册：方法", all(methods for _, _, methods, _ in context.routes))

        import asyncio

        async def call(route, query=None, body=None):
            web.request.query = FakeQuery(query or {})
            web.request._body = body or {}
            return await routes[route]()

        overview = asyncio.run(call("/astrbot_plugin_chat_learning/dashboard/overview"))
        check(
            "接口：状态",
            overview["kind"] == "json" and overview["data"]["totals"]["questions"] == 1,
        )
        wordstock = asyncio.run(
            call(
                "/astrbot_plugin_chat_learning/dashboard/wordstock",
                {"group": "1001", "q": "天气"},
            )
        )
        check("接口：词库", wordstock["data"]["total"] == 1)
        saved = asyncio.run(
            call(
                "/astrbot_plugin_chat_learning/dashboard/config/save",
                body={"values": {"replychance": 25}},
            )
        )
        check(
            "接口：保存配置",
            saved["kind"] == "json" and config.as_int("replychance") == 25,
        )
        filters_payload = asyncio.run(
            call("/astrbot_plugin_chat_learning/dashboard/filters")
        )
        check("接口：过滤", filters_payload["data"]["filter"][0]["preview"] == "广告")
        added = asyncio.run(
            call(
                "/astrbot_plugin_chat_learning/dashboard/filters/add",
                body={"kind": "filter", "text": "二维码", "accurate": True},
            )
        )
        check("接口：新增过滤词", added["kind"] == "json")
        bad = asyncio.run(
            call(
                "/astrbot_plugin_chat_learning/dashboard/wordstock/delete",
                body={"group": "../x", "question_key": "x"},
            )
        )
        check("接口：业务错误转 400", bad["kind"] == "error" and bad["status"] == 400)
        groups = asyncio.run(call("/astrbot_plugin_chat_learning/dashboard/groups"))
        check(
            "接口：群列表",
            groups["kind"] == "json"
            and groups["data"]["groups"][0]["group"] == "1001",
        )
        group_saved = asyncio.run(
            call(
                "/astrbot_plugin_chat_learning/dashboard/groups/save",
                body={"group": "1002", "learning": True, "tags": ["测试"]},
            )
        )
        check(
            "接口：保存群配置",
            group_saved["kind"] == "json" and config.has_group("learning", "1002"),
        )
        group_removed = asyncio.run(
            call(
                "/astrbot_plugin_chat_learning/dashboard/groups/remove",
                body={"group": "1002", "delete_wordstock": True},
            )
        )
        check(
            "接口：移除群配置",
            group_removed["kind"] == "json"
            and not config.has_group("learning", "1002"),
        )
        bad_group = asyncio.run(
            call(
                "/astrbot_plugin_chat_learning/dashboard/groups/save",
                body={"group": "abc", "learning": True},
            )
        )
        check("接口：群号校验", bad_group["kind"] == "error")
        backup = asyncio.run(call("/astrbot_plugin_chat_learning/dashboard/backup"))
        check(
            "接口：下载备份",
            backup["kind"] == "file" and Path(backup["path"]).exists(),
        )
        payload = service.backup_payload()
        restored = asyncio.run(
            call(
                "/astrbot_plugin_chat_learning/dashboard/restore",
                body={
                    "backup": payload,
                    "restore": {"config": True, "wordstock": True},
                },
            )
        )
        check(
            "接口：恢复",
            restored["kind"] == "json" and "wordstock" in restored["data"]["restored"],
        )
        merged = asyncio.run(
            call("/astrbot_plugin_chat_learning/dashboard/maintenance/merge")
        )
        check("接口：合并", merged["kind"] == "json" and merged["data"]["logs"])
        missing = asyncio.run(
            call(
                "/astrbot_plugin_chat_learning/dashboard/maintenance/autodelete",
                body={},
            )
        )
        check("接口：清理需确认", missing["kind"] == "error")

    print(f"全部通过：{len(PASSED)} 项")
    for name in PASSED:
        print("  ✓", name)


if __name__ == "__main__":
    main()
