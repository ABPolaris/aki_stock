"""命令层测试：输入反馈、自选股、批量间隔与命令注册。

全部使用合成 resolver / quotes / 临时数据目录，不访问 AKShare 或 QQ。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import aki_stock
from aki_stock import (
    _handle_overview,
    _handle_quote,
    _handle_watchlist,
    _handle_watchlist_quotes,
)
from aki_stock.config import Config
from aki_stock.models import (
    Quote,
    QuoteUnavailableError,
    StockNotFoundError,
    StockRef,
)
from aki_stock.resolver import normalize_name, parse_stock_token
from aki_stock.runtime import AkiStockConfigConsumer, AkiStockRuntime
from aki_stock.storage import WatchlistStore

TCL = StockRef("000100", "TCL科技", "stock", "SZ")
OFILM = StockRef("002456", "欧菲光", "stock", "SZ")
MAOTAI = StockRef("600519", "贵州茅台", "stock", "SH")
PINGAN = StockRef("000001", "平安银行", "stock", "SZ")
SH_INDEX = StockRef("000001", "上证综指", "index", "SH")
SZ_INDEX = StockRef("399001", "深证成指", "index", "SZ")
BJ_INDEX = StockRef("899050", "北证50", "index", "BJ")

PRICES = {
    "000100": (4.63, -1.07),
    "002456": (5.78, 1.76),
    "600519": (1452.0, 0.38),
    "000001": (10.0, -0.5),
    "399001": (11471.09, 1.19),
    "899050": (910.07, 1.79),
}


class FakeResolver:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.mapping = {
            "002456": OFILM,
            "欧菲光": OFILM,
            "000100": TCL,
            "tcl科技": TCL,
            "600519": MAOTAI,
            "贵州茅台": MAOTAI,
            "000001": PINGAN,
            "平安银行": PINGAN,
            "上证综指": SH_INDEX,
            "399001": SZ_INDEX,
            "899050": BJ_INDEX,
        }

    async def resolve(self, raw: str) -> StockRef:
        self.calls.append(raw)
        token = parse_stock_token(raw)
        key = token.code if token.code is not None else token.name
        if key in self.mapping:
            return self.mapping[key]
        key = normalize_name(key or "")
        if key in self.mapping:
            return self.mapping[key]
        raise StockNotFoundError("没有找到该股票名称，请检查后重试。")


class FakeQuotes:
    def __init__(self, *, fail_codes=()) -> None:
        self.calls: list[str] = []
        self.fail_codes = set(fail_codes)
        self.source_name = "xueqiu"

    async def fetch(self, ref: StockRef) -> Quote:
        self.calls.append(ref.code)
        if ref.code in self.fail_codes:
            raise QuoteUnavailableError(
                "雪球源访问令牌失效，请稍后再试或切换到东方财富源。"
            )
        price, change = PRICES.get(ref.code, (10.0, 0.0))
        amounts = {
            "000100": -0.05,
            "002456": 0.10,
            "000001": -0.05,
            "600519": 5.56,
            "399001": 135.40,
            "899050": 15.99,
        }
        return Quote(
            ref=ref,
            price=price,
            change_percent=change,
            source="xueqiu",
            change_amount=amounts.get(ref.code),
        )

    def set_source(self, source: str) -> None:
        self.source_name = source


class FakeSleep:
    def __init__(self) -> None:
        self.calls: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


class Ctx:
    def __init__(self, args, user_id: int = 10001) -> None:
        self.args = list(args)
        self.raw_args = " ".join(self.args)
        self.user_id = user_id


def build_runtime(tmp_path, *, limit: int = 2, fail_codes=()):
    resolver = FakeResolver()
    quotes = FakeQuotes(fail_codes=fail_codes)
    sleep = FakeSleep()
    runtime = AkiStockRuntime(
        Config(source="xueqiu", watchlist_limit=limit),
        tmp_path,
        resolver=resolver,
        quotes=quotes,
        watchlists=WatchlistStore(tmp_path / "watchlists.json"),
        sleep=sleep,
    )
    return runtime, resolver, quotes, sleep


@pytest.mark.asyncio
async def test_quote_success_format(tmp_path):
    runtime, _, quotes, _ = build_runtime(tmp_path)
    text = await _handle_quote(runtime, Ctx(["002456"]))
    assert "002456 欧菲光" in text
    assert "最新价：5.78 元" in text
    assert "涨跌额：+0.10 元" in text
    assert "涨跌幅：+1.76%" in text
    assert "数据源：雪球" in text
    assert quotes.calls == ["002456"]


@pytest.mark.asyncio
async def test_quote_by_name_and_index(tmp_path):
    runtime, _, _, _ = build_runtime(tmp_path)
    name_text = await _handle_quote(runtime, Ctx(["TCL科技"]))
    assert "000100 TCL科技" in name_text

    index_text = await _handle_quote(runtime, Ctx(["上证综指"]))
    assert "上证综指（000001）" in index_text
    assert "最新：10.00 点" in index_text
    assert "涨跌额：-0.05 点" in index_text
    assert "涨跌幅：-0.50%" in index_text


@pytest.mark.asyncio
async def test_missing_many_injection_and_unknown_stock_do_not_query(tmp_path):
    runtime, _, quotes, _ = build_runtime(tmp_path)

    assert "请提供股票代码或名称" in await _handle_quote(runtime, Ctx([]))
    assert "一次只能查询一只" in await _handle_quote(
        runtime, Ctx(["002456", "600519"])
    )
    assert "一次只能查询一只" in await _handle_quote(
        runtime, Ctx(["002456;rm -rf /"])
    )
    assert "无效字符" in await _handle_quote(runtime, Ctx(["<script>"]))
    assert "没有找到" in await _handle_quote(runtime, Ctx(["999999"]))
    assert quotes.calls == []


@pytest.mark.asyncio
async def test_watchlist_add_list_limit_duplicate_and_isolation(tmp_path):
    runtime, _, _, _ = build_runtime(tmp_path, limit=2)
    text = await _handle_watchlist(runtime, Ctx(["添加", "002456"], user_id=1))
    assert "已添加自选股：002456 欧菲光（1/2）" in text

    text = await _handle_watchlist(runtime, Ctx([], user_id=1))
    assert "自选股（1/2）" in text
    assert "1. 002456 欧菲光" in text

    text = await _handle_watchlist(runtime, Ctx(["添加", "002456"], user_id=1))
    assert "已在自选股中" in text

    await _handle_watchlist(runtime, Ctx(["添加", "600519"], user_id=1))
    text = await _handle_watchlist(runtime, Ctx(["添加", "899050"], user_id=1))
    assert "最多 2 只" in text

    # 用户隔离：另一个用户仍可添加同一只。
    text = await _handle_watchlist(runtime, Ctx(["添加", "002456"], user_id=2))
    assert "（1/2）" in text
    assert "自选股（2/2）" in await _handle_watchlist(runtime, Ctx([], user_id=1))


@pytest.mark.asyncio
async def test_watchlist_remove_by_name_and_missing(tmp_path):
    runtime, _, _, _ = build_runtime(tmp_path, limit=5)
    await _handle_watchlist(runtime, Ctx(["添加", "002456"]))
    text = await _handle_watchlist(runtime, Ctx(["删除", "欧菲光"]))
    assert "已删除自选股：002456 欧菲光（0/5）" in text
    assert "自选股列表为空" in await _handle_watchlist(runtime, Ctx([]))

    text = await _handle_watchlist(runtime, Ctx(["删除", "欧菲光"]))
    assert "不在自选股中" in text


@pytest.mark.asyncio
async def test_watchlist_help_and_argument_feedback(tmp_path):
    runtime, _, _, _ = build_runtime(tmp_path)
    assert "自选股用法" in await _handle_watchlist(runtime, Ctx(["帮助"]))
    assert "请提供要添加" in await _handle_watchlist(runtime, Ctx(["添加"]))
    assert "请提供要删除" in await _handle_watchlist(runtime, Ctx(["删除"]))
    assert "一次只能添加" in await _handle_watchlist(
        runtime, Ctx(["添加", "002456", "600519"])
    )
    assert "不需要股票参数" in await _handle_watchlist(
        runtime, Ctx(["列表", "002456"])
    )
    assert "未知操作" in await _handle_watchlist(runtime, Ctx(["乱写"]))


@pytest.mark.asyncio
async def test_watchlist_quotes_order_interval_and_partial_failure(tmp_path):
    runtime, _, quotes, sleep = build_runtime(tmp_path, limit=5)
    await _handle_watchlist(runtime, Ctx(["添加", "002456"]))
    await _handle_watchlist(runtime, Ctx(["添加", "600519"]))

    text = await _handle_watchlist_quotes(runtime, Ctx([]))
    assert "自选股行情（2/5） · 价格 | 涨跌额 | 涨跌幅" in text
    assert "1. 002456 欧菲光\n5.78 | +0.10 | +1.76%" in text
    assert "2. 600519 贵州茅台\n1452.00 | +5.56 | +0.38%" in text
    assert quotes.calls == ["002456", "600519"]
    assert sleep.calls == [1.0]

    quotes.fail_codes.add("600519")
    text = await _handle_watchlist_quotes(runtime, Ctx([]))
    assert "1. 002456 欧菲光\n5.78 |" in text
    assert "2. 600519 贵州茅台：查询失败（数据源鉴权失败）" in text

    assert "自选股列表为空" in await _handle_watchlist_quotes(
        runtime, Ctx([], user_id=999)
    )
    assert "不接受参数" in await _handle_watchlist_quotes(
        runtime, Ctx(["002456"])
    )


@pytest.mark.asyncio
async def test_overview_order_and_interval(tmp_path):
    runtime, _, quotes, sleep = build_runtime(tmp_path)
    text = await _handle_overview(runtime, Ctx([]))
    assert "A股概况 · 价格 | 涨跌额 | 涨跌幅" in text
    assert "1. 上证综指（000001）\n10.00 | -0.05 | -0.50%" in text
    assert "2. 深证成指（399001）\n11471.09 | +135.40 | +1.19%" in text
    assert "3. 北证50（899050）\n910.07 | +15.99 | +1.79%" in text
    assert quotes.calls == ["000001", "399001", "899050"]
    assert sleep.calls == [1.0, 1.0]
    assert "不接受参数" in await _handle_overview(runtime, Ctx(["002456"]))


@pytest.mark.asyncio
async def test_config_consumer_applies_source_and_limit(tmp_path):
    runtime, _, quotes, _ = build_runtime(tmp_path, limit=2)
    consumer = AkiStockConfigConsumer(runtime, "aki_stock")
    assert consumer.config_paths == ("plugin.aki_stock",)
    assert {rule.path for rule in consumer.hot_reload_policies} == {
        "plugin.aki_stock.source",
        "plugin.aki_stock.watchlist_limit",
    }

    await consumer.apply_config({"source": "eastmoney", "watchlist_limit": 7})
    assert runtime.config.source == "eastmoney"
    assert runtime.config.watchlist_limit == 7
    assert quotes.source_name == "eastmoney"


@pytest.mark.asyncio
async def test_command_registration_and_handler_dispatch(tmp_path):
    runtime, _, _, _ = build_runtime(tmp_path)

    class FakeRegistrar:
        available = True

        def __init__(self):
            self.commands = {}

        def register(self, name, **kwargs):
            def decorate(handler):
                self.commands[name] = {"handler": handler, **kwargs}
                return handler

            return decorate

    registrar = FakeRegistrar()
    ctx = SimpleNamespace(app_commands=registrar, logger=None)
    aki_stock._set_runtime(runtime)
    try:
        aki_stock._register_commands(ctx)
        assert set(registrar.commands) == {
            "行情",
            "自选股",
            "自选股信息",
            "A股概况",
        }
        assert registrar.commands["行情"]["aliases"] == ("股价", "quote")
        text = await registrar.commands["行情"]["handler"](Ctx(["002456"]))
        assert "002456 欧菲光" in text
    finally:
        aki_stock._set_runtime(None)


def test_plugin_surface_has_config_and_guide():
    document = aki_stock.plugin.surface.describe(aki_stock.plugin)
    source = document["config"]["properties"]["source"]
    assert source["enum"] == ["eastmoney", "xueqiu"]
    assert document["guides"][0]["id"] == "first-use"
