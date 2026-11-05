"""个股查询与批量查询的公共流程。

``quote.py`` 负责“单只股票怎么查”，批量循环只在这里实现一次：
自选股信息与 A 股概况都复用它，从而保证 1 秒间隔、失败不中断等行为一致。
以后给个股增加 K 线、资金流等能力时，也优先扩展本模块，而不是复制命令逻辑。
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Any

from .models import (
    Quote,
    QuoteOutcome,
    QuoteUnavailableError,
    StockRef,
    UserInputError,
)

#: 批量查询之间的固定间隔；聊天场景下既避免刷屏，也降低数据源限流概率。
BATCH_INTERVAL_SECONDS = 1.0


async def query_one(runtime: Any, raw: str) -> Quote:
    ref = await runtime.resolver.resolve(raw)
    return await runtime.quotes.fetch(ref)


async def query_ref(runtime: Any, ref: StockRef) -> Quote:
    return await runtime.quotes.fetch(ref)


async def fetch_many(
    runtime: Any,
    refs: Sequence[StockRef],
    *,
    interval: float = BATCH_INTERVAL_SECONDS,
) -> list[QuoteOutcome]:
    """依次查询，单只失败只记录该只的错误，不阻断后续股票。"""

    outcomes: list[QuoteOutcome] = []
    total = len(refs)
    for index, ref in enumerate(refs):
        try:
            quote = await query_ref(runtime, ref)
            outcomes.append(QuoteOutcome(ref=ref, quote=quote))
        except asyncio.CancelledError:
            raise
        except (QuoteUnavailableError, UserInputError) as exc:
            runtime.log_warning(f"批量查询 {ref.code} 失败: {type(exc).__name__}: {exc}")
            outcomes.append(QuoteOutcome(ref=ref, error=str(exc)))
        except Exception as exc:  # pragma: no cover - 数据源已做异常翻译
            runtime.log_warning(f"批量查询 {ref.code} 异常: {type(exc).__name__}: {exc}")
            outcomes.append(QuoteOutcome(ref=ref, error="查询失败"))
        if index < total - 1 and interval > 0:
            await runtime.sleep(interval)
    return outcomes
