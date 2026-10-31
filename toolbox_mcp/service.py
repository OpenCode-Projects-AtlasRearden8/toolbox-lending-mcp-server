"""Domain logic: inventory, lend-a-tool ledger, maintenance nudges,
project gap lists, and seasonal tune-up prompts.

Every function is a small, plain Python method so tests can call them
directly without spawning the MCP server.
"""

from __future__ import annotations

import calendar
import datetime as _dt
from typing import Any, Optional


# ---------------------------------------------------------------- helpers

def today() -> _dt.date:
    return _dt.date.today()


def parse_date(value: Optional[str], *, field: str) -> _dt.date:
    if value in (None, ""):
        raise ValueError(f"{field} is required (YYYY-MM-DD)")
    try:
        return _dt.date.fromisoformat(str(value))
    except ValueError:
        raise ValueError(f"{field} must be a date like 2026-01-31, got {value!r}")


def _next_id(existing: list[dict], prefix: str) -> str:
    numbers = []
    for entry in existing:
        eid = str(entry.get("id", ""))
        if eid.startswith(prefix + "-") and eid[len(prefix) + 1 :].isdigit():
            numbers.append(int(eid[len(prefix) + 1 :]))
    return f"{prefix}-{max(numbers, default=0) + 1:03d}"


def _find_by_name_or_id(entries: list[dict], key: str,
                        value: Optional[str]) -> Optional[dict]:
    if not value:
        return None
    for entry in entries:
        if entry.get("id") == value or entry.get("name", "").lower() == value.lower():
            return entry
    return None


# ---------------------------------------------------------------- service

class Toolbox:
    """All toolbox operations against one JSON document."""

    def __init__(self, document: dict, clock: Any = None) -> None:
        for key in ("items", "loans", "maintenance", "projects", "seasonal_log"):
            document.setdefault(key, [])
        self.doc = document
        self._today = clock or today

    # ------------------------------------------------------ inventory

    def add_item(self, *, name: str, kind: str = "tool", category: str = "",
                 location: str = "", quantity: int = 1, low_at: int = 1,
                 condition: str = "good", notes: str = "") -> dict:
        name = str(name).strip()
        if not name:
            raise ValueError("name is required")
        kind = str(kind).lower()
        if kind not in ("tool", "consumable"):
            raise ValueError('kind must be "tool" or "consumable"')
        try:
            quantity = max(0, int(quantity))
            low_at = max(0, int(low_at))
        except (TypeError, ValueError):
            raise ValueError("quantity and low_at must be whole numbers")
        if _find_by_name_or_id(self.doc["items"], "name", name):
            raise ValueError(
                f'there is already something called "{name}" in the inventory'
            )
        item = {
            "id": _next_id(self.doc["items"], "i"),
            "name": name,
            "kind": kind,
            "category": str(category).strip(),
            "location": str(location).strip(),
            "quantity": quantity,
            "low_at": low_at,
            "condition": str(condition).strip() or "good",
            "notes": str(notes).strip(),
        }
        self.doc["items"].append(item)
        extra = (
            f'"{name}" added to the toolbox as a {kind}.'
            if kind == "tool"
            else f'"{name}" added as a consumable.'
        )
        return {"status": "ok", "item": item, "extra": extra}

    def list_items(self, *, kind: str = "", category: str = "",
                   search: str = "", include_low: bool = False) -> dict:
        rows = [
            dict(item)
            for item in self.doc["items"]
            if (not kind or item.get("kind") == kind.lower())
            and (not category or item.get("category", "").lower()
                 == category.lower())
            and (not search or search.lower() in json_flat(item))
            and (
                not include_low
                or item.get("quantity", 99)
                <= item.get("low_at", 0)
            )
        ]
        low = [i for i in self.doc["items"]
               if i.get("kind") == "consumable"
               and i.get("quantity", 0) <= i.get("low_at", 0)]
        extra = f"{len(rows)} item(s) listed."
        if include_low and low:
            extra += " low stock: " + ", ".join(i["name"] for i in low)
        return {"status": "ok", "items": rows, "extra": extra}

    def update_item(self, *, item: str, **fields: Any) -> dict:
        found = self._need_item(item)
        allowed = ("name", "kind", "category", "location",
                   "quantity", "low_at", "condition", "notes")
        changed = []
        for key, value in fields.items():
            if key not in allowed or value is None:
                continue
            if key in ("quantity", "low_at"):
                value = max(0, int(value))
            found[key] = value
            changed.append(key)
        if not changed:
            raise ValueError("nothing changed: pass at least one field")
        return {
            "status": "ok",
            "item": found,
            "extra": f'Updated {", ".join(changed)} for "{found["name"]}".',
        }

    def remove_item(self, *, item: str) -> dict:
        found = self._need_item(item)
        borrow = self.has_loan(item_id=found["id"])
        if borrow is not None:
            raise ValueError(
                f'"{found["name"]}" is currently lent out; record its return first.'
            )
        self.doc["items"].remove(found)
        self.doc["maintenance"] = [
            m for m in self.doc["maintenance"] if m.get("item_id") != found["id"]
        ]
        return {"status": "ok", "removed": found["name"],
                "extra": f'"{found["name"]}" removed from the toolbox.'}

    def _need_item(self, item: str) -> dict:
        found = _find_by_name_or_id(self.doc["items"], "id", item)
        if not found:
            raise ValueError(
                f'no inventory item matches "{item}". '
                "Use toolbox_inventory action=list to see what is there."
            )
        return found

    # --------------------------------------------------------- ledger

    def lend(self, *, item: str, borrower: str, due_on: str = "",
             notes: str = "") -> dict:
        found = self._need_item(item)
        titre = borrower.strip()
        if not titre:
            raise ValueError("borrower is required")
        lent_day = self._today()
        if self.has_loan(item_id=found["id"]) is not None:
            current = self._open_loan(found["id"])
            raise ValueError(
                f'"{found["name"]}" is already out with '
                f'{current["borrower"]} (lent {current["lent_on"]}).'
            )
        due_day = parse_date(due_on, field="due_on") if due_on else None
        loan = {
            "id": _next_id(self.doc["loans"], "l"),
            "item_id": found["id"],
            "item_name": found["name"],
            "borrower": titre,
            "lent_on": lent_day.isoformat(),
            "due_on": due_day.isoformat() if due_day else None,
            "returned_on": None,
            "notes": str(notes).strip(),
        }
        self.doc["loans"].append(loan)
        message = f'"{found["name"]}" is now out on loan to {titre}.'
        if due_day:
            message += f" Hoping to see it back by {due_day.isoformat()}."
        return {"status": "ok", "loan": loan, "extra": message}

    def return_tool(self, *, loan: str = "", item: str = ""
                    ) -> dict:
        open_loan = self._find_loan(loan=loan, item=item)
        open_loan["returned_on"] = self._today().isoformat()
        return {
            "status": "ok",
            "loan": open_loan,
            "extra": (
                f'"{open_loan.get("item_name", open_loan["item_id"])}" is back home. '
                f"Nice {open_loan['borrower']}!"
            ),
        }

    def ledger(self) -> dict:
        open_loans = [l for l in self.doc["loans"] if not l.get("returned_on")]
        now = self._today()
        nudge: list[tuple[dict, str]] = []
        for loan in open_loans:
            if loan.get("due_on"):
                due = _dt.date.fromisoformat(loan["due_on"])
                if due < now:
                    nudge.append((loan, f"overdue since {loan['due_on']}"))
                elif (due - now).days <= 3:
                    nudge.append((loan, f"due {loan['due_on']}"))
        extra = f"{len(open_loans)} borrowed tool(s) still out."
        if nudge:
            extra += " Ahem: " + "; ".join(
                f'{loan_.get("item_name", loan_["item_id"])} ({loan_["borrower"]}) {why}'
                for loan_, why in nudge
            )
        return {"status": "ok", "loans": list(self.doc["loans"]), "extra": extra}

    def has_loan(self, *, item_id: str) -> Optional[dict]:
        loan = self._open_loan(item_id)
        return loan

    def _open_loan(self, item_id: str) -> Optional[dict]:
        for loan in self.doc["loans"]:
            if loan.get("item_id") == item_id and not loan.get("returned_on"):
                return loan
        return None

    def _find_loan(self, *, loan: str = "", item: str = "") -> dict:
        if loan:
            for entry in self.doc["loans"]:
                if entry["id"] == loan and not entry.get("returned_on"):
                    return entry
            raise ValueError(f"no open loan with id {loan!r}")
        found = self._need_item(item)
        entry = self._open_loan(found["id"])
        if not entry:
            raise ValueError(
                f'"{found["name"]}" is not out on loan right now.'
            )
        return entry

    # ---------------------------------------------------- maintenance

    def log_maintenance(self, *, item: Optional[str] = None, item_id: Optional[str] = None,
                        task: str, interval_days: int,
                        last_done: str = "", notes: str = "") -> dict:
        target = self._resolve_item(item, item_id)
        interval_days = int(interval_days)
        if interval_days <= 0:
            raise ValueError("interval_days must be a positive number")
        done_day = parse_date(last_done, field="last_done") if last_done \
            else self._today()
        entry = {
            "id": _next_id(self.doc["maintenance"], "m"),
            "item_id": target["id"] if target else "",
            "item_name": target["name"] if target else "",
            "task": str(task).strip(),
            "interval_days": interval_days,
            "last_done": done_day.isoformat(),
            "notes": str(notes).strip(),
        }
        self.doc["maintenance"].append(entry)
        next_day = done_day + _dt.timedelta(days=interval_days)
        return {
            "status": "ok",
            "task": entry,
            "extra": f'Scheduled "{entry["task"]}"; next around {next_day.isoformat()}.',
        }

    def maintenance_log(self) -> dict:
        now = self._today()
        nudges = []
        items_by_id = {i["id"]: i for i in self.doc["items"]}
        for entry in self.doc["maintenance"]:
            done_day = _dt.date.fromisoformat(entry["last_done"])
            due_day = done_day + _dt.timedelta(days=entry["interval_days"])
            if due_day < now:
                state = f"overdue since {due_day.isoformat()}"
                urgency = "overdue"
            elif (due_day - now).days <= 7:
                state = f"due {due_day.isoformat()}"
                urgency = "soon"
            else:
                state = "on track"
                urgency = "later"
            who = entry.get("item_name") or (
                items_by_id.get(entry.get("item_id"), {}).get("name", "")
            ) or entry.get("item_id", "")
            nudges.append({
                "id": entry["id"],
                "item": who,
                "task": entry["task"],
                "state": state,
                "urgency": urgency,
            })
        nudges.sort(key=lambda n: {"overdue": 0, "soon": 1, "later": 2}[n["urgency"]])
        actionable = [n for n in nudges if n["urgency"] in ("overdue", "soon")]
        extra = "Nothing needs attention right now."
        if actionable:
            extra = "Poke list: " + "; ".join(
                f'{n["item"] or "toolbox"} - {n["task"]} ({n["state"]})'
                for n in actionable
            )
        return {"status": "ok", "nudges": nudges, "extra": extra}

    def complete_maintenance(self, *, task: str) -> dict:
        entry = _find_by_name_or_id(self.doc["maintenance"], "id", task)
        if not entry:
            raise ValueError(f"no maintenance task matches {task!r}")
        entry["last_done"] = self._today().isoformat()
        return {
            "status": "ok",
            "task": entry,
            "extra": f'Marked "{entry["task"]}" done today. Good habit!',
        }

    def _resolve_item(self, item: Optional[str],
                      item_id: Optional[str]) -> Optional[dict]:
        value = item or item_id
        if not value:
            return None
        return self._need_item(value)

    # ------------------------------------------------------- projects

    def add_project(self, *, name: str, needed: Optional[list[Any]] = None,
                    notes: str = "") -> dict:
        name = str(name).strip()
        if not name:
            raise ValueError("name is required")
        cleaned = []
        for row in needed or []:
            if isinstance(row, dict):
                cleaned.append({"name": str(row.get("name", "")).strip(),
                                "qty": max(1, int(row.get("qty", 1)))})
            else:
                cleaned.append({"name": str(row).strip(), "qty": 1})
            if not cleaned[-1]["name"]:
                raise ValueError("each needed item needs a name")
        project = {
            "id": _next_id(self.doc["projects"], "p"),
            "name": name,
            "status": "planning",
            "needed": cleaned,
            "notes": str(notes).strip(),
        }
        self.doc["projects"].append(project)
        return {
            "status": "ok",
            "project": project,
            "extra": f'Project "{name}" saved with {len(cleaned)} needed item(s).',
        }

    def projects(self) -> dict:
        rows = list(self.doc["projects"])
        extra = f"{len(rows)} project(s) on the board."
        return {"status": "ok", "projects": rows, "extra": extra}

    def gap_list(self, *, project: str) -> dict:
        found = _find_by_name_or_id(self.doc["projects"], "id", project)
        if not found:
            raise ValueError(f'no project matches "{project}"')
        gaps: list[dict] = []
        for strap in found["needed"]:
            stock = _find_by_name_or_id(self.doc["items"], "id", strap["name"])
            have = stock.get("quantity", 0) if stock else 0
            short = max(0, strap["qty"] - have)
            gaps.append({
                "name": strap["name"],
                "need": strap["qty"],
                "have": have,
                "buy": short,
                "in_inventory": bool(stock),
            })
        shopping = [g for g in gaps if g["buy"] > 0]
        if shopping:
            extra = "Shopping list: " + "; ".join(
                (f"{g['name']} x{g['buy']}" if g["in_inventory"]
                 else f"{g['name']} x{g['buy']} (not in toolbox yet)")
                for g in shopping
            )
        else:
            extra = "Good news: you have everything this project needs."
        return {"status": "ok", "project": found["name"],
                "gaps": gaps, "extra": extra}

    def complete_project(self, *, project: str) -> dict:
        found = _find_by_name_or_id(self.doc["projects"], "id", project)
        if not found:
            raise ValueError(f'no project matches "{project}"')
        found["status"] = "done"
        return {"status": "ok", "project": found,
                "extra": f'"{found["name"]}" marked done. Nice work!'}

    # ------------------------------------------------------- seasonal

    SEASON_JOBS: dict[int, list[str]] = {
        1: ["Check drill batteries hold charge in cold snaps",
            "Wipe down hand tools - dry winter air rusts them"],
        2: ["Sharpen mower blade before spring",
            "Inventory screws and bolts - restock what ran out last year"],
        3: ["Tune-up weekend: oil hinges and latches",
            "Sort the tool chest - put spring things up front"],
        4: ["Check ladder safety (rungs, feet) before gutter duty",
            "Test smoke detector batteries"],
        5: ["Air up tires and test garden gear as it comes out"],
        6: ["Fence and deck tools: clean the saw, oil the pruners"],
        7: ["Garage deep clean - sweep out, check for rust"],
        8: ["Sharpen yard-tool blades ahead of fall"],
        9: ["Prep generator (if kept) and check winter batteries"],
        10: ["Tune-up weekend: rake, leaf blower, ladder audit",
             "Check weatherstripping kit stock before it gets cold"],
        11: ["Bring batteries inside - cold kills them",
             "Fog king oil: store gas gear for winter (drain fuel)"],
        12: ["One cozy hour: inventory everything, update low-stock",
             "Plan next year's projects from the gap list"],
    }

    def seasonal_prompt(self, *, month: Optional[int] = None) -> dict:
        if month is None:
            month = self._today().month
        try:
            month = int(month)
        except (TypeError, ValueError):
            raise ValueError("month must be 1-12")
        if not 1 <= month <= 12:
            raise ValueError("month must be 1-12")
        season = ("winter", "winter", "spring", "spring", "spring", "summer",
                  "summer", "summer", "fall", "fall", "fall", "winter")[month - 1]
        month_name = calendar.month_name[month]
        jobs = list(self.SEASON_JOBS[month])
        return {
            "status": "ok",
            "month": month,
            "month_name": month_name,
            "season": season,
            "jobs": jobs,
            "extra": (
                f"{month_name} ({season}) tune-up ideas: " + "; ".join(jobs)
            ),
        }

    def log_season_done(self, *, job: str, month: Optional[int] = None,
                        notes: str = "") -> dict:
        if month is None:
            month = self._today().month
        month = int(month)
        if not 1 <= month <= 12:
            raise ValueError("month must be 1-12")
        entry = {
            "id": _next_id(self.doc["seasonal_log"], "s"),
            "job": str(job).strip(),
            "month": month,
            "done_on": self._today().isoformat(),
            "notes": str(notes).strip(),
        }
        self.doc["seasonal_log"].append(entry)
        return {"status": "ok", "entry": entry,
                "extra": f'Logged "{entry["job"]}" for month {month}.'}

    def season_history(self) -> dict:
        rows = list(self.doc["seasonal_log"])
        return {"status": "ok", "log": rows,
                "extra": f"{len(rows)} seasonal task(s) logged."}

    # -------------------------------------------------------- summary

    def summary(self) -> dict:
        tools = [i for i in self.doc["items"] if i.get("kind") == "tool"]
        consumables = [i for i in self.doc["items"] if i.get("kind") == "consumable"]
        low = [i["name"] for i in consumables
               if i.get("quantity", 0) <= i.get("low_at", 0)]
        out_on_loan = [l for l in self.doc["loans"] if not l.get("returned_on")]
        muddy = []
        now = self._today()
        for entry in self.doc["maintenance"]:
            due = (_dt.date.fromisoformat(entry["last_done"])
                   + _dt.timedelta(days=entry["interval_days"]))
            if due <= now:
                muddy.append(entry["task"])
        extra = (
            f"{len(tools)} tool(s), {len(consumables)} consumable(s), "
            f"{len(out_on_loan)} out on loan"
            + (f", {len(low)} low on stock" if low else "")
            + (f", {len(muddy)} maintenance task(s) due" if muddy else "")
            + "."
        )
        return {
            "status": "ok",
            "inventory": len(self.doc["items"]),
            "tools": len(tools),
            "consumables": len(consumables),
            "out_on_loan": len(out_on_loan),
            "low_stock": low,
            "maintenance_due": muddy,
            "extra": extra,
        }


def json_flat(item: dict) -> str:
    return " ".join(str(value) for value in item.values() if value)
