"""End-to-end test: run the real server as a subprocess and speak MCP
over stdin/stdout, exactly as a real MCP client would.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


class McpClient:
    def __init__(self, proc: subprocess.Popen) -> None:
        self.proc = proc
        self.next_id = 1

    def send(self, method: str, params: dict | None = None,
             notify: bool = False) -> dict | None:
        message: dict = {"jsonrpc": "2.0", "method": method}
        if not notify:
            message["id"] = self.next_id
        if params is not None:
            message["params"] = params
        if not notify:
            self.next_id += 1
        self.proc.stdin.write(json.dumps(message) + "\n")
        self.proc.stdin.flush()
        if notify:
            return None
        line = self.proc.stdout.readline()
        assert line, "server closed early; stderr: " + self.proc.stderr.read()
        return json.loads(line)

    def initialize(self) -> dict:
        reply = self.send("initialize", {
            "protocolVersion": "2025-06-18",
            "clientInfo": {"name": "smoke-test", "version": "1"},
        })
        self.send("notifications/initialized", notify=True)
        return reply["result"]

    def call(self, tool: str, arguments: dict) -> dict:
        reply = self.send("tools/call",
                          {"name": tool, "arguments": arguments})
        return reply["result"]


@pytest.fixture()
def mcp(tmp_path: Path):
    env = dict(os.environ)
    env["TOOLBOX_DATA_DIR"] = str(tmp_path)
    proc = subprocess.Popen(
        [sys.executable, "-m", "toolbox_mcp"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, cwd=str(REPO_ROOT), env=env,
    )
    try:
        client = McpClient(proc)
        client.initialize()
        yield client, tmp_path
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def test_full_handshake(mcp):
    client, _ = mcp
    listing = client.send("tools/list")
    names = [t["name"] for t in listing["result"]["tools"]]
    assert "toolbox_lend" in names
    assert "toolbox_inventory" in names


def test_seed_data_and_lend_round_trip(mcp):
    client, tmp_path = mcp
    summary = client.call("toolbox_summary", {})
    assert summary["structuredContent"]["out_on_loan"] == 1
    result = client.call("toolbox_lend", {
        "action": "lend", "item": "Cordless drill",
        "borrower": "Neighbor Sam", "due_on": "2027-01-14"})
    assert result["structuredContent"]["status"] == "ok"
    # data survived on disk
    on_disk = json.loads(
        (tmp_path / "data" / "toolbox.json").read_text())
    assert any(l["borrower"] == "Neighbor Sam"
               for l in on_disk["loans"] if not l.get("returned_on"))
    # and a fresh server sees the same loan (persistence across restarts)
    env = dict(os.environ, TOOLBOX_DATA_DIR=str(tmp_path))
    proc = subprocess.Popen(
        [sys.executable, "-m", "toolbox_mcp"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, cwd=str(REPO_ROOT), env=env,
    )
    try:
        fresh = McpClient(proc)
        fresh.initialize()
        ledger = fresh.call("toolbox_lend", {"action": "ledger"})
        names = [loan.get("borrower") for loan in
                 ledger["structuredContent"]["loans"]]
        assert "Neighbor Sam" in names
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def test_unsafe_store_path_fails_closes_cleanly(tmp_path):
    env = dict(os.environ, TOOLBOX_DATA_DIR=str(tmp_path))
    env["TOOLBOX_STORE"] = "../escape.json"
    proc = subprocess.run(
        [sys.executable, "-m", "toolbox_mcp"],
        input="", text=True, timeout=30, env=env,
        capture_output=True,
    )
    assert proc.returncode == 2
    assert "unsafe" in proc.stderr.lower()
    assert not (tmp_path.parent / "escape.json").exists()
