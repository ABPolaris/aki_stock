"""AKShare 返回结构解析与异常翻译测试（不访问网络）。"""

from __future__ import annotations

import pytest

from aki_stock.models import QuoteUnavailableError, StockRef
from aki_stock.sources import (
    EastmoneySource,
    QuoteService,
    _as_float,
    _parse_em_index_row,
    _parse_em_item,
    _parse_xq_item,
    _translate_error,
    looks_like_token_error,
    validate_source,
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


OFILM = StockRef("002456", "欧菲光", "stock", "SZ")
SH_INDEX = StockRef("000001", "上证综指", "index", "SH")
BJ_INDEX = StockRef("899050", "北证50", "index", "BJ")


class InlineGate:
    async def run(self, func, *args, timeout=None):
        return func(*args)


def item_frame(**values) -> Frame:
    return Frame({"item": list(values), "value": list(values.values())})


def test_parse_eastmoney_item_quote():
    quote = _parse_em_item(item_frame(最新=5.78, 涨幅=1.76), OFILM)
    assert quote.price == 5.78
    assert quote.change_percent == 1.76
    assert quote.source == "eastmoney"


def test_parse_eastmoney_index_row():
    quote = _parse_em_index_row(
        {"代码": "000001", "名称": "上证指数", "最新价": 3689.91, "涨跌幅": 0.1},
        SH_INDEX,
    )
    assert quote.price == 3689.91
    assert quote.change_percent == 0.1
    assert quote.ref.name == "上证综指"


def test_parse_xueqiu_item_quote():
    quote = _parse_xq_item(
        item_frame(名称="欧菲光", 代码="SZ002456", 现价=5.78, 涨幅=1.76),
        OFILM,
    )
    assert quote.price == 5.78
    assert quote.change_percent == 1.76
    assert quote.source == "xueqiu"


def test_parse_xueqiu_fills_name_when_ref_name_is_code():
    quote = _parse_xq_item(
        item_frame(名称="欧菲光", 现价=5.78, 涨幅=1.76),
        StockRef("002456", "002456", "stock", "SZ"),
    )
    assert quote.ref.name == "欧菲光"


def test_parse_rejects_empty_quotes():
    with pytest.raises(QuoteUnavailableError):
        _parse_em_item(item_frame(最新="-", 涨幅="-"), OFILM)
    with pytest.raises(QuoteUnavailableError):
        _parse_xq_item(item_frame(现价=None, 涨幅=None), OFILM)


def test_as_float_handles_akshare_placeholders():
    assert _as_float("-") is None
    assert _as_float("") is None
    assert _as_float("1,234.5") == 1234.5
    assert _as_float(float("nan")) is None
    assert _as_float(0) == 0.0


def test_eastmoney_index_uses_main_board_then_falls_back():
    class FakeAk:
        def __init__(self):
            self.calls = []

        def stock_zh_index_spot_em(self, symbol):
            self.calls.append(symbol)
            if symbol == "沪深重要指数":
                return Frame(
                    {
                        "代码": ["000001"],
                        "名称": ["上证指数"],
                        "最新价": [3689.91],
                        "涨跌幅": [0.1],
                    }
                )
            return Frame(
                {
                    "代码": ["000300"],
                    "名称": ["沪深300"],
                    "最新价": [4000.0],
                    "涨跌幅": [0.5],
                }
            )

    ak = FakeAk()
    source = EastmoneySource()
    quote = source._fetch_index(ak, SH_INDEX)
    assert quote.price == 3689.91
    assert ak.calls == ["沪深重要指数"]

    index_300 = StockRef("000300", "沪深300", "index", "SH")
    quote_300 = source._fetch_index(ak, index_300)
    assert quote_300.price == 4000.0
    assert ak.calls == ["沪深重要指数", "中证系列指数"]


def test_eastmoney_index_falls_back_to_bid_ask_for_bj():
    class FakeAk:
        def __init__(self):
            self.calls = []

        def stock_zh_index_spot_em(self, symbol):
            self.calls.append(symbol)
            return Frame({"代码": [], "最新价": [], "涨跌幅": []})

        def stock_bid_ask_em(self, symbol):
            self.calls.append(symbol)
            return item_frame(最新=910.07, 涨幅=1.79)

    ak = FakeAk()
    source = EastmoneySource()
    quote = source._fetch_index(ak, BJ_INDEX)
    assert quote.price == 910.07
    assert ak.calls == ["沪深重要指数", "899050"]


def test_translate_rate_limit_and_token_errors():
    class Push2RateLimitError(Exception):
        pass

    class APIError(Exception):
        pass

    rate = _translate_error(Push2RateLimitError("当前 IP 被临时限流"), "eastmoney")
    assert "频繁" in str(rate)

    token = _translate_error(
        APIError("Xueqiu quote endpoint requires a valid xq_a_token 400016"),
        "xueqiu",
    )
    assert "令牌" in str(token)
    assert looks_like_token_error(APIError("400016 登录"))


def test_source_validation_and_switch():
    assert validate_source(" xueqiu ") == "xueqiu"
    with pytest.raises(ValueError):
        validate_source("sina")
    service = QuoteService("eastmoney", InlineGate())
    assert service.source_name == "eastmoney"
    service.set_source("xueqiu")
    assert service.source_name == "xueqiu"
    assert service.source_label == "雪球"
