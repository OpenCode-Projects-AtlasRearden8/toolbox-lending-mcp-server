"""MCP protocol tests: initialize, tools/list, tools/call over JSON-RPC."""
import io
import json
from datetime import date

from toolbox_mcp.server import McpServer, tool_catalogue
from toolbox_mcp.seed import fresh_document
from toolbox_mcp.service import Toolbox

FIXED_TODAY = date(2026, 10, 31)


def make_service():
    return Toolbox(fresh_document(FIXED_TODAY), clock=lambda: FIXED_TODAY)


def rpc(server: McpServer, payload: dict) -> dict:
    line = server.handle_line(json.dumps(payload))
    assert line is not None, f"no reply for {payload['method']}"
    return json.loads(line)


def initialize(server: McpServer) -> dict:
    return rpc(server, {
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-06-18",
                   "clientInfo": {"name": "pytest", "version": "0"}},
    })


# ------------------------------------------------------------------ basics

def test_initialize_handshake():
    server = McpServer(make_service(), out=io.StringIO(), err=io.StringIO())
    reply = initialize(server)
    result = reply["result"]
    assert result["protocolVersion"] == "2025-06-18"
    assert "tools" in result["capabilities"]
    assert result["serverInfo"]["name"] == "toolbox-lending-mcp-server"


def test_ping_and_shutdown():
    server = McpServer(make_service(), out=io.StringIO(), err=io.StringIO())
    assert rpc(server, {"jsonrpc": "2.0", "id": 2, "method": "ping"})["result"] == {}


def test_unknown_method_is_json_rpc_error():
    server = McpServer(make_service(), out=io.StringIO(), err=io.StringIO())
    reply = rpc(server, {"jsonrpc": "2.0", "id": 3, "method": "bogus/thing"})
    assert reply["error"]["code"] == -32601


def test_notifications_get_no_reply():
    server = McpServer(make_service(), out=io.StringIO(), err=io.StringIO())
    line = server.handle_line(json.dumps({
        "jsonrpc": "2.0", "method": "notifications/initialized"
    }))
    assert line is None


def test_garbage_line_is_swallowed_not_fatal():
    server = McpServer(make_service(), out=io.StringIO(), err=io.StringIO())
    assert server.handle_line("<html hack>") is None


# -------------------------------------------------------------- tools/list

def test_tools_list_covers_all_six():
    server = McpServer(make_service(), out=io.StringIO(), err=io.StringIO())
    reply = rpc(server, {"jsonrpc": "2.0", "id": 4, "method": "tools/list"})
    names = [t["name"] for t in reply["result"]["tools"]]
    assert names == [
        "toolbox_inventory", "toolbox_lend", "toolbox_maintenance",
        "toolbox_project", "toolbox_seasonal", "toolbox_summary",
    ]
    for tool in tool_catalogue():
        assert tool["inputSchema"]["type"] == "object"
        assert tool["description"], tool["name"]


# -------------------------------------------------------------- tools/call

def call(server: McpServer, name: str, arguments: dict) -> dict:
    reply = rpc(server, {
        "jsonrpc": "2.0", "id": 99, "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    })
    return reply["result"]


def test_tool_call_inventory_list():
    server = McpServer(make_service(), out=io.StringIO(), err=io.StringIO())
    result = call(server, "toolbox_inventory", {"action": "list"})
    assert result.get("isError") is not True
    structured = result["structuredContent"]
    assert structured["status"] == "ok"
    assert len(structured["items"]) >= 5
    assert "Claw hammer" in result["content"][0]["text"]


def test_tool_call_lend_then_ledger():
    server = McpServer(make_service(), out=io.StringIO(), err=io.StringIO())
    call(server, "toolbox_lend", {
        "action": "lend", "item": "Cordless drill",
        "borrower": "Neighbor Sam", "due_on": "2026-11-14",
    })
    result = call(server, "toolbox_lend", {"action": "ledger"})
    text = result["content"][0]["text"]
    loans = result["structuredContent"]["loans"]
    drill = next(l for l in loans if l.get("item_name") == "Cordless drill")
    assert drill["borrower"] == "Neighbor Sam"
    assert drill["returned_on"] is None
    assert "due 2026-11-03" in text  # the sample hammer loan, gentle nudge


def test_tool_call_maintenance_nudges():
    server = McpServer(make_service(), out=io.StringIO(), err=io.StringIO())
    result = call(server, "toolbox_maintenance", {"action": "nudges"})
    text = result["content"][0]["text"]
    assert "Poke list" in text
    assert "overdue" in json.dumps(result["structuredContent"])


def test_tool_call_project_gaps_text_mentions_shopping():
    server = McpServer(make_service(), out=io.StringIO(), err=io.StringIO())
    result = call(server, "toolbox_project",
                  {"action": "gaps", "project": "p-001"})
    assert "Shopping list" in result["content"][0]["text"]


def test_tool_call_seasonal_prompt():
    server = McpServer(make_service(), out=io.StringIO(), err=io.StringIO())
    result = call(server, "toolbox_seasonal", {"action": "prompt", "month": 10})
    text = result["content"][0]["text"]
    assert "October" in text and "tune-up" in text.lower()


def test_tool_call_summary():
    server = McpServer(make_service(), out=io.StringIO(), err=io.StringIO())
    result = call(server, "toolbox_summary", {})
    structured = result["structuredContent"]
    assert structured["tools"] >= 3
    assert structured["out_on_loan"] == 1


def test_tool_error_returns_is_error_not_protocol_error():
    server = McpServer(make_service(), out=io.StringIO(), err=io.StringIO())
    result = call(server, "toolbox_lend",
                  {"action": "return", "item": "Saw"})  # not out on loan
    assert result.get("isError") is True
    assert "fix" in result["content"][0]["text"].lower()


def test_unknown_tool_is_invalid_params():
    server = McpServer(make_service(), out=io.StringIO(), err=io.StringIO())
    reply = rpc(server, {
        "jsonrpc": "2.0", "id": 42, "method": "tools/call",
        "params": {"name": "forge_hammer_of_doom", "arguments": {}},
    })
    assert reply["error"]["code"] == -32602


# ------------------------------------------------------------ persistence

def test_tool_call_persists_to_disk(tmp_path):
    from toolbox_mcp.server import build_service
    from toolbox_mcp.storage import JsonStore
    service = build_service("toolbox.json", base_dir=str(tmp_path),
                            clock=lambda: FIXED_TODAY)
    store = JsonStore("toolbox.json", base_dir=str(tmp_path))
    server = McpServer(service, save_callback=lambda: store.save(service.doc))
    call(server, "toolbox_inventory", {
        "action": "add", "name": "Wood saw", "kind": "tool",
        "location": "garage",
    })
    on_disk = json.loads((tmp_path / "toolbox.json").read_text())
    assert any(i["name"] == "Wood saw" for i in on_disk["items"])
    # reload from disk in a fresh server: the saw is still there
    service2 = build_service("toolbox.json", base_dir=str(tmp_path),
                             clock=lambda: FIXED_TODAY)
    listed = service2.list_items(search="saw")
    assert any(i["name"] == "Wood saw" for i in listed["items"])
