"""插件内部的数据模型与用户可见异常。

这里不依赖 AKShare、NeoBot 或 pandas，方便业务层与测试层单独使用。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Market = Literal["SH", "SZ", "BJ"]
Kind = Literal["stock", "index"]


@dataclass(frozen=True, slots=True)
class StockRef:
    """已经通过白名单解析的股票 / 指数引用。

    ``code`` 一定是 6 位数字，``market`` 一定来自本插件计算，
    不会把用户原始输入直接交给数据源。
    """

    code: str
    name: str
    kind: Kind = "stock"
    market: Market = "SH"

    @property
    def label(self) -> str:
        if self.kind == "index":
            return f"{self.name}（{self.code}）" if self.name else self.code
        name = (self.name or "").strip()
        return f"{self.code} {name}" if name and name != self.code else self.code


@dataclass(frozen=True, slots=True)
class Quote:
    """一次行情查询的结果；未知值用 ``None`` 表示，而不是伪造 0。"""

    ref: StockRef
    price: float | None
    change_percent: float | None
    source: str = ""
    as_of: str | None = None


@dataclass(frozen=True, slots=True)
class QuoteOutcome:
    """批量查询中单只股票的成败结果，失败不会中断其余查询。"""

    ref: StockRef
    quote: Quote | None = None
    error: str = ""

    @property
    def ok(self) -> bool:
        return self.quote is not None


@dataclass(frozen=True, slots=True)
class WatchlistEntry:
    """自选股持久化条目；保存名称是为了在行情源不可用时仍能显示列表。"""

    code: str
    name: str
    kind: Kind = "stock"
    market: Market = "SH"

    def as_ref(self) -> StockRef:
        return StockRef(
            code=self.code,
            name=self.name,
            kind=self.kind,
            market=self.market,
        )


class UserInputError(ValueError):
    """用户输入不合法；消息可以直接回复给用户。"""


class MissingStockError(UserInputError):
    """缺少股票代码或名称。"""


class TooManyStocksError(UserInputError):
    """一次给了多个股票。"""


class StockNotFoundError(UserInputError):
    """代码或名称无法解析成已上市的 A 股 / 指数。"""


class QuoteUnavailableError(RuntimeError):
    """行情源暂时不可用；消息可以直接回复给用户。"""


class WatchlistLimitError(ValueError):
    """自选股数量达到上限。"""


class WatchlistDuplicateError(ValueError):
    """自选股已经存在。"""


class WatchlistMissingError(ValueError):
    """自选股不在列表中。"""
