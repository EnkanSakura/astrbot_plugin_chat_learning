"""把 DashboardService 注册为 AstrBot 插件的 Web API。

视图通过 window.AstrBotPluginView 调用这些接口：
endpoint 例如 dashboard/overview，Dashboard 会转发到 /api/v1/plugins/extensions/<插件名>/...
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from astrbot.api import logger
from astrbot.api.web import error_response, file_response, json_response, request

from .dashboard import DashboardError, DashboardService

PLUGIN_NAME = "astrbot_plugin_chat_learning"


def _as_payload(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


async def _reply(func: Any, *args: Any) -> Any:
    """执行同步业务方法并把异常转换成标准响应。"""
    try:
        return json_response(func(*args))
    except DashboardError as exc:
        return error_response(str(exc), status_code=400)
    except Exception:  # noqa: BLE001 - 视图接口失败不能影响插件
        logger.exception("ChatLearning: 可视化视图接口执行失败")
        return error_response("服务器内部错误，请查看 AstrBot 日志", status_code=500)


async def _reply_async(func: Any, *args: Any) -> Any:
    """在后台线程执行较重的业务方法（合并/清理/备份/恢复）。"""
    try:
        return json_response(await asyncio.to_thread(func, *args))
    except DashboardError as exc:
        return error_response(str(exc), status_code=400)
    except Exception:  # noqa: BLE001
        logger.exception("ChatLearning: 可视化视图接口执行失败")
        return error_response("服务器内部错误，请查看 AstrBot 日志", status_code=500)


def register_dashboard_web_apis(
    context: Any,
    service: DashboardService,
) -> None:
    """注册视图所需的全部 Web API（重复注册会替换旧路由）。"""
    prefix = "/" + PLUGIN_NAME

    async def overview() -> Any:
        return await _reply(service.overview)

    async def wordstock() -> Any:
        params = {
            "group": request.query.get("group"),
            "q": request.query.get("q"),
            "page": request.query.get("page"),
            "page_size": request.query.get("page_size"),
        }
        return await _reply(service.wordstock, params)

    async def wordstock_delete() -> Any:
        payload = _as_payload(await request.json(default={}))
        return await _reply(service.delete_entry, payload)

    async def wordstock_weight() -> Any:
        payload = _as_payload(await request.json(default={}))
        return await _reply(service.update_weight, payload)

    async def filters() -> Any:
        return await _reply(service.filters_payload)

    async def filters_add() -> Any:
        payload = _as_payload(await request.json(default={}))
        return await _reply(service.add_filter, payload)

    async def filters_remove() -> Any:
        payload = _as_payload(await request.json(default={}))
        return await _reply(service.remove_filter, payload)

    async def filters_types() -> Any:
        payload = _as_payload(await request.json(default={}))
        return await _reply(service.set_types, payload)

    async def groups() -> Any:
        return await _reply(service.groups_payload)

    async def groups_save() -> Any:
        payload = _as_payload(await request.json(default={}))
        return await _reply_async(service.save_group, payload)

    async def groups_remove() -> Any:
        payload = _as_payload(await request.json(default={}))
        return await _reply_async(service.remove_group, payload)

    async def config_get() -> Any:
        return await _reply(service.config_payload)

    async def config_save() -> Any:
        payload = _as_payload(await request.json(default={}))
        return await _reply(service.save_config, payload)

    async def backup() -> Any:
        try:
            path: Path = await asyncio.to_thread(service.write_backup_file)
        except DashboardError as exc:
            return error_response(str(exc), status_code=400)
        except Exception:  # noqa: BLE001
            logger.exception("ChatLearning: 生成备份失败")
            return error_response("生成备份失败，请查看 AstrBot 日志", status_code=500)
        return file_response(path, filename=path.name, content_type="application/json")

    async def restore() -> Any:
        payload = _as_payload(await request.json(default={}))
        return await _reply_async(service.restore_backup, payload)

    async def maintenance_merge() -> Any:
        return await _reply_async(service.merge_now)

    async def maintenance_autodelete() -> Any:
        payload = _as_payload(await request.json(default={}))
        return await _reply_async(service.autodelete_now, payload)

    routes: list[tuple[str, Any, list[str], str]] = [
        (f"{prefix}/dashboard/overview", overview, ["GET"], "ChatLearning 运行状态"),
        (f"{prefix}/dashboard/wordstock", wordstock, ["GET"], "ChatLearning 词库查询"),
        (
            f"{prefix}/dashboard/wordstock/delete",
            wordstock_delete,
            ["POST"],
            "ChatLearning 删除词库条目",
        ),
        (
            f"{prefix}/dashboard/wordstock/weight",
            wordstock_weight,
            ["POST"],
            "ChatLearning 调整答案权重",
        ),
        (f"{prefix}/dashboard/filters", filters, ["GET"], "ChatLearning 过滤规则"),
        (
            f"{prefix}/dashboard/filters/add",
            filters_add,
            ["POST"],
            "ChatLearning 添加过滤规则",
        ),
        (
            f"{prefix}/dashboard/filters/remove",
            filters_remove,
            ["POST"],
            "ChatLearning 删除过滤规则",
        ),
        (
            f"{prefix}/dashboard/filters/types",
            filters_types,
            ["POST"],
            "ChatLearning 消息类型过滤",
        ),
        (f"{prefix}/dashboard/groups", groups, ["GET"], "ChatLearning 群列表"),
        (
            f"{prefix}/dashboard/groups/save",
            groups_save,
            ["POST"],
            "ChatLearning 保存群配置",
        ),
        (
            f"{prefix}/dashboard/groups/remove",
            groups_remove,
            ["POST"],
            "ChatLearning 移除群配置",
        ),
        (f"{prefix}/dashboard/config", config_get, ["GET"], "ChatLearning 配置读取"),
        (
            f"{prefix}/dashboard/config/save",
            config_save,
            ["POST"],
            "ChatLearning 配置保存",
        ),
        (f"{prefix}/dashboard/backup", backup, ["GET"], "ChatLearning 数据备份"),
        (
            f"{prefix}/dashboard/restore",
            restore,
            ["POST"],
            "ChatLearning 数据恢复",
        ),
        (
            f"{prefix}/dashboard/maintenance/merge",
            maintenance_merge,
            ["POST"],
            "ChatLearning 立即合并词库",
        ),
        (
            f"{prefix}/dashboard/maintenance/autodelete",
            maintenance_autodelete,
            ["POST"],
            "ChatLearning 自动清理词库",
        ),
    ]
    for route, handler, methods, desc in routes:
        context.register_web_api(route, handler, methods, desc)
    logger.info("ChatLearning: 已注册 %d 个可视化视图 API", len(routes))
