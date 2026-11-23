"""个股查询与批量查询的公共流程。

``quote.py`` 负责“单只股票怎么查”，批量循环只在这里实现一次：
自选股信息与 A 股概况都复用它，从而保证 1 秒间隔、失败不中断等行为一致。
所有命令共用同一个总超时预算，避免单次或批量查询无限等待。
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

#: 单次命令的总超时预算（单只和批量都使用这个总时长）。
COMMAND_BUDGET_SECONDS = 20.0
#: 批量查询之间的固定间隔；聊天场景下既避免刷屏，也降低数据源限流概率。
BATCH_INTERVAL_SECONDS = 1.0
#: 命令先等一小段时间；查得快就直接返回结果，查得慢再交给后台任务。
BACKGROUND_GRACE_SECONDS = 1.5


async def query_one(
    runtime: Any,
    raw: str,
    *,
    budget: float = COMMAND_BUDGET_SECONDS,
) -> Quote:
    """查询单只股票，并把名称解析和行情请求都计入同一个预算。"""

    loop = asyncio.get_running_loop()
    deadline = loop.time() + budget
    try:
        async with asyncio.timeout(budget):
            ref = await runtime.resolver.resolve(raw)
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise QuoteUnavailableError(
                    f"查询超过 {budget:g} 秒预算，请稍后再试。"
                )
            return await query_ref(runtime, ref, timeout=remaining)
    except TimeoutError as exc:
        raise QuoteUnavailableError(
            f"查询超过 {budget:g} 秒预算，请稍后再试。"
        ) from exc


async def query_ref(
    runtime: Any,
    ref: StockRef,
    *,
    timeout: float | None = None,
) -> Quote:
    return await runtime.quotes.fetch(ref, timeout=timeout)


async def fetch_many(
    runtime: Any,
    refs: Sequence[StockRef],
    *,
    interval: float = BATCH_INTERVAL_SECONDS,
    budget: float = COMMAND_BUDGET_SECONDS,
) -> list[QuoteOutcome]:
    """依次查询，单只失败只记录该只的错误，不阻断后续股票。

    整个循环共享 ``budget`` 秒预算；预算耗尽后剩余股票不再发起请求，
    直接标记为“预算已用完”，避免一次命令拖住事件管线过久。
    """

    outcomes: list[QuoteOutcome] = []
    loop = asyncio.get_running_loop()
    deadline = loop.time() + budget
    total = len(refs)
    for index, ref in enumerate(refs):
        remaining = deadline - loop.time()
        if remaining <= 0.05:
            outcomes.append(
                QuoteOutcome(
                    ref=ref,
                    error=f"查询超过 {budget:g} 秒预算，已跳过",
                )
            )
            continue
        try:
            quote = await query_ref(runtime, ref, timeout=remaining)
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
            remaining = deadline - loop.time()
            if remaining > 0:
                await runtime.sleep(min(interval, remaining))
    return outcomes
