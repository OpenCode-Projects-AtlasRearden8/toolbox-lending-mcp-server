"""Safety cases: the store refuses to touch anything outside its pen.

A hostile or confused client should be able to say nothing that makes
this server read or overwrite a file outside its data directory.
"""
import pytest

from toolbox_mcp.storage import JsonStore, UnsafePathError

@pytest.mark.parametrize("evil", [
    "../escape.json",
    "../../etc/passwd.json",
    "sub/../../../../outside.json",
    ".json",
    "no-extension.txt",
    "bad*name.json",
    "empty/.json",
])
def test_rejects_traversal_and_bad_names(data_dir, evil):
    with pytest.raises(UnsafePathError):
        JsonStore(evil, base_dir=data_dir)


def test_rejects_absolute_path_outside(data_dir):
    with pytest.raises(UnsafePathError):
        JsonStore("/etc/toolbox-secrets.json", base_dir=data_dir)


def test_rejects_windows_style_escape(data_dir):
    with pytest.raises(UnsafePathError):
        JsonStore("..\\..\\win32.json", base_dir=data_dir)


def test_rejects_store_inside_backups_dir(data_dir):
    with pytest.raises(UnsafePathError):
        JsonStore("backups/toolbox.json", base_dir=data_dir)


def test_rejects_store_deep_inside_backups_tree(data_dir):
    with pytest.raises(UnsafePathError):
        JsonStore("backups/2026/toolbox.json", base_dir=data_dir)


def test_symlink_escape_is_caught(data_dir, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir(exist_ok=True)
    target = outside / "victim.json"
    target.write_text('{"do": "not touch"}')
    link = data_dir / "link.json"
    link.symlink_to(target)
    with pytest.raises(UnsafePathError):
        JsonStore("link.json", base_dir=data_dir)
    assert target.read_text() == '{"do": "not touch"}'


def test_base_dir_itself_is_resolved(data_dir):
    # A base dir with a symlinked component still confines the store.
    store = JsonStore("toolbox.json", base_dir=str(data_dir) + "/.")
    store.save({"ok": True})
    assert (data_dir / "toolbox.json").exists()


def test_never_fails_on_missing_parents(data_dir):
    store = JsonStore("sub/dir/toolbox.json", base_dir=data_dir)
    store.save({"ok": True})
    assert (data_dir / "sub" / "dir" / "toolbox.json").exists()
