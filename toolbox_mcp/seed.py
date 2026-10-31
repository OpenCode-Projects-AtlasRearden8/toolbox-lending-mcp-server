"""Gentle sample data so a brand-new server never feels empty.

The examples are deliberately friendly and generic — no real people,
no real addresses, nothing personal. Just enough to play with.
"""

from __future__ import annotations

from datetime import date, timedelta

SAMPLE_DOCUMENT = {
    "items": [
        {
            "id": "i-001",
            "name": "Claw hammer",
            "kind": "tool",
            "category": "hand tools",
            "location": "garage pegboard",
            "quantity": 1,
            "low_at": 1,
            "condition": "good",
            "notes": "The one with the yellow grip.",
        },
        {
            "id": "i-002",
            "name": "Cordless drill",
            "kind": "tool",
            "category": "power tools",
            "location": "garage shelf",
            "quantity": 1,
            "low_at": 1,
            "condition": "good",
            "notes": "Batteries stored separately.",
        },
        {
            "id": "i-003",
            "name": "Phillips screwdriver set",
            "kind": "tool",
            "category": "hand tools",
            "location": "tool chest, top drawer",
            "quantity": 1,
            "low_at": 1,
            "condition": "good",
            "notes": "",
        },
        {
            "id": "i-004",
            "name": "Drill bits (assorted)",
            "kind": "consumable",
            "category": "accessories",
            "location": "garage shelf",
            "quantity": 0,
            "low_at": 6,
            "condition": "good",
            "notes": "Count remaining bits when restocking.",
        },
        {
            "id": "i-005",
            "name": "Wood glue",
            "kind": "consumable",
            "category": "adhesives",
            "location": "laundry cabinet",
            "quantity": 1,
            "low_at": 1,
            "condition": "good",
            "notes": "",
        },
    ],
    "loans": [
        {
            "id": "l-001",
            "item_id": "i-001",
            "item_name": "Claw hammer",
            "borrower": "Neighbor Sam",
            "lent_on": "2026-10-20",
            "due_on": "2026-11-03",
            "returned_on": None,
            "notes": "",
        }
    ],
    "maintenance": [
        {
            "id": "m-001",
            "item_name": "Cordless drill",
            "item_id": "i-002",
            "task": "Oil the chuck and check battery contacts",
            "interval_days": 90,
            "last_done": "2026-08-01",
            "notes": "",
        },
        {
            "id": "m-002",
            "item_name": "Phillips screwdriver set",
            "item_id": "i-003",
            "task": "Wipe handles and sharpen tips",
            "interval_days": 180,
            "last_done": "2026-04-15",
            "notes": "",
        },
        {
            "id": "m-003",
            "item_name": "Cordless drill",
            "item_id": "i-002",
            "task": "Replace drill battery if it stops holding charge",
            "interval_days": 540,
            "last_done": "2025-05-01",
            "notes": "",
        },
    ],
    "projects": [
        {
            "id": "p-001",
            "name": "Build a picture-frame shelf",
            "status": "planning",
            "needed": [
                {"name": "Cordless drill", "qty": 1},
                {"name": "Wood saw", "qty": 1},
                {"name": "Wood glue", "qty": 1},
            ],
            "notes": "Shopping list grows out of the gap list.",
        }
    ],
    "seasonal_log": [],
}


def fresh_document(today: date | None = None) -> dict:
    """Return a deep copy of the sample data with friendly dates."""
    import copy

    doc = copy.deepcopy(SAMPLE_DOCUMENT)
    if today is not None:
        # Dates below are relative so a fresh install always demos well:
        # one loan almost due, one maintenance task overdue, one soon.
        doc["loans"][0]["lent_on"] = (today - timedelta(days=11)).isoformat()
        doc["loans"][0]["due_on"] = (today + timedelta(days=3)).isoformat()
        doc["maintenance"][0]["last_done"] = (today - timedelta(days=91)).isoformat()
        doc["maintenance"][1]["last_done"] = (today - timedelta(days=199)).isoformat()
        doc["maintenance"][2]["last_done"] = (today - timedelta(days=548)).isoformat()
    return doc
