# STATUS.md — toolbox-lending-mcp-server

**Last verified:** 2026-10-31

## What was built

A complete, standard-library-only **MCP (Model Context Protocol) server** over
stdio for the household toolbox, by Asdale Dynamics, LLC.

**Six tools exposed to any MCP client:**

1. `toolbox_inventory` — tools and consumables; list/search/add/update/remove,
   low-stock view.
2. `toolbox_lend` — lend-a-tool ledger: who borrowed what, when it left, when
   it came home, gentle nudges for due (≤3 days) and overdue entries.
3. `toolbox_maintenance` — repeating chores by interval (blades ~90 days, oil,
   batteries ~540 days), sorted by urgency; `complete` moves the next due date.
4. `toolbox_project` — project gap lists compared against the inventory,
   doubling as a shopping list.
5. `toolbox_seasonal` — month-specific tune-up prompts with a done-log.
6. `toolbox_summary` — one-glance snapshot.

**Infrastructure:**

- MCP protocol: JSON-RPC 2.0 over stdio, one message per line
  (`initialize`, `notifications/initialized`, `ping`, `shutdown`,
  `tools/list`, `tools/call`; protocol version `2025-06-18`).
- Tool results carry both `structuredContent` (machine) and a friendly
  `text` blurb ("Claw hammer is now out on loan to Neighbor Sam...").
- JSON storage (`data/toolbox.json`), **path-safety enforcement**
  (`toolbox_mcp/storage.py`): no `..` traversal, no absolute escapes, symlink
  resolution re-checked, `backups/` protected, `.json`-only names.
- **Auto-backup** before every write: `data/backups/toolbox.json.<stamp>.bak.json`,
  newest 10 kept; corrupt main file recovers from the newest backup.
- **Gentle sample data** on first run: relative dates so the demo always
  shows one near-due loan and one overdue maintenance task.
- Offline pytest suite (64 tests) including dedicated security cases,
  subprocess end-to-end MCP client tests, and persistence tests.
- MIT LICENSE with the line `Copyright (c) 2026 Asdale Dynamics, LLC`.

## How to run it

```bash
# tests (offline, ~0.2s)
python3 -m pytest

# server over stdio
python3 -m toolbox_mcp
# or: python3 -m toolbox_mcp mybox.json   (file name inside TOOLBOX_DATA_DIR)

# env knobs
TOOLBOX_DATA_DIR=/path/to/data  python3 -m toolbox_mcp
TOOLBOX_STORE=toolbox.json                     # default
```

## Verification results (as run on 2026-10-31)

- `python3 -m pytest` → **64 passed**, no network, under 1 second.
- `python3 -m pyflakes toolbox_mcp tests` → clean.
- Manual stdio smoke test (piped initialize + tools/call) → correct JSON-RPC
  replies; maintenance nudges render friendly item names.
- Security checks confirmed: traversal (`../escape.json`), absolute paths,
  symlink escape, and `backups/`-tree paths all rejected with exit code 2 in
  the subprocess test; no files written outside the data dir.
- End-to-end subprocess test: handshake, seeding, lending, persistence
  across server restarts — all verified in `tests/test_e2e.py`.

## Known gaps / honest notes

- The MCP layer is a minimal, hand-rolled JSON-RPC implementation covering
  the core lifecycle (initialize/tools/list/tools/call). It does not
  implement resource or prompt surfaces, cursors, or protocol version
  negotiation logic beyond returning `2025-06-18`.
- No schema validation library: tool arguments are validated in Python, so a
  typo like `"kind": "both"` returns a friendly error rather than a
  schema-diff.
- Sample-data item names are matched case-insensitively but not
  fuzzily ("cordless drill" matches exactly; fuzzy search could be added).
- Backups are file-level (`json` + `os.replace`), not transactional across
  multiple rapid writes.
- Seasonal prompts are static month-based guidance — no weather or location
  integration (intentionally: local-first, no network).
