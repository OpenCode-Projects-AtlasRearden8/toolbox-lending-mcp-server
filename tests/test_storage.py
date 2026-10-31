"""Storage behavior: persistence, backups, crash recovery."""
import json

from toolbox_mcp.storage import JsonStore


def test_creates_and_roundtrips(data_dir):
    store = JsonStore("toolbox.json", base_dir=data_dir,
                      clock=lambda: "stamp")
    store.save({"hello": "toolbox"})
    again = JsonStore("toolbox.json", base_dir=data_dir)
    assert again.load() == {"hello": "toolbox"}


def test_load_default_when_missing(data_dir):
    store = JsonStore("toolbox.json", base_dir=data_dir)
    assert store.load(default={"x": 1}) == {"x": 1}


def test_backup_made_before_write(data_dir):
    store = JsonStore("toolbox.json", base_dir=data_dir,
                      clock=lambda: "20261031-000000-000000")
    store.save({"v": 1})
    store.save({"v": 2})
    backups = store.backup_files
    assert backups, "expected an automatic backup"
    assert json.loads(backups[-1].read_text()) == {"v": 1}


def test_backup_pruning_keeps_newest(data_dir):
    store = JsonStore("toolbox.json", base_dir=data_dir, max_backups=3)
    # each save backups up the *previous* contents
    for i in range(5):
        store._stamp = (lambda n=i: f"s{n:03d}")
        store.save({"v": i})
    assert len(store.backup_files) == 3
    assert json.loads(store.backup_files[-1].read_text())["v"] == 3


def test_corrupt_store_recovers_from_backup(data_dir):
    store = JsonStore("toolbox.json", base_dir=data_dir,
                      clock=lambda: "bak1")
    store.save({"v": 1})   # had no backup yet (file was new)
    store.save({"v": 42})  # also backs up the previous version {"v": 1}
    store.path.write_text("{ not json ]")
    recovered = store.load(default=None)
    # newest surviving backup is the pre-corruption copy {"v": 1}
    assert recovered == {"v": 1}


def test_corrupt_store_without_backup_returns_default(data_dir):
    store = JsonStore("toolbox.json", base_dir=data_dir)
    store.path.write_text("garbage")
    assert store.load(default="fresh") == "fresh"
