"""A 股概况：上证综指、深证成指、北证 50。

“北证指数”在公开行情里通常指北证 50（899050）；这里固定查询该指数，
并沿用 :mod:`quote` 的批量间隔，避免三只指数连续打满数据源。
"""

from __future__ import annotations

from typing import Any

from . import quote
from .resolver import index_definitions


async def query_all(runtime: Any):
    return await quote.fetch_many(runtime, index_definitions())
