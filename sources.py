"""AKShare 数据源适配层。

本模块只做三件事：

1. 把 AKShare 的同步网络调用放进线程，并串行化，避免并发请求触发限流；
2. 把东方财富 / 雪球两种返回结构归一化成 :class:`Quote`；
3. 把底层异常翻译成用户能看懂、且不泄露细节的中文提示。

AKShare 在模块内延迟导入：插件本身可以离线加载，只有真正查询时才需要它。
"""

from __future__ import annotations

import asyncio
import math
import time
from collections.abc import Callable
from dataclasses import replace
from typing import Any, TypeVar

from .models import Quote, QuoteUnavailableError, StockRef

T = TypeVar("T")

SOURCE_LABELS: dict[str, str] = {
    "eastmoney": "东方财富",
    "xueqiu": "雪球",
}

_XUEQIU_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
_EASTMONEY_INDEX_BOARDS = ("沪深重要指数", "中证系列指数", "上证系列指数", "深证系列指数")


def validate_source(value: str) -> str:
    """把配置值收敛到受支持的数据源，未知值直接报错而不是静默猜一个。"""

    text = str(value or "").strip().lower()
    if text not in SOURCE_LABELS:
        raise ValueError(f"不支持的行情数据源: {value!r}")
    return text


class AkshareGate:
    """串行执行 AKShare 同步调用，避免多个聊天命令同时打满数据源。

    线程无法被 ``wait_for`` 真正终止，因此超时只保证调用方及时拿到错误；
    后台线程结束后会释放底层资源，不会持有插件状态。
    """

    def __init__(self, *, timeout: float = 20.0) -> None:
        self._lock = asyncio.Lock()
        self._timeout = float(timeout)

    async def run(self, func: Callable[..., T], *args: Any, timeout: float | None = None) -> T:
        async with self._lock:
            try:
                return await asyncio.wait_for(
                    asyncio.to_thread(func, *args),
                    timeout=self._timeout if timeout is None else float(timeout),
                )
            except asyncio.TimeoutError as exc:
                raise QuoteUnavailableError("行情源响应超时，请稍后再试。") from exc


def _akshare():
    try:
        import akshare as ak
    except ImportError as exc:  # pragma: no cover - 安装完整时不会走到
        raise QuoteUnavailableError(
            "插件缺少 akshare 依赖，请在 NeoBot 插件管理中重新安装本插件。"
        ) from exc
    return ak


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip().replace(",", "")
        if text in {"", "-", "--", "None", "nan", "NaN"}:
            return None
        try:
            value = float(text)
        except ValueError:
            return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return number


def _normalize_code(value: Any) -> str:
    """把 ``sh000001`` / ``1`` / ``000001`` 统一成 6 位代码。"""

    text = str(value if value is not None else "").strip()
    if text.endswith(".0") and text[:-2].isdigit():
        text = text[:-2]
    lowered = text.casefold()
    if lowered.startswith(("sh", "sz", "bj")):
        text = text[2:]
    if text.isdigit():
        return text.zfill(6)
    return text


def _item_map(frame: Any) -> dict[str, Any]:
    """把 AKShare 的 ``item`` / ``value`` 两列结构转成字典。"""

    try:
        if frame is None or len(frame) == 0:
            return {}
        columns = set(getattr(frame, "columns", ()))
        if not {"item", "value"}.issubset(columns):
            return {}
        return {str(key): value for key, value in zip(frame["item"], frame["value"])}
    except (KeyError, TypeError, ValueError):
        return {}


def _row_for_code(frame: Any, code: str) -> dict[str, Any] | None:
    try:
        if frame is None or len(frame) == 0:
            return None
        if "代码" not in set(getattr(frame, "columns", ())):
            return None
        for row in frame.to_dict("records"):
            if _normalize_code(row.get("代码")) == code:
                return dict(row)
    except (KeyError, TypeError, ValueError):
        return None
    return None


def _parse_em_item(frame: Any, ref: StockRef) -> Quote:
    data = _item_map(frame)
    price = _as_float(data.get("最新"))
    change = _as_float(data.get("涨幅"))
    if price is None and change is None:
        raise QuoteUnavailableError("东方财富未返回有效行情，该股票可能停牌或代码已失效。")
    return Quote(ref=ref, price=price, change_percent=change, source="eastmoney")


def _parse_em_index_row(row: dict[str, Any], ref: StockRef) -> Quote:
    price = _as_float(row.get("最新价"))
    change = _as_float(row.get("涨跌幅"))
    if price is None and change is None:
        raise QuoteUnavailableError("东方财富未返回该指数的有效行情。")
    return Quote(ref=ref, price=price, change_percent=change, source="eastmoney")


def _parse_xq_item(frame: Any, ref: StockRef) -> Quote:
    data = _item_map(frame)
    price = _as_float(data.get("现价"))
    change = _as_float(data.get("涨幅"))
    if price is None and change is None:
        raise QuoteUnavailableError("雪球未返回有效行情，该股票可能停牌或代码已失效。")
    name = str(data.get("名称") or "").strip()
    # 代码查询且名称表暂时不可用时，ref.name 会退化为代码；此时用雪球返回的名称补全。
    if name and (not ref.name or ref.name == ref.code):
        display_ref = replace(ref, name=name)
    else:
        display_ref = ref
    return Quote(ref=display_ref, price=price, change_percent=change, source="xueqiu")


def looks_like_token_error(exc: BaseException) -> bool:
    text = str(exc)
    return "xq_a_token" in text or "400016" in text or "登录" in text


class XueqiuTokenProvider:
    """从雪球首页获取匿名访问令牌；令牌只保存在内存中，不写日志、不落盘。"""

    def __init__(self, *, ttl: float = 25 * 60) -> None:
        self._token: str | None = None
        self._fetched_at = 0.0
        self._ttl = float(ttl)

    def get(self, *, force: bool = False) -> str:
        now = time.monotonic()
        if not force and self._token and now - self._fetched_at < self._ttl:
            return self._token
        token = self._fetch()
        self._token = token
        self._fetched_at = now
        return token

    @staticmethod
    def _fetch() -> str:
        try:
            import requests
        except ImportError as exc:  # pragma: no cover - akshare 会带入 requests
            raise QuoteUnavailableError("雪球源缺少 requests 依赖，请重新安装插件。") from exc
        try:
            session = requests.Session()
            response = session.get(
                "https://xueqiu.com",
                headers={"User-Agent": _XUEQIU_USER_AGENT},
                timeout=12,
            )
            response.raise_for_status()
            token = session.cookies.get("xq_a_token")
        except Exception as exc:
            raise QuoteUnavailableError("无法获取雪球访问令牌，请稍后再试或切换到东方财富源。") from exc
        if not token:
            raise QuoteUnavailableError("雪球没有返回访问令牌，请稍后再试或切换到东方财富源。")
        return str(token)


class EastmoneySource:
    """东方财富：个股走 ``stock_bid_ask_em``，指数走指数列表接口。"""

    def __init__(self) -> None:
        self._board_cache: dict[str, tuple[float, Any]] = {}

    def fetch(self, ref: StockRef) -> Quote:
        ak = _akshare()
        if ref.kind == "index":
            return self._fetch_index(ak, ref)
        frame = ak.stock_bid_ask_em(symbol=ref.code)
        return _parse_em_item(frame, ref)

    def _fetch_index(self, ak: Any, ref: StockRef) -> Quote:
        row = self._index_row(ak, ref.code)
        if row is not None:
            return _parse_em_index_row(row, ref)
        # 东财 399xxx / 899xxx 的 market 段为 0，个股报价接口可以直接取到指数。
        if ref.code.startswith(("399", "899")):
            frame = ak.stock_bid_ask_em(symbol=ref.code)
            return _parse_em_item(frame, ref)
        raise QuoteUnavailableError("东方财富未返回该指数的行情。")

    def _index_row(self, ak: Any, code: str) -> dict[str, Any] | None:
        row = _row_for_code(self._board(ak, _EASTMONEY_INDEX_BOARDS[0]), code)
        if row is not None:
            return row
        # 北证 50 / 深证指数不在“沪深重要指数”时，用个股报价接口兜底；
        # 其余指数再试几个板块列表，避免把 000001 当成深市股票。
        if code.startswith(("399", "899")):
            return None
        for board in _EASTMONEY_INDEX_BOARDS[1:]:
            row = _row_for_code(self._board(ak, board), code)
            if row is not None:
                return row
        return None

    def _board(self, ak: Any, board: str) -> Any:
        now = time.monotonic()
        cached = self._board_cache.get(board)
        if cached is not None and now - cached[0] < 30:
            return cached[1]
        frame = ak.stock_zh_index_spot_em(symbol=board)
        self._board_cache[board] = (now, frame)
        return frame


class XueqiuSource:
    """雪球：个股与指数共用一个接口，代码需要 SH / SZ / BJ 前缀。"""

    def __init__(self, tokens: XueqiuTokenProvider | None = None) -> None:
        self._tokens = tokens or XueqiuTokenProvider()

    def fetch(self, ref: StockRef) -> Quote:
        ak = _akshare()
        symbol = f"{ref.market}{ref.code}"
        token = self._tokens.get()
        try:
            return self._fetch_with_token(ak, symbol, token, ref)
        except Exception as exc:
            if not looks_like_token_error(exc):
                raise
            token = self._tokens.get(force=True)
            try:
                return self._fetch_with_token(ak, symbol, token, ref)
            except Exception as retry_exc:
                raise QuoteUnavailableError(
                    "雪球源访问令牌失效，请稍后再试或切换到东方财富源。"
                ) from retry_exc

    @staticmethod
    def _fetch_with_token(ak: Any, symbol: str, token: str, ref: StockRef) -> Quote:
        try:
            frame = ak.stock_individual_spot_xq(symbol=symbol, token=token)
        except TypeError:
            # 兼容旧版 AKShare 没有 token 关键字参数的情况。
            frame = ak.stock_individual_spot_xq(symbol=symbol)
        return _parse_xq_item(frame, ref)


def _translate_error(exc: BaseException, source: str) -> QuoteUnavailableError:
    name = type(exc).__name__
    text = str(exc)
    lowered = text.casefold()
    if "RateLimit" in name or "限流" in text or "请求过多" in text or "too many" in lowered:
        label = SOURCE_LABELS.get(source, source)
        return QuoteUnavailableError(
            f"{label}源请求过于频繁，请稍后再试，或在插件设置中切换数据源。"
        )
    if source == "xueqiu" and looks_like_token_error(exc):
        return QuoteUnavailableError("雪球源访问令牌失效，请稍后再试或切换到东方财富源。")
    if isinstance(exc, TimeoutError) or "timed out" in lowered or "超时" in text:
        return QuoteUnavailableError("行情源响应超时，请稍后再试。")
    if "Network" in name or "Connection" in name or "RequestException" in name:
        return QuoteUnavailableError("行情源网络连接失败，请稍后再试。")
    return QuoteUnavailableError("行情服务暂时不可用，请稍后再试。")


class QuoteService:
    """按配置选择数据源，并统一处理超时、异常翻译与串行化。"""

    def __init__(
        self,
        source: str,
        gate: AkshareGate,
        *,
        logger: Any | None = None,
    ) -> None:
        self._gate = gate
        self._logger = logger
        self._sources = {
            "eastmoney": EastmoneySource(),
            "xueqiu": XueqiuSource(),
        }
        self._source_name = validate_source(source)

    @property
    def source_name(self) -> str:
        return self._source_name

    @property
    def source_label(self) -> str:
        return SOURCE_LABELS.get(self._source_name, self._source_name)

    def set_source(self, source: str) -> None:
        self._source_name = validate_source(source)

    async def fetch(self, ref: StockRef) -> Quote:
        source = self._sources[self._source_name]
        try:
            return await self._gate.run(source.fetch, ref)
        except QuoteUnavailableError:
            raise
        except Exception as exc:
            self._warn(f"{SOURCE_LABELS[self._source_name]}行情请求失败: {type(exc).__name__}: {exc}")
            raise _translate_error(exc, self._source_name) from exc

    def _warn(self, message: str) -> None:
        if self._logger is None:
            return
        try:
            self._logger.warning(message[:500])
        except Exception:  # pragma: no cover - 日志失败不影响业务
            pass
