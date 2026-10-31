"""Shared test fixtures."""
from datetime import date
from pathlib import Path

import pytest

from toolbox_mcp.server import build_service
from toolbox_mcp.storage import JsonStore

FIXED_TODAY = date(2026, 10, 31)


@pytest.fixture()
def data_dir(tmp_path: Path) -> Path:
    """An empty directory the store is allowed to live in.

    Deliberately inside tmp_path so tests can also place files *outside*
    the pen and check they are never touched.
    """
    data = tmp_path / "data"
    data.mkdir()
    (data / "backups").mkdir()
    return data


@pytest.fixture()
def store(data_dir: Path) -> JsonStore:
    return JsonStore(
        "toolbox.json", base_dir=data_dir, clock=lambda: "20261031-000000"
    )


@pytest.fixture()
def service(data_dir: Path):
    return build_service(
        "toolbox.json", base_dir=str(data_dir), clock=lambda: FIXED_TODAY
    )
