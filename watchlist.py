"""自选股业务：添加、删除、列表与批量行情。

持久化和锁在 :mod:`storage`；本模块只负责把用户文本解析成受信任引用，
再调用 :mod:`quote` 的统一批量查询流程。
"""

from __future__ import annotations

from typing import Any

from . import quote
from .models import (
    StockRef,
    WatchlistEntry,
    WatchlistMissingError,
)


async def add(runtime: Any, user_id: int, raw: str) -> tuple[StockRef, int]:
    ref = await runtime.resolver.resolve(raw)
    count = await runtime.watchlists.add(
        user_id, ref, runtime.config.watchlist_limit
    )
    return ref, count


async def remove(runtime: Any, user_id: int, raw: str) -> tuple[WatchlistEntry, int]:
    entry = await runtime.watchlists.find(user_id, raw)
    if entry is None:
        # 先解析一次，用于区分“没有这只股票”和“股票存在但不在列表里”。
        ref = await runtime.resolver.resolve(raw)
        raise WatchlistMissingError(f"{ref.label} 不在自选股中。")
    removed = await runtime.watchlists.remove(user_id, entry.code)
    if removed is None:  # 并发删除时给出明确结果，而不是静默成功
        raise WatchlistMissingError(f"{entry.as_ref().label} 不在自选股中。")
    items = await runtime.watchlists.items(user_id)
    return removed, len(items)


async def list_items(runtime: Any, user_id: int) -> list[WatchlistEntry]:
    return await runtime.watchlists.items(user_id)


async def query_all(
    runtime: Any,
    user_id: int,
    *,
    items: list[WatchlistEntry] | None = None,
):
    entries = items if items is not None else await list_items(runtime, user_id)
    refs = [entry.as_ref() for entry in entries]
    return await quote.fetch_many(runtime, refs)
