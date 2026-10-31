"""Domain logic tests, run entirely against a fixed clock."""
import json
from datetime import date

import pytest

import toolbox_mcp.service as svc
from toolbox_mcp.service import Toolbox
from toolbox_mcp.seed import fresh_document

FIXED_TODAY = date(2026, 10, 31)


def make_toolbox(doc=None):
    document = doc if doc is not None else fresh_document(FIXED_TODAY)
    return Toolbox(document, clock=lambda: FIXED_TODAY)


# ------------------------------------------------------------ inventory

def test_sample_data_is_seedable():
    service = make_toolbox()
    result = service.summary()
    assert result["tools"] >= 3
    assert result["consumables"] >= 2
    assert result["out_on_loan"] == 1


def test_add_and_list_items():
    service = make_toolbox()
    added = service.add_item(name="Wood saw", kind="tool",
                             location="garage")
    assert added["status"] == "ok"
    listed = service.list_items(search="saw")
    assert any(i["name"] == "Wood saw" for i in listed["items"])


def test_duplicate_names_are_rejected():
    service = make_toolbox()
    with pytest.raises(ValueError, match="already"):
        service.add_item(name="Claw hammer")


def test_kind_must_be_tool_or_consumable():
    service = make_toolbox()
    with pytest.raises(ValueError, match="kind"):
        service.add_item(name="Mystery", kind="both")


def test_low_stock_detection():
    service = make_toolbox()
    drill_bits = next(i for i in service.doc["items"]
                      if i["name"] == "Drill bits (assorted)")
    assert drill_bits["quantity"] <= drill_bits["low_at"]
    low = service.list_items(include_low=True)
    assert any(i["name"] == "Drill bits (assorted)" for i in low["items"])


def test_remove_item_clears_its_maintenance():
    service = make_toolbox()
    screwdrivers = next(i for i in service.doc["items"]
                        if i["name"].startswith("Phillips"))
    service.remove_item(item="Phillips screwdriver set")
    assert "Phillips screwdriver set" not in json_flat_names(service)
    assert all(m.get("item_id") != screwdrivers["id"]
               for m in service.doc["maintenance"])


def json_flat_names(service):
    return [i["name"] for i in service.doc["items"]]


def test_cannot_remove_lent_out_tool():
    service = make_toolbox()
    with pytest.raises(ValueError, match="return first"):
        service.remove_item(item="Claw hammer")


def test_update_item_quantity():
    service = make_toolbox()
    result = service.update_item(item="Wood glue", quantity=0)
    assert result["item"]["quantity"] == 0
    low = service.list_items(include_low=True)
    assert any(i["name"] == "Wood glue" for i in low["items"])


# ---------------------------------------------------------------- ledger

def test_lend_and_return_round_trip():
    service = make_toolbox()
    lent = service.lend(item="Cordless drill", borrower="Neighbor Sam",
                        due_on="2026-11-14")
    assert lent["loan"]["returned_on"] is None
    returned = service.return_tool(item="Cordless drill")
    assert returned["loan"]["returned_on"] == "2026-10-31"
    ledger = service.ledger()
    open_names = [l.get("item_name", l["item_id"])
                  for l in ledger["loans"] if not l.get("returned_on")]
    assert "Cordless drill" not in open_names
    assert "Claw hammer" in open_names  # sample loan still out


def test_cannot_lend_twice():
    service = make_toolbox()
    with pytest.raises(ValueError, match="already out"):
        service.lend(item="Claw hammer", borrower="Neighbor Sam")


def test_overdue_nudge():
    service = make_toolbox()
    # sample loan: lent 11 days ago, due in 3 days -> not overdue yet
    ledger = service.ledger()
    assert "due" in ledger["extra"]
    # make it overdue by returning nothing and shifting the date logic
    hammer_loan = next(
        l for l in service.doc["loans"] if not l.get("returned_on"))
    hammer_loan["due_on"] = "2026-10-01"
    overdue = service.ledger()
    assert "overdue" in overdue["extra"]


def test_borrower_with_odd_characters_is_just_text():
    service = make_toolbox()
    sneaky = 'Sam <script>alert("x")</script>'
    result = service.lend(item="Cordless drill", borrower=sneaky)
    assert result["loan"]["borrower"] == sneaky  # stored, never executed


# ----------------------------------------------------------- maintenance

def test_maintenance_nudges_sort_by_urgency():
    service = make_toolbox()
    nudges = service.maintenance_log()["nudges"]
    urgency_order = [n["urgency"] for n in nudges]
    assert urgency_order == sorted(
        urgency_order, key=lambda u: {"overdue": 0, "soon": 1, "later": 2}[u]
    )
    assert nudges[0]["urgency"] == "overdue"


def test_complete_task_moves_due_date_out():
    service = make_toolbox()
    result = service.complete_maintenance(task="m-001")
    assert result["task"]["last_done"] == "2026-10-31"
    fresh = service.maintenance_log()["nudges"]
    this_task = next(n for n in fresh if n["id"] == "m-001")
    assert this_task["urgency"] == "later"


def test_interval_must_be_positive():
    service = make_toolbox()
    with pytest.raises(ValueError, match="interval_days"):
        service.log_maintenance(item="Claw hammer", task="Sharpen",
                                interval_days=0)


def test_maintenance_without_item_is_allowed():
    service = make_toolbox()
    result = service.log_maintenance(task="Sweep the garage floor",
                                     interval_days=30)
    assert result["task"]["item_id"] == ""


# --------------------------------------------------------------- projects

def test_gap_list_builds_shopping_list():
    service = make_toolbox()
    gaps = service.gap_list(project="p-001")
    rows = {g["name"]: g for g in gaps["gaps"]}
    assert rows["Wood saw"]["have"] == 0 and rows["Wood saw"]["buy"] == 1
    assert rows["Wood glue"]["have"] == 1 and rows["Wood glue"]["buy"] == 0
    assert "Shopping list" in gaps["extra"]


def test_gap_list_when_everything_on_hand():
    service = make_toolbox()
    service.add_item(name="Wood saw", kind="tool")
    gaps = service.gap_list(project="p-001")
    assert "Good news" in gaps["extra"]


def test_project_qty_shortfall_counts():
    service = make_toolbox()
    service.add_project(name="Sandbox", needed=[{"name": "Wood glue", "qty": 4}])
    gaps = service.gap_list(project="Sandbox")
    assert gaps["gaps"][0]["buy"] == 3


def test_complete_project_status():
    service = make_toolbox()
    result = service.complete_project(project="p-001")
    assert result["project"]["status"] == "done"


# --------------------------------------------------------------- seasonal

def test_seasonal_prompt_by_month():
    service = make_toolbox()
    october = service.seasonal_prompt(month=10)
    assert october["season"] == "fall"
    assert october["jobs"] == svc.Toolbox.SEASON_JOBS[10]
    assert "Tune-up weekend" in october["extra"]


def test_seasonal_prompt_defaults_to_clock():
    service = make_toolbox()
    assert service.seasonal_prompt()["month"] == FIXED_TODAY.month


def test_seasonal_month_bounds():
    service = make_toolbox()
    with pytest.raises(ValueError, match="month"):
        service.seasonal_prompt(month=13)


def test_seasonal_log_round_trip():
    service = make_toolbox()
    logged = service.log_season_done(job="Tune-up weekend", month=10)
    history = service.season_history()
    assert len(history["log"]) == 1
    assert history["log"][0]["job"] == "Tune-up weekend"
    assert logged["entry"]["done_on"] == "2026-10-31"


# ---------------------------------------------------------------- summary

def test_summary_counts_https_free():
    service = make_toolbox()
    summary = service.summary()
    assert summary["out_on_loan"] == 1
    assert "in summary" not in json.dumps(summary)  # no surprise fields


def test_dates_round_trip_through_json():
    service = make_toolbox()
    text = json.dumps(service.doc)
    reloaded = json.loads(text)
    assert reloaded["loans"][0]["due_on"] == service.doc["loans"][0]["due_on"]
