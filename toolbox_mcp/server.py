"""A Model Context Protocol (MCP) server over stdio for the household
toolbox. No third-party libraries: this speaks the JSON-RPC 2.0 wire
format that MCP uses, using only the Python standard library.

How it works, plainly:
* MCP clients (Claude Code, Claude Desktop, other agents) launch this
  program and talk line-by-line JSON over stdin/stdout.
* The client sends ``initialize``, asks for ``tools/list``, and calls
  tools with ``tools/call``. This server answers each request on its
  own stdout line. Diagnostics go to stderr so they never confuse a
  connected client.
* Tool results carry both machine-readable ``structuredContent`` and a
  friendly ``text`` blurb a beginner can actually read.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any, Callable, Optional

from . import __version__
from .service import Toolbox, today
from .seed import fresh_document
from .storage import JsonStore, UnsafePathError

PROTOCOL_VERSION = "2025-06-18"
SERVER_NAME = "toolbox-lending-mcp-server"

# ------------------------------------------------------------------
# Tool catalogue: friendly descriptions double as beginner docs.
# ------------------------------------------------------------------

INVENTORY_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["list", "add", "update", "remove", "low-stock"],
            "description": "What to do: list the toolbox, add an item, "
                           "update one, remove one, or see what is low.",
        },
        "name": {"type": "string", "description": "Item name (add/update/remove lookups)."},
        "item": {"type": "string", "description": "Item name or id for update/remove."},
        "kind": {"type": "string", "enum": ["tool", "consumable"],
                 "description": "A tool stays around; a consumable gets used up."},
        "category": {"type": "string"},
        "location": {"type": "string", "description": "Where it lives, e.g. 'garage shelf'."},
        "quantity": {"type": "integer"},
        "low_at": {"type": "integer", "description": "Remind when quantity falls to this or less."},
        "condition": {"type": "string"},
        "notes": {"type": "string"},
        "search": {"type": "string", "description": "Search text for action=list."},
    },
}

LEND_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["lend", "return", "ledger"],
                   "description": "lend a tool, record its return, or just look at the ledger."},
        "item": {"type": "string", "description": "Tool name (or id) being lent or returned."},
        "borrower": {"type": "string", "description": "Who has it, e.g. 'Neighbor Sam'."},
        "due_on": {"type": "string", "description": "Suggested return date, YYYY-MM-DD."},
        "loan": {"type": "string", "description": "Loan id for return (optional)."},
        "notes": {"type": "string"},
    },
}

MAINTENANCE_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["log", "nudges", "complete"],
                   "description": "Schedule a repeating task, see what is due, or mark it done."},
        "item": {"type": "string", "description": "Tool the task belongs to (log only)."},
        "task": {"type": "string", "description": "What to do, e.g. 'Sharpen mower blade'."},
        "interval_days": {"type": "integer",
                          "description": "How often: 90 for blades, 540 for batteries."},
        "last_done": {"type": "string", "description": "YYYY-MM-DD last performed."},
        "notes": {"type": "string"},
    },
}

PROJECT_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["add", "list", "gaps", "complete"],
                   "description": "add a project, list them, get a gap list + shopping list, or finish one."},
        "name": {"type": "string"},
        "project": {"type": "string", "description": "Project name (or id) for gaps/complete."},
        "needed": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"name": {"type": "string"}, "qty": {"type": "integer"}},
            },
            "description": "Things the project needs, e.g. [{\"name\": \"Wood saw\", \"qty\": 1}].",
        },
        "notes": {"type": "string"},
    },
}

SEASONAL_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["prompt", "log", "history"],
                   "description": "get this month's tune-up ideas, log a finished one, or see history."},
        "job": {"type": "string", "description": "The seasonal task you completed (log)."},
        "month": {"type": "integer", "minimum": 1, "maximum": 12,
                  "description": "Defaults to the current month."},
        "notes": {"type": "string"},
    },
}

SUMMARY_SCHEMA = {"type": "object", "properties": {}}


def tool_catalogue() -> list[dict]:
    return [
        {
            "name": "toolbox_inventory",
            "title": "Toolbox Inventory",
            "description": (
                "Track the toolbox: hand and power tools plus consumables "
                "(drill bits, glue, screws). List, add, update, remove, "
                "and check what is running low."
            ),
            "inputSchema": INVENTORY_SCHEMA,
        },
        {
            "name": "toolbox_lend",
            "title": "Lend-a-Tool Ledger",
            "description": (
                "Who borrowed what, when it left, and when it came home. "
                "Nudges gently when something is due back or overdue."
            ),
            "inputSchema": LEND_SCHEMA,
        },
        {
            "name": "toolbox_maintenance",
            "title": "Maintenance Nudges",
            "description": (
                "Repeating chores by interval: sharpen blades (~90 days), "
                "oil and battery checks (~90-540 days). Mark tasks done and "
                "the next due date moves out automatically."
            ),
            "inputSchema": MAINTENANCE_SCHEMA,
        },
        {
            "name": "toolbox_project",
            "title": "Project Gap Lists",
            "description": (
                "Say what a project needs; the server compares it against "
                "the inventory and hands back a gap list that doubles as a "
                "shopping list."
            ),
            "inputSchema": PROJECT_SCHEMA,
        },
        {
            "name": "toolbox_seasonal",
            "title": "Seasonal Tune-Ups",
            "description": (
                "Simple month-appropriate prompts, like sharpening things "
                "in spring and bringing batteries inside before winter."
            ),
            "inputSchema": SEASONAL_SCHEMA,
        },
        {
            "name": "toolbox_summary",
            "title": "Toolbox at a Glance",
            "description": "One friendly snapshot: counts, loans out, low stock, due tasks.",
            "inputSchema": SUMMARY_SCHEMA,
        },
    ]


# ------------------------------------------------------------------
# The server
# ------------------------------------------------------------------

class McpServer:
    """JSON-RPC request router wired to toolbox actions."""

    def __init__(self, service: Toolbox, *, out: Any = None,
                 err: Any = None, save_callback: Any = None) -> None:
        self.service = service
        self.save_callback = save_callback  # persist the toolbox document
        self.out = out if out is not None else sys.stdout
        self.err = err if err is not None else sys.stderr
        self.methods: dict[str, Callable[[Any, Any], Any]] = {
            "initialize": self._initialize,
            "ping": self._ping,
            "tools/list": self._tools_list,
            "tools/call": self._tools_call,
            "shutdown": self._ping,
        }

    # -- wire helpers -------------------------------------------------

    def handle_line(self, line: str) -> Optional[str]:
        """Process one JSON-RPC message; return the reply line or None."""
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            if line.strip():
                self._log("unreadable message ignored")
            return None
        if not isinstance(message, dict) or "method" not in message:
            return None
        request_id = message.get("id")
        method = message.get("method", "")
        handler = self.methods.get(method)
        if handler is None:
            if request_id is not None:
                return self._reply(request_id, error={
                    "code": -32601, "message": f"method not found: {method}"
                })
            return None
        try:
            result = handler(message.get("params") or {}, request_id)
        except ToolkitError as exc:
            if request_id is None:
                return None
            return self._reply(request_id, error={
                "code": exc.code, "message": exc.message
            })
        except Exception as exc:  # unexpected bug: log, then fail cleanly
            self._log(f"internal error: {exc!r}")
            if request_id is None:
                return None
            return self._reply(request_id, error={
                "code": -32603, "message": "internal error",
            })
        if request_id is None:
            return None  # notification: no reply expected
        return self._reply(request_id, result=result)

    @staticmethod
    def _reply(request_id: Any, result: Any = None,
               error: Any = None) -> str:
        payload = {"jsonrpc": "2.0", "id": request_id}
        if error is not None:
            payload["error"] = error
        else:
            payload["result"] = result
        return json.dumps(payload)

    def _log(self, text: str) -> None:
        print(f"[{SERVER_NAME}] {text}", file=self.err, flush=True)

    # -- MCP methods --------------------------------------------------

    def _initialize(self, params: Any, request_id: Any) -> dict:
        client_info = (params or {}).get("clientInfo", {})
        self._log(
            f"initialized for {client_info.get('name', 'a client')}"
        )
        return {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {
                "name": SERVER_NAME,
                "version": __version__,
                "title": "Household Toolbox",
            },
            "instructions": (
                "Your neighborly toolbox assistant: inventory, lend-a-tool "
                "ledger, maintenance nudges, project gap lists, and "
                "seasonal tune-ups. Everything is stored locally as JSON."
            ),
        }

    def _ping(self, params: Any, request_id: Any) -> dict:
        return {}

    def _tools_list(self, params: Any, request_id: Any) -> dict:
        return {"tools": tool_catalogue(), "nextCursor": None}

    def _tools_call(self, params: Any, request_id: Any) -> dict:
        name = params.get("name", "")
        arguments = params.get("arguments") or {}
        try:
            structured = self.dispatch_tool(name, arguments)
        except ToolkitError:
            raise
        except (ValueError, TypeError) as exc:
            return self._tool_text_result(
                f"That needs a small fix: {exc}", structured=None
            )
        except Exception as exc:  # unexpected
            self._log(f"tool {name!r} failed: {exc!r}")
            return self._tool_text_result(
                "Something went sideways on my end. Check the server log.",
                structured=None,
            )
        if self.save_callback:
            try:
                self.save_callback()
            except Exception as exc:  # keep serving, but say something
                self._log(f"failed to save data: {exc!r}")
        return self._render_result(structured)

    # -- tool routing -------------------------------------------------

    def dispatch_tool(self, name: str, args: dict) -> dict:
        service = self.service
        if name == "toolbox_inventory":
            return self._call_inventory(service, args)
        if name == "toolbox_lend":
            return self._call_lend(service, args)
        if name == "toolbox_maintenance":
            return self._call_maintenance(service, args)
        if name == "toolbox_project":
            return self._call_project(service, args)
        if name == "toolbox_seasonal":
            return self._call_seasonal(service, args)
        if name == "toolbox_summary":
            return service.summary()
        raise ToolkitError(-32602, f"unknown tool: {name}")

    def _call_inventory(self, service: Toolbox, args: dict) -> dict:
        action = args.get("action", "list")
        if action == "add":
            return service.add_item(
                name=args.get("name", ""), kind=args.get("kind", "tool"),
                category=args.get("category", ""),
                location=args.get("location", ""),
                quantity=args.get("quantity", 1),
                low_at=args.get("low_at", 1),
                condition=args.get("condition", "good"),
                notes=args.get("notes", ""),
            )
        if action == "update":
            fields = {
                key: args.get(key)
                for key in ("name", "kind", "category", "location",
                            "quantity", "low_at", "condition", "notes")
            }
            return service.update_item(item=args.get("item", ""), **fields)
        if action == "remove":
            return service.remove_item(item=args.get("item", ""))
        if action == "low-stock":
            return service.list_items(include_low=True)
        return service.list_items(
            kind=args.get("kind", ""), category=args.get("category", ""),
            search=args.get("search", ""),
        )

    def _call_lend(self, service: Toolbox, args: dict) -> dict:
        action = args.get("action", "ledger")
        if action == "lend":
            return service.lend(
                item=args.get("item", ""), borrower=args.get("borrower", ""),
                due_on=args.get("due_on", ""), notes=args.get("notes", ""),
            )
        if action == "return":
            return service.return_tool(
                loan=args.get("loan", ""), item=args.get("item", "")
            )
        return service.ledger()

    def _call_maintenance(self, service: Toolbox, args: dict) -> dict:
        action = args.get("action", "nudges")
        if action == "log":
            return service.log_maintenance(
                item=args.get("item"), item_id=None,
                task=args.get("task", ""),
                interval_days=args.get("interval_days", 0),
                last_done=args.get("last_done", ""),
                notes=args.get("notes", ""),
            )
        if action == "complete":
            return service.complete_maintenance(task=args.get("task", ""))
        return service.maintenance_log()

    def _call_project(self, service: Toolbox, args: dict) -> dict:
        action = args.get("action", "list")
        if action == "add":
            return service.add_project(
                name=args.get("name", ""),
                needed=args.get("needed"), notes=args.get("notes", ""),
            )
        if action == "gaps":
            return service.gap_list(project=args.get("project", ""))
        if action == "complete":
            return service.complete_project(project=args.get("project", ""))
        return service.projects()

    def _call_seasonal(self, service: Toolbox, args: dict) -> dict:
        action = args.get("action", "prompt")
        if action == "prompt":
            return service.seasonal_prompt(month=args.get("month"))
        if action == "log":
            return service.log_season_done(
                job=args.get("job", ""), month=args.get("month"),
                notes=args.get("notes", ""),
            )
        return service.season_history()

    # -- result rendering ---------------------------------------------

    @staticmethod
    def _render_result(structured: dict) -> dict:
        lines: list[str] = []
        if structured.get("status") != "ok":
            lines.append("Hmm, that did not go through as expected.")
        extra = structured.get("extra", "")
        if extra:
            lines.append(extra)
        body = {key: value for key, value in structured.items()
                if key not in ("status", "extra") and value not in (None, [])}
        if body:
            lines.append(json.dumps(body, indent=2))
        return {
            "content": [{"type": "text", "text": "\n".join(lines) or "Done."}],
            "structuredContent": structured,
        }

    @staticmethod
    def _tool_text_result(text: str, structured: Any) -> dict:
        payload: dict = {
            "content": [{"type": "text", "text": text}],
            "isError": True,
        }
        if structured is not None:
            payload["structuredContent"] = structured
        return payload

    # -- run loop -----------------------------------------------------

    def serve_forever(self) -> None:
        self._log("listening on stdin (JSON-RPC/MCP, one message per line)")
        for raw_line in sys.stdin:
            line = raw_line.strip()
            if not line:
                continue
            reply = self.handle_line(line)
            if reply is not None:
                print(reply, file=self.out, flush=True)
        self._log("goodbye")


class ToolkitError(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def build_service(store_path: str, base_dir: Optional[str] = None,
                  clock: Any = None) -> Toolbox:
    store = JsonStore(store_path, base_dir=base_dir, clock=_stamp_clock)
    document = store.load(default=None)
    if not document:
        day = clock() if clock else today()
        store.save(fresh_document(day))
        document = store.load(default={})
    return Toolbox(document, clock=clock)


def persist_callback(service: Toolbox, store: JsonStore) -> Any:
    return lambda: store.save(service.doc)


def _stamp_clock() -> str:
    from datetime import datetime
    return datetime.now().strftime("%Y%m%d-%H%M%S-%f")


def main(argv: Optional[list[str]] = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    data_dir = os.environ.get("TOOLBOX_DATA_DIR") or \
        os.environ.get("TOOLBOX_BASE_DIR") or os.getcwd()
    store_file = argv[0] if argv else os.environ.get(
        "TOOLBOX_STORE", "data/toolbox.json"
    )
    try:
        store = JsonStore(store_file, base_dir=data_dir, clock=_stamp_clock)
    except UnsafePathError as exc:
        print(f"[{SERVER_NAME}] refusing unsafe storage path: {exc}",
              file=sys.stderr)
        return 2
    document = store.load(default=None)
    if not document:
        store.save(fresh_document(today()))
        document = store.load(default={})
    service = Toolbox(document)
    server = McpServer(
        service, save_callback=lambda: store.save(service.doc)
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print(f"[{SERVER_NAME}] interrupted; data saved as we write",
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
