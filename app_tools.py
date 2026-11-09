"""Application selection and tool routing for the local assistant."""

from __future__ import annotations

import importlib
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import audit_log

APP_CATALOG_PATH = Path(__file__).resolve().parent / "app_registry.json"

_catalog_data = json.loads(APP_CATALOG_PATH.read_text(encoding="utf-8"))
if (
    not isinstance(_catalog_data, dict)
    or _catalog_data.get("version") != 1
    or not isinstance(_catalog_data.get("applications"), list)
    or not _catalog_data["applications"]
):
    raise ValueError("app_registry.json must be a version 1 object with applications")

APP_CATALOG: list[dict[str, str]] = []
APP_TOOLSETS: dict[str, Any] = {}
for _application in _catalog_data["applications"]:
    if not isinstance(_application, dict):
        raise ValueError("app_registry.json applications must be objects")
    _app_id = _application.get("id")
    _app_name = _application.get("name")
    _app_purpose = _application.get("purpose")
    _module_name = _application.get("tool_module")
    if (
        not isinstance(_app_id, str)
        or not _app_id.strip()
        or not isinstance(_app_name, str)
        or not _app_name.strip()
        or not isinstance(_app_purpose, str)
        or not _app_purpose.strip()
        or not isinstance(_module_name, str)
        or not _module_name.strip()
        or _app_id in {app["id"] for app in APP_CATALOG}
    ):
        raise ValueError("app_registry.json contains an invalid or duplicate application")
    _toolset = importlib.import_module(_module_name)
    if (
        not isinstance(getattr(_toolset, "TOOLS", None), list)
        or not isinstance(getattr(_toolset, "HANDLERS", None), dict)
        or not callable(getattr(_toolset, "dispatch", None))
    ):
        raise ValueError(
            f"application {_app_id!r} tool module must define TOOLS, HANDLERS, and dispatch"
        )
    APP_TOOLSETS[_app_id] = _toolset
    APP_CATALOG.append(
        {"id": _app_id, "name": _app_name.strip(), "purpose": _app_purpose.strip()}
    )


_app_ids = [app["id"] for app in APP_CATALOG]
_app_selection_tool = {
    "type": "function",
    "function": {
        "name": "select_app",
        "description": (
            "Choose the application for the user's current task from the app "
            "catalog. Keep it selected for follow-up turns until the user asks "
            "to switch apps. If a task clearly requires multiple apps, ask the "
            "user to split it into separate requests."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "app_id": {
                    "type": "string",
                    "enum": _app_ids,
                    "description": "ID of the one application to use for this request.",
                }
            },
            "required": ["app_id"],
        },
    },
}


@dataclass
class ToolContext:
    selected_app_id: str | None = None


@dataclass(frozen=True)
class ToolRegistration:
    definition: dict[str, Any]
    handler: Callable[[dict[str, Any], ToolContext], str]
    is_available: Callable[[ToolContext], bool]
    app_id: str | None = None


def _arguments_dict(value: Any) -> dict[str, Any]:
    arguments = json.loads(value) if isinstance(value, str) else value
    if not isinstance(arguments, dict):
        raise ValueError("tool arguments must be a JSON object")
    return arguments


def _select_app(args: dict[str, Any], context: ToolContext) -> str:
    app_id = args.get("app_id")
    if not isinstance(app_id, str) or app_id not in APP_TOOLSETS:
        raise ValueError("app_id must name an application in the catalog")
    context.selected_app_id = app_id
    application = next(app for app in APP_CATALOG if app["id"] == app_id)
    return (
        f"Selected {application['name']}: {application['purpose']} "
        "Use this application's tools for follow-up turns until the user "
        "selects another app."
    )


def _dispatch_app_tool(
    app_id: str, tool_name: str, args: dict[str, Any], _context: ToolContext
) -> str:
    return APP_TOOLSETS[app_id].dispatch(tool_name, args)


def _build_tool_registry() -> dict[str, ToolRegistration]:
    registrations = [
        ToolRegistration(
            definition=_app_selection_tool,
            handler=_select_app,
            is_available=lambda _context: True,
            app_id="app_router",
        )
    ]

    for app_id, toolset in APP_TOOLSETS.items():
        for definition in toolset.TOOLS:
            tool_name = definition["function"]["name"]
            registrations.append(
                ToolRegistration(
                    definition=definition,
                    handler=lambda args, context, app_id=app_id, tool_name=tool_name: (
                        _dispatch_app_tool(app_id, tool_name, args, context)
                    ),
                    is_available=lambda context, app_id=app_id: (
                        context.selected_app_id == app_id
                    ),
                    app_id=app_id,
                )
            )

    registry = {}
    for registration in registrations:
        tool_name = registration.definition["function"]["name"]
        if tool_name in registry:
            raise ValueError(f"duplicate tool name registered: {tool_name!r}")
        registry[tool_name] = registration
    return registry


TOOL_REGISTRY = _build_tool_registry()


def get_available_tools(context: ToolContext) -> list[dict[str, Any]]:
    """Return tool definitions available for the current routing context."""
    return [
        registration.definition
        for registration in TOOL_REGISTRY.values()
        if registration.is_available(context)
    ]


def dispatch_tool(
    name: str,
    raw_arguments: Any,
    context: ToolContext,
    available_tool_names: set[str] | None = None,
) -> str:
    """Dispatch one registered tool if it is enabled in the current context."""
    started_at = time.perf_counter()
    registration = TOOL_REGISTRY.get(name)
    if registration is None:
        result = f"error: unknown tool {name!r}"
        app_id = "app_router"
        status = "error"
    elif (
        not registration.is_available(context)
        or (
            available_tool_names is not None
            and name not in available_tool_names
        )
    ):
        result = f"error: tool {name!r} is not available in the current context"
        app_id = registration.app_id or "app_router"
        status = "error"
    else:
        app_id = registration.app_id or "app_router"
        try:
            arguments = _arguments_dict(raw_arguments)
            result = registration.handler(arguments, context)
        except (json.JSONDecodeError, OSError, ValueError) as error:
            result = f"error: {name} failed: {error}"
        except Exception as error:
            audit_log.log_tool_call(
                app_id=app_id,
                tool_name=name,
                arguments=raw_arguments,
                result=f"error: {name} failed: {error}",
                status="error",
                duration_ms=(time.perf_counter() - started_at) * 1000,
            )
            raise
        status = "error" if result.startswith("error:") else "success"

    audit_log.log_tool_call(
        app_id=app_id,
        tool_name=name,
        arguments=raw_arguments,
        result=result,
        status=status,
        duration_ms=(time.perf_counter() - started_at) * 1000,
    )
    return result
