# Toolbox Lending MCP Server

A beginner-friendly **MCP server** (Model Context Protocol) for the household
toolbox. Track your tools and consumables, keep a **lend-a-tool ledger** (who
borrowed what, when it came home), get **maintenance nudges** by interval,
turn **project gap lists into a shopping list**, and receive **seasonal
tune-up prompts**.

Built by [Asdale Dynamics, LLC](https://dylanasdale.github.io) as part of
Dylan Asdale's easy, friendly AI tools — plain English, local-first, no
cloud required.

**Standard library only.** No pip installs, no API keys, no network calls.
Your toolbox stays on your machine as simple JSON you can open in any editor.

## What it does

| Tool | What it is for |
|---|---|
| `toolbox_inventory` | Tools and consumables (hand tools, power tools, drill bits, glue). List, add, update, remove, and check what is running low. |
| `toolbox_lend` | The lend-a-tool ledger: who has it, when it left, when it came home. Gentle nudges when something is due or overdue. |
| `toolbox_maintenance` | Repeating chores by interval — sharpen blades (~90 days), oil and battery checks (~90–540 days). Marking done moves the next date out. |
| `toolbox_project` | Say what a project needs; the server compares against the inventory and hands back a gap list that doubles as a shopping list. |
| `toolbox_seasonal` | Month-appropriate prompts, like sharpening things in spring and bringing batteries inside before winter. |
| `toolbox_summary` | One friendly snapshot: counts, loans out, low stock, what's due. |

## Quick start

Requirements: **Python 3.10+** (nothing else).

```bash
# Run the test suite (offline, no network)
python3 -m pytest

# Start the server (it speaks JSON over stdin/stdout — you don't do this by hand)
python3 -m toolbox_mcp
```

On first run the server seeds **gentle sample data** — a claw hammer, a
cordless drill, a project, one almost-due loan — so there is something to
play with. Everything is saved to `data/toolbox.json`, with automatic backups
in `data/backups/` before every change.

### Wire it to your MCP client

Add to your MCP client's config (e.g. Claude Code):

```json
{
  "mcpServers": {
    "toolbox": {
      "command": "python3",
      "args": ["-m", "toolbox_mcp"],
      "cwd": "/path/to/toolbox-lending-mcp-server"
    }
  }
}
```

### Where the data lives

| Setting | Meaning | Default |
|---|---|---|
| `TOOLBOX_DATA_DIR` | The directory the JSON store is allowed to live in | current directory |
| `TOOLBOX_STORE` | The `.json` file name inside that directory | `data/toolbox.json` |

**Path safety:** the server refuses any store path that could escape the
`TOOLBOX_DATA_DIR` — no `..` segments, no absolute paths outside it, no
symlink tricks, nothing inside `backups/`. A hostile prompt cannot point it
at your other files.

### Backup and recovery

Before every save, the previous file is copied to
`data/backups/toolbox.json.<timestamp>.bak.json` (kept: 10 newest). If the
main file is ever corrupted, the server reloads the newest good backup on the
next start.

## Smoke test from a terminal

Paste this into a shell to see the handshake and one real tool call:

```bash
printf '%s\n%s\n' \
'{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' \
'{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"toolbox_seasonal","arguments":{"action":"prompt","month":10}}}' \
| python3 -m toolbox_mcp
```

You should see two JSON lines: the MCP handshake and an October tune-up
prompt.

### Example questions to ask your assistant

- "What's out on loan from the toolbox and when is it due back?"
- "What maintenance is overdue?" (blades, oil, batteries)
- "I want to build a picture-frame shelf — what do I need to buy?"
- "What should I do to the toolbox this month?"

## For families, not just docks

Everything the assistant says is plain sentences: "Claw hammer is now out on
loan to Neighbor Sam. Hoping to see it back by 2026-11-03." No jargon walls,
no dashboards. And nothing leaves your machine.

## Testing

```bash
python3 -m pytest        # 64 tests, fully offline
```

The suite covers the MCP handshake, every tool action, persistence and
backup recovery, and a set of **security cases** (path traversal, symlinks,
absolute escapes, backups-dir protection).

## Related projects by Asdale Dynamics, LLC

- `home-maintenance-mcp-server` — the repair and fix-it side of the house
- `checklists-mcp-server` — general-purpose household checklists

## License

MIT — see [LICENSE](LICENSE). Copyright (c) 2026 Asdale Dynamics, LLC.
