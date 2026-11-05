"""自选股持久化测试：隔离、上限、原子写入与损坏恢复。"""

from __future__ import annotations

import json

import pytest

from aki_stock.models import (
    StockRef,
    WatchlistDuplicateError,
    WatchlistLimitError,
)
from aki_stock.storage import WatchlistStore

TCL = StockRef("000100", "TCL科技", "stock", "SZ")
MAOTAI = StockRef("600519", "贵州茅台", "stock", "SH")
INDEX = StockRef("399001", "深证成指", "index", "SZ")


def make_store(tmp_path, limit=2):
    return WatchlistStore(tmp_path / "watchlists.json")


@pytest.mark.asyncio
async def test_add_list_find_remove_and_reload(tmp_path):
    store = make_store(tmp_path)
    assert await store.add(10001, TCL, 2) == 1
    assert await store.add(10001, MAOTAI, 2) == 2

    items = await store.items(10001)
    assert [item.code for item in items] == ["000100", "600519"]
    assert (await store.find(10001, "TCL科技")).code == "000100"
    assert (await store.find(10001, "600519")).name == "贵州茅台"

    removed = await store.remove(10001, "000100")
    assert removed is not None and removed.name == "TCL科技"
    assert [item.code for item in await store.items(10001)] == ["600519"]

    reloaded = WatchlistStore(tmp_path / "watchlists.json")
    assert [item.code for item in await reloaded.items(10001)] == ["600519"]


@pytest.mark.asyncio
async def test_limit_duplicate_and_user_isolation(tmp_path):
    store = make_store(tmp_path)
    await store.add(1, TCL, 2)
    await store.add(1, MAOTAI, 2)

    with pytest.raises(WatchlistDuplicateError):
        await store.add(1, TCL, 2)
    with pytest.raises(WatchlistLimitError):
        await store.add(1, INDEX, 2)

    assert await store.add(2, TCL, 2) == 1
    assert [item.code for item in await store.items(2)] == ["000100"]
    assert [item.code for item in await store.items(1)] == ["000100", "600519"]


@pytest.mark.asyncio
async def test_index_entry_round_trips(tmp_path):
    store = make_store(tmp_path)
    await store.add(1, INDEX, 5)
    item = (await store.items(1))[0]
    assert item.kind == "index"
    assert item.market == "SZ"
    assert item.as_ref().name == "深证成指"


@pytest.mark.asyncio
async def test_corrupt_file_is_quarantined(tmp_path):
    path = tmp_path / "watchlists.json"
    path.write_text("{not json", encoding="utf-8")
    store = WatchlistStore(path)
    assert await store.items(1) == []
    assert list(tmp_path.glob("watchlists.json.corrupt-*"))
    # 隔离后仍可正常写入新数据。
    assert await store.add(1, TCL, 2) == 1
    assert json.loads(path.read_text(encoding="utf-8"))["users"]["1"][0]["code"] == "000100"
