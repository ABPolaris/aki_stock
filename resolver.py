"""用户输入校验，以及“代码 / 名称 → 受信任的股票引用”的解析。

安全边界：

* 用户输入先经过长度、字符集和分隔符检查；
* 名称只在 AKShare 返回的沪深京 A 股白名单里精确匹配；
* 最终交给数据源的永远是本模块计算的 6 位代码与市场标识，
  不会把用户原文拼进 URL、SQL、shell 或文件路径。
"""

from __future__ import annotations

import asyncio
import re
import time
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .models import (
    Market,
    MissingStockError,
    QuoteUnavailableError,
    StockNotFoundError,
    StockRef,
    TooManyStocksError,
    UserInputError,
)
from .sources import AkshareGate

_INDEX_ALIASES: dict[str, tuple[str, ...]] = {
    "000001": ("上证指数", "沪指", "上证"),
    "399001": ("深成指", "深指"),
    "399006": ("创业板", "创业板指数"),
    "000688": ("科创板50", "科创五十"),
    "000300": ("沪深三百",),
    "899050": ("北证指数", "北证", "北证五十"),
}
_INDEX_NAMES: dict[str, str] = {
    "000001": "上证综指",
    "399001": "深证成指",
    "399006": "创业板指",
    "000688": "科创50",
    "000300": "沪深300",
    "000016": "上证50",
    "000905": "中证500",
    "000852": "中证1000",
    "899050": "北证50",
}
_INDEX_MARKETS: dict[str, Market] = {
    "000001": "SH",
    "399001": "SZ",
    "399006": "SZ",
    "000688": "SH",
    "000300": "SH",
    "000016": "SH",
    "000905": "SH",
    "000852": "SH",
    "899050": "BJ",
}
#: 000001 同时是平安银行和上证综指；按用户要求“优先 A 股”，代码形式默认股票。
_AMBIGUOUS_INDEX_CODES = frozenset({"000001"})
_INDEX_BY_NAME: dict[str, str] = {}
for _code, _name in _INDEX_NAMES.items():
    _INDEX_BY_NAME[unicodedata.normalize("NFKC", _name).casefold()] = _code
    for _alias in _INDEX_ALIASES.get(_code, ()):
        _INDEX_BY_NAME[unicodedata.normalize("NFKC", _alias).casefold()] = _code

_ALLOWED_NAME_CHARS = frozenset("*.-+")
_SEPARATORS = frozenset(",，、;；/|\\")
_CONTROL_PREFIX = "C"
_MAX_RAW_LENGTH = 64
_MAX_NAME_LENGTH = 32

_CODE_RE = re.compile(r"(?:(SH|SZ|BJ))?(\d{6})", re.IGNORECASE)
_DIGITS_RE = re.compile(r"\d+")


@dataclass(frozen=True, slots=True)
class ParsedToken:
    code: str | None = None
    name: str | None = None
    market: Market | None = None


@dataclass(frozen=True, slots=True)
class StockUniverse:
    name_to_code: dict[str, str]
    code_to_name: dict[str, str]
    loaded_at: float


def normalize_name(value: str) -> str:
    """名称匹配键：全角转半角、去掉所有空白、忽略大小写。"""

    text = unicodedata.normalize("NFKC", str(value or ""))
    return "".join(char for char in text if not char.isspace()).casefold()


def clean_display_name(value: str) -> str:
    """展示名：全角转半角，保留单词之间的一个空格。"""

    text = unicodedata.normalize("NFKC", str(value or ""))
    return " ".join(text.split())


def market_for_code(code: str) -> Market:
    if code.startswith(("4", "8", "92")):
        return "BJ"
    if code.startswith("6"):
        return "SH"
    if code.startswith(("0", "3")):
        return "SZ"
    if code.startswith("899"):
        return "BJ"
    return "SH"


def is_index_code(code: str) -> bool:
    return code in _INDEX_NAMES and code not in _AMBIGUOUS_INDEX_CODES


def index_ref(code: str) -> StockRef:
    return StockRef(
        code=code,
        name=_INDEX_NAMES[code],
        kind="index",
        market=_INDEX_MARKETS[code],
    )


def index_definitions() -> tuple[StockRef, ...]:
    """A 股概况使用的固定顺序；名称固定，避免行情源返回不同别名。"""

    return tuple(
        index_ref(code) for code in ("000001", "399001", "899050")
    )


def parse_stock_token(raw: str | None) -> ParsedToken:
    """校验单个用户参数，返回代码或规范化名称。

    多股票检查由命令层按 ``args`` 长度完成；本函数还会拦截逗号、分号等
    显式分隔符，防止 ``002456,000001`` 这类输入混进数据源。
    """

    if raw is None:
        raise MissingStockError("请提供股票代码或名称，例如：/行情 002456 或 /行情 TCL科技。")
    text = unicodedata.normalize("NFKC", str(raw)).strip()
    if not text:
        raise MissingStockError("请提供股票代码或名称，例如：/行情 002456 或 /行情 TCL科技。")
    if len(text) > _MAX_RAW_LENGTH:
        raise UserInputError("输入过长，请提供 6 位股票代码或股票名称。")

    for char in text:
        if unicodedata.category(char).startswith(_CONTROL_PREFIX):
            raise UserInputError("输入包含无效控制字符，请重新输入。")
        if char in _SEPARATORS:
            raise TooManyStocksError("一次只能查询一只股票，请只提供一个代码或名称。")

    match = _CODE_RE.fullmatch(text)
    if match is not None:
        prefix = (match.group(1) or "").upper()
        code = match.group(2)
        market = market_for_code(code)
        if prefix and prefix != market:
            raise UserInputError(f"代码 {code} 属于 {market} 市场，与前缀 {prefix} 不一致。")
        return ParsedToken(code=code, market=market)

    if _DIGITS_RE.fullmatch(text):
        raise UserInputError("股票代码应为 6 位数字，例如：002456。")

    for char in text:
        if char.isspace() or char.isalnum() or char in _ALLOWED_NAME_CHARS:
            continue
        raise UserInputError("股票代码或名称包含无效字符，请重新输入。")

    normalized = normalize_name(text)
    if not normalized:
        raise MissingStockError("请提供股票代码或名称，例如：/行情 002456 或 /行情 TCL科技。")
    if len(normalized) > _MAX_NAME_LENGTH:
        raise UserInputError("股票名称过长，请检查后重新输入。")
    return ParsedToken(name=normalized)


def _load_a_code_name() -> Any:
    try:
        import akshare as ak
    except ImportError as exc:  # pragma: no cover - 安装完整时不会走到
        raise QuoteUnavailableError(
            "插件缺少 akshare 依赖，请在 NeoBot 插件管理中重新安装本插件。"
        ) from exc
    return ak.stock_info_a_code_name()


class StockResolver:
    """代码 / 名称解析器，带 1 小时名称表缓存和失败冷却。"""

    def __init__(
        self,
        gate: AkshareGate,
        *,
        loader: Callable[[], Any] | None = None,
        logger: Any | None = None,
        ttl: float = 3600.0,
        error_cooldown: float = 30.0,
        refresh_after: float = 300.0,
    ) -> None:
        self._gate = gate
        self._loader = loader or _load_a_code_name
        self._logger = logger
        self._ttl = float(ttl)
        self._error_cooldown = float(error_cooldown)
        self._refresh_after = float(refresh_after)
        self._cache: StockUniverse | None = None
        self._last_error_at = 0.0
        self._lock = asyncio.Lock()

    async def resolve(self, raw: str | None) -> StockRef:
        token = parse_stock_token(raw)
        if token.code is not None:
            return await self._resolve_code(token.code)
        assert token.name is not None
        return await self._resolve_name(token.name)

    async def _resolve_code(self, code: str) -> StockRef:
        if is_index_code(code):
            return index_ref(code)
        universe = await self._universe(allow_failure=True)
        if universe is not None:
            name = universe.code_to_name.get(code)
            if name is None:
                if code in _INDEX_NAMES:
                    return index_ref(code)
                raise StockNotFoundError(
                    f"没有找到代码 {code} 对应的股票，请检查后重试。"
                )
        else:
            name = code
        return StockRef(code=code, name=name, kind="stock", market=market_for_code(code))

    async def _resolve_name(self, name: str) -> StockRef:
        index_code = _INDEX_BY_NAME.get(name)
        if index_code is not None:
            return index_ref(index_code)
        universe = await self._universe()
        code = universe.name_to_code.get(name)
        if code is None and time.monotonic() - universe.loaded_at > self._refresh_after:
            # 名称表可能缓存了上一交易日的数据；只在缓存较旧时刷新一次，
            # 避免用户反复输错名称时每次都重新拉取 5000 多行列表。
            universe = await self._universe(force=True)
            code = universe.name_to_code.get(name)
        if code is None:
            raise StockNotFoundError("没有找到该股票名称，请检查后重试。")
        return StockRef(
            code=code,
            name=universe.code_to_name.get(code, name),
            kind="stock",
            market=market_for_code(code),
        )

    async def _universe(
        self,
        *,
        allow_failure: bool = False,
        force: bool = False,
    ) -> StockUniverse | None:
        now = time.monotonic()
        if not force and self._cache is not None and now - self._cache.loaded_at < self._ttl:
            return self._cache
        if (
            not force
            and self._cache is None
            and self._last_error_at
            and now - self._last_error_at < self._error_cooldown
        ):
            if allow_failure:
                return None
            raise QuoteUnavailableError("股票名称列表暂时不可用，请稍后再试或改用 6 位代码。")

        async with self._lock:
            now = time.monotonic()
            if not force and self._cache is not None and now - self._cache.loaded_at < self._ttl:
                return self._cache
            try:
                frame = await self._gate.run(self._loader, timeout=60)
                universe = self._build_universe(frame)
            except QuoteUnavailableError:
                self._last_error_at = time.monotonic()
                if allow_failure:
                    return None
                raise
            except Exception as exc:
                self._last_error_at = time.monotonic()
                self._warn(f"股票名称列表获取失败: {type(exc).__name__}: {exc}")
                if allow_failure:
                    return None
                raise QuoteUnavailableError(
                    "股票名称列表获取失败，请稍后再试或改用 6 位代码。"
                ) from exc
            self._cache = universe
            self._last_error_at = 0.0
            return universe

    @staticmethod
    def _build_universe(frame: Any) -> StockUniverse:
        columns = set(getattr(frame, "columns", ()))
        if not {"code", "name"}.issubset(columns):
            raise QuoteUnavailableError("AKShare 返回的股票列表格式不符合预期。")
        name_to_code: dict[str, str] = {}
        code_to_name: dict[str, str] = {}
        for row in frame.to_dict("records"):
            code = _normalize_code(row.get("code"))
            name = clean_display_name(str(row.get("name") or ""))
            if not code or not name:
                continue
            code_to_name[code] = name
            key = normalize_name(name)
            if key and key not in name_to_code:
                name_to_code[key] = code
        if not code_to_name:
            raise QuoteUnavailableError("AKShare 返回的股票列表为空。")
        return StockUniverse(
            name_to_code=name_to_code,
            code_to_name=code_to_name,
            loaded_at=time.monotonic(),
        )

    def _warn(self, message: str) -> None:
        if self._logger is None:
            return
        try:
            self._logger.warning(message[:500])
        except Exception:  # pragma: no cover - 日志失败不影响业务
            pass


def _normalize_code(value: Any) -> str:
    text = str(value if value is not None else "").strip()
    if text.endswith(".0") and text[:-2].isdigit():
        text = text[:-2]
    if text.casefold().startswith(("sh", "sz", "bj")):
        text = text[2:]
    if text.isdigit():
        return text.zfill(6)
    return text
