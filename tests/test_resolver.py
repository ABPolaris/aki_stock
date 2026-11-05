"""输入校验与名称解析的离线测试。"""

from __future__ import annotations

import pytest

from aki_stock.models import (
    MissingStockError,
    QuoteUnavailableError,
    StockNotFoundError,
    TooManyStocksError,
    UserInputError,
)
from aki_stock.resolver import (
    StockResolver,
    normalize_name,
    parse_stock_token,
)


class Frame:
    """测试用的最小 DataFrame 替身，避免单元测试依赖 pandas / AKShare。"""

    def __init__(self, data):
        self.columns = tuple(data)
        values = list(data.values())
        self._rows = [dict(zip(self.columns, row)) for row in zip(*values)] if values else []

    def __len__(self):
        return len(self._rows)

    def __getitem__(self, key):
        return [row[key] for row in self._rows]

    def to_dict(self, orient="records"):
        if orient != "records":
            raise ValueError(orient)
        return [dict(row) for row in self._rows]




class InlineGate:
    async def run(self, func, *args, timeout=None):
        return func(*args)


def stock_frame() -> Frame:
    return Frame(
        {
            "code": ["000001", "000100", "002456", "600519", "430139"],
            "name": ["平安银行", "TCL科技", "欧菲光", "贵州茅台", "诺思兰德"],
        }
    )


def make_resolver(loader=None, calls=None):
    if loader is None:
        def loader():
            if calls is not None:
                calls.append(1)
            return stock_frame()

    return StockResolver(InlineGate(), loader=loader)


def test_parse_code_with_and_without_market_prefix():
    token = parse_stock_token("002456")
    assert token.code == "002456"
    assert token.market == "SZ"

    token = parse_stock_token("SH600519")
    assert token.code == "600519"
    assert token.market == "SH"

    token = parse_stock_token("bj430139")
    assert token.code == "430139"
    assert token.market == "BJ"


def test_parse_name_normalizes_width_and_spaces():
    assert parse_stock_token("TCL科技").name == "tcl科技"
    assert parse_stock_token("万  科Ａ").name == "万科a"
    assert parse_stock_token("*ST美丽").name == "*st美丽"


@pytest.mark.parametrize(
    "value",
    [
        "002456,000001",
        "002456、000001",
        "002456; rm -rf /",
        "002456/000001",
        "002456|000001",
    ],
)
def test_parse_rejects_multiple_or_injected_input(value):
    with pytest.raises(TooManyStocksError):
        parse_stock_token(value)


@pytest.mark.parametrize(
    "value",
    [
        "00245",
        "0024567",
        "SH002456",
        "002456<script>",
        "@all",
        "002456\n000001",
        "002456\u0000",
    ],
)
def test_parse_rejects_invalid_codes_and_characters(value):
    with pytest.raises(UserInputError):
        parse_stock_token(value)


def test_parse_rejects_blank_and_too_long():
    with pytest.raises(MissingStockError):
        parse_stock_token("   ")
    with pytest.raises(UserInputError):
        parse_stock_token("股票" * 40)


@pytest.mark.asyncio
async def test_code_000001_prefers_stock_but_name_prefers_index():
    resolver = make_resolver()
    stock = await resolver.resolve("000001")
    assert stock.kind == "stock"
    assert stock.name == "平安银行"
    assert stock.market == "SZ"

    index = await resolver.resolve("上证综指")
    assert index.kind == "index"
    assert index.code == "000001"
    assert index.market == "SH"


@pytest.mark.asyncio
async def test_index_codes_and_aliases():
    resolver = make_resolver()
    assert (await resolver.resolve("399001")).kind == "index"
    assert (await resolver.resolve("深证成指")).code == "399001"
    assert (await resolver.resolve("北证指数")).code == "899050"
    assert (await resolver.resolve("创业板")).code == "399006"


@pytest.mark.asyncio
async def test_name_lookup_is_case_insensitive_and_cached():
    calls: list[int] = []
    resolver = make_resolver(calls=calls)
    assert (await resolver.resolve("tcl科技")).code == "000100"
    assert (await resolver.resolve("贵州茅台")).code == "600519"
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_unknown_name_and_code_are_rejected():
    resolver = make_resolver()
    with pytest.raises(StockNotFoundError):
        await resolver.resolve("不存在的股票")
    with pytest.raises(StockNotFoundError):
        await resolver.resolve("999999")


@pytest.mark.asyncio
async def test_name_map_failure_falls_back_to_code_query():
    def failing_loader():
        raise RuntimeError("network down")

    resolver = make_resolver(loader=failing_loader)
    with pytest.raises(QuoteUnavailableError):
        await resolver.resolve("TCL科技")

    # 名称表不可用时，6 位代码仍可尝试查询，名称暂时用代码代替。
    ref = await resolver.resolve("600519")
    assert ref.code == "600519"
    assert ref.name == "600519"


def test_normalize_name_removes_spaces_and_folds_case():
    assert normalize_name(" 万  科Ａ ") == "万科a"


def test_stock_label_does_not_repeat_code_as_name():
    from aki_stock.models import StockRef

    assert StockRef("600519", "600519", "stock", "SH").label == "600519"
    assert StockRef("000100", "TCL科技", "stock", "SZ").label == "000100 TCL科技"
