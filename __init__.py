"""aki_stock：A 股行情查询、自选股管理与大盘概况。

命令层只负责解析聊天参数、调用业务模块并组织中文回复；
数据源细节在 ``sources.py``，名称解析在 ``resolver.py``，
自选股持久化在 ``storage.py``，个股 / 自选 / 概况分别在
``quote.py`` / ``watchlist.py`` / ``overview.py``。
"""

from __future__ import annotations

from typing import Any

from neobot_modloader import Plugin

from . import overview, quote, watchlist
from .config import Config
from .models import (
    Quote,
    QuoteOutcome,
    QuoteUnavailableError,
    StockRef,
    UserInputError,
    WatchlistDuplicateError,
    WatchlistLimitError,
    WatchlistMissingError,
)
from .runtime import AkiStockRuntime
from .sources import SOURCE_LABELS

plugin = Plugin(
    "aki_stock",
    version="v0.1.4-a1",
    description="A股行情查询、自选股管理与大盘概况（AKShare）",
    config=Config,
    hot_reload=True,
    config_hot_reload=True,
)

_runtime: AkiStockRuntime | None = None


def _set_runtime(runtime: AkiStockRuntime | None) -> None:
    """测试与生命周期共用的运行时绑定入口。"""

    global _runtime
    _runtime = runtime


@plugin.on_load
async def _on_load(ctx: Any) -> None:
    runtime = AkiStockRuntime.from_context(ctx)
    _set_runtime(runtime)
    runtime.register_config_consumer()
    _register_commands(ctx)


@plugin.on_shutdown
async def _on_shutdown(ctx: Any) -> None:
    runtime = _runtime
    _set_runtime(None)
    if runtime is not None:
        await runtime.close()


def _register_commands(ctx: Any) -> None:
    registrar = getattr(ctx, "app_commands", None)
    if registrar is None or not getattr(registrar, "available", False):
        logger = getattr(ctx, "logger", None)
        if logger is not None:
            try:
                logger.warning("宿主命令表不可用，aki_stock 跳过命令注册")
            except Exception:  # pragma: no cover - 日志失败不影响加载
                pass
        return

    @registrar.register(
        "行情",
        description="查询一只 A 股或指数的最新行情",
        usage="<股票代码或名称>",
        aliases=("股价", "quote"),
        params=(
            (
                "股票代码或名称",
                "6 位代码或名称，如 002456、TCL科技、上证综指",
            ),
        ),
    )
    async def _quote_command(command_ctx: Any) -> str:
        runtime = _runtime
        if runtime is None:
            return _NOT_READY
        return await _handle_quote(runtime, command_ctx)

    @registrar.register(
        "自选股",
        description="管理自己的自选股列表",
        usage="[列表|添加|删除|信息] [股票代码或名称]",
        aliases=("自选", "watchlist"),
        params=(
            ("操作", "列表 / 添加 / 删除 / 信息 / 帮助；省略时显示列表"),
            ("股票代码或名称", "添加或删除时提供一只股票"),
        ),
    )
    async def _watchlist_command(command_ctx: Any) -> str:
        runtime = _runtime
        if runtime is None:
            return _NOT_READY
        return await _handle_watchlist(runtime, command_ctx)

    @registrar.register(
        "自选股信息",
        description="依次查询自选股列表中的全部行情",
        aliases=("自选行情", "自选股行情"),
    )
    async def _watchlist_quotes_command(command_ctx: Any) -> str:
        runtime = _runtime
        if runtime is None:
            return _NOT_READY
        return await _handle_watchlist_quotes(runtime, command_ctx)

    @registrar.register(
        "A股概况",
        description="查询上证综指、深证成指和北证 50",
        aliases=("大盘", "股市概况", "a股概况"),
    )
    async def _overview_command(command_ctx: Any) -> str:
        runtime = _runtime
        if runtime is None:
            return _NOT_READY
        return await _handle_overview(runtime, command_ctx)

    logger = getattr(ctx, "logger", None)
    if logger is not None:
        try:
            logger.info("aki_stock 已注册 /行情、/自选股、/自选股信息、/A股概况")
        except Exception:  # pragma: no cover - 日志失败不影响加载
            pass


_NOT_READY = "插件尚未就绪，请稍后再试。"
_MISSING_QUOTE = "请提供股票代码或名称，例如：/行情 002456 或 /行情 TCL科技。"
_TOO_MANY = "一次只能查询一只股票，请只提供一个代码或名称。"
_EMPTY_WATCHLIST = "自选股列表为空。\n添加：/自选股 添加 <代码或名称>"
_WATCHLIST_HELP = (
    "自选股用法：\n"
    "/自选股 列表 — 查看列表\n"
    "/自选股 添加 <代码或名称> — 添加一只\n"
    "/自选股 删除 <代码或名称> — 删除一只\n"
    "/自选股信息 — 查询全部自选股行情"
)


def _args(command_ctx: Any) -> list[str]:
    args = getattr(command_ctx, "args", None)
    if isinstance(args, (list, tuple)):
        return [str(item).strip() for item in args if str(item).strip()]
    raw = str(getattr(command_ctx, "raw_args", "") or "").strip()
    return raw.split() if raw else []


def _user_id(command_ctx: Any) -> int:
    try:
        return int(getattr(command_ctx, "user_id", 0) or 0)
    except (TypeError, ValueError):
        return 0


async def _handle_quote(runtime: AkiStockRuntime, command_ctx: Any) -> str:
    args = _args(command_ctx)
    if not args:
        return _MISSING_QUOTE
    if len(args) != 1:
        return _TOO_MANY
    try:
        result = await quote.query_one(runtime, args[0])
        return _format_single(result)
    except (UserInputError, QuoteUnavailableError) as exc:
        return str(exc)
    except Exception as exc:  # pragma: no cover - 兜底，避免异常泄漏到聊天
        runtime.log_error(f"行情查询失败: {type(exc).__name__}: {exc}")
        return "查询失败，请稍后再试。"


async def _handle_watchlist(runtime: AkiStockRuntime, command_ctx: Any) -> str:
    user_id = _user_id(command_ctx)
    if user_id <= 0:
        return "无法识别当前用户，请在群聊或私聊中重新发送。"
    args = _args(command_ctx)
    if not args:
        return await _list_watchlist(runtime, user_id)

    action = args[0].casefold()
    rest = args[1:]
    if action in {"列表", "list", "ls", "查看"}:
        if rest:
            return "查询自选股列表不需要股票参数。"
        return await _list_watchlist(runtime, user_id)
    if action in {"信息", "行情", "quotes", "query"}:
        if rest:
            return "查询自选股行情不需要股票参数；请直接发送 /自选股信息。"
        return await _query_watchlist(runtime, user_id)
    if action in {"添加", "add", "新增", "a"}:
        return await _add_watchlist(runtime, user_id, rest)
    if action in {"删除", "remove", "del", "rm", "d"}:
        return await _remove_watchlist(runtime, user_id, rest)
    if action in {"帮助", "help", "?", "h"}:
        return _WATCHLIST_HELP
    return "未知操作。支持：列表 / 添加 / 删除 / 信息 / 帮助。"


async def _handle_watchlist_quotes(runtime: AkiStockRuntime, command_ctx: Any) -> str:
    if _args(command_ctx):
        return "该指令不接受参数，请直接发送 /自选股信息。"
    user_id = _user_id(command_ctx)
    if user_id <= 0:
        return "无法识别当前用户，请在群聊或私聊中重新发送。"
    return await _query_watchlist(runtime, user_id)


async def _handle_overview(runtime: AkiStockRuntime, command_ctx: Any) -> str:
    if _args(command_ctx):
        return "该指令不接受参数，请直接发送 /A股概况。"
    try:
        outcomes = await overview.query_all(runtime)
        return _format_many("A股概况", outcomes)
    except QuoteUnavailableError as exc:
        return str(exc)
    except Exception as exc:  # pragma: no cover - 兜底
        runtime.log_error(f"A股概况查询失败: {type(exc).__name__}: {exc}")
        return "查询失败，请稍后再试。"


async def _list_watchlist(runtime: AkiStockRuntime, user_id: int) -> str:
    try:
        items = await watchlist.list_items(runtime, user_id)
    except QuoteUnavailableError as exc:
        return str(exc)
    limit = runtime.config.watchlist_limit
    if not items:
        return f"自选股列表为空（0/{limit}）。\n添加：/自选股 添加 <代码或名称>"
    lines = [f"自选股（{len(items)}/{limit}）"]
    for index, item in enumerate(items, 1):
        lines.append(f"{index}. {item.as_ref().label}")
    lines.extend(
        [
            "",
            "添加：/自选股 添加 <代码或名称>",
            "删除：/自选股 删除 <代码或名称>",
            "查询行情：/自选股信息",
        ]
    )
    return "\n".join(lines)


async def _add_watchlist(runtime: AkiStockRuntime, user_id: int, rest: list[str]) -> str:
    if not rest:
        return "请提供要添加的股票代码或名称，例如：/自选股 添加 002456。"
    if len(rest) != 1:
        return "一次只能添加一只股票，请只提供一个代码或名称。"
    try:
        ref, count = await watchlist.add(runtime, user_id, rest[0])
        return (
            f"已添加自选股：{ref.label}"
            f"（{count}/{runtime.config.watchlist_limit}）"
        )
    except (UserInputError, QuoteUnavailableError) as exc:
        return str(exc)
    except (WatchlistDuplicateError, WatchlistLimitError) as exc:
        return str(exc)
    except Exception as exc:  # pragma: no cover - 兜底
        runtime.log_error(f"添加自选股失败: {type(exc).__name__}: {exc}")
        return "添加失败，请稍后再试。"


async def _remove_watchlist(runtime: AkiStockRuntime, user_id: int, rest: list[str]) -> str:
    if not rest:
        return "请提供要删除的股票代码或名称，例如：/自选股 删除 002456。"
    if len(rest) != 1:
        return "一次只能删除一只股票，请只提供一个代码或名称。"
    try:
        entry, count = await watchlist.remove(runtime, user_id, rest[0])
        return (
            f"已删除自选股：{entry.as_ref().label}"
            f"（{count}/{runtime.config.watchlist_limit}）"
        )
    except (UserInputError, QuoteUnavailableError) as exc:
        return str(exc)
    except WatchlistMissingError as exc:
        return str(exc)
    except Exception as exc:  # pragma: no cover - 兜底
        runtime.log_error(f"删除自选股失败: {type(exc).__name__}: {exc}")
        return "删除失败，请稍后再试。"


async def _query_watchlist(runtime: AkiStockRuntime, user_id: int) -> str:
    try:
        items = await watchlist.list_items(runtime, user_id)
        if not items:
            return _EMPTY_WATCHLIST
        outcomes = await watchlist.query_all(runtime, user_id, items=items)
        title = f"自选股行情（{len(items)}/{runtime.config.watchlist_limit}）"
        return _format_many(title, outcomes)
    except QuoteUnavailableError as exc:
        return str(exc)
    except Exception as exc:  # pragma: no cover - 兜底
        runtime.log_error(f"自选股行情查询失败: {type(exc).__name__}: {exc}")
        return "查询失败，请稍后再试。"


def _format_single(result: Quote) -> str:
    ref = result.ref
    head = ref.label
    price = _price(result.price)
    if ref.kind == "index":
        price_line = f"最新：{price}" + (" 点" if result.price is not None else "")
    else:
        price_line = f"最新价：{price}" + (" 元" if result.price is not None else "")
    unit = "点" if ref.kind == "index" else "元"
    lines = [
        head,
        price_line,
        f"涨跌额：{_amount(result.change_amount, unit)}",
        f"涨跌幅：{_pct(result.change_percent)}",
        f"数据源：{_source_label(result)}",
    ]
    if result.as_of:
        lines.insert(-1, f"行情时间：{result.as_of}")
    return "\n".join(lines)


def _format_many(title: str, outcomes: list[QuoteOutcome]) -> str:
    # 一行里三个数字，标题先说明顺序，避免把涨跌额误读成价格。
    lines = [f"{title} · 价格 | 涨跌额 | 涨跌幅"]
    for index, outcome in enumerate(outcomes, 1):
        name = _ref_name(outcome.ref)
        if outcome.quote is None:
            lines.append(f"{index}. {name}：查询失败（{_short_error(outcome.error)}）")
            continue
        result = outcome.quote
        lines.append(
            f"{index}. {name}\n"
            f"{_price(result.price)} | {_amount_value(result.change_amount)} | {_pct(result.change_percent)}"
        )
    source = next((item.quote.source for item in outcomes if item.quote is not None), "")
    if source:
        lines.append(f"数据源：{SOURCE_LABELS.get(source, source)}")
    return "\n".join(lines)


def _ref_name(ref: StockRef) -> str:
    return ref.label


def _source_label(result: Quote) -> str:
    return SOURCE_LABELS.get(result.source, result.source or "未知")


def _price(value: float | None) -> str:
    return "—" if value is None else f"{value:.2f}"


def _amount_value(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:+.2f}" if value > 0 else f"{value:.2f}"


def _amount(value: float | None, unit: str) -> str:
    text = _amount_value(value)
    return text if text == "—" else f"{text} {unit}"


def _pct(value: float | None) -> str:
    if value is None:
        return "—"
    if value > 0:
        return f"+{value:.2f}%"
    if value < 0:
        return f"{value:.2f}%"
    return "0.00%"


def _short_error(error: str) -> str:
    text = str(error or "")
    if not text:
        return "暂时不可用"
    if "频繁" in text or "限流" in text:
        return "数据源限流"
    if "令牌" in text or "鉴权" in text:
        return "数据源鉴权失败"
    if "超时" in text:
        return "请求超时"
    if "名称列表" in text:
        return "名称解析失败"
    return "暂时不可用"


plugin.guide(
    "first-use",
    title="配置行情源并查询 A 股",
    steps=[
        {
            "target": "config.source",
            "form": "start",
            "chapter": "选择数据源",
            "text": "在插件管理选择 aki_stock，打开配置表单。默认使用雪球源；这里从公开默认值开始演示，真实配置未被修改。",
            "example": {"source": "xueqiu", "watchlist_limit": 5},
        },
        {
            "target": "config.source",
            "form": "edit",
            "chapter": "切换数据源",
            "text": "东方财富源在部分网络会被限流，雪球源会自动获取访问令牌。按网络情况选择即可，保存后下一条命令生效。",
            "example": {"source": "eastmoney", "watchlist_limit": 5},
        },
        {
            "target": "config.watchlist_limit",
            "form": "edit",
            "chapter": "设置自选股上限",
            "text": "填写每位用户最多保存的自选股数量，默认 5 只，范围 1-50。聊天里一次只增删一只，避免刷屏和频繁请求。",
            "example": {"source": "eastmoney", "watchlist_limit": 8},
        },
        {
            "target": "config",
            "form": "validate",
            "chapter": "核对配置",
            "text": "核对数据源与上限。这里演示本地检查；真实保存仍由服务端校验，错误时请按字段提示修正。",
            "example": {"source": "eastmoney", "watchlist_limit": 8},
        },
        {
            "target": "config",
            "form": "save",
            "chapter": "保存配置",
            "text": "使用底部保存。这里仅演示保存位置，不会发送真实保存请求；真实配置写入 plugins_data/aki_stock/config.toml。",
            "example": {"source": "eastmoney", "watchlist_limit": 8},
        },
        {
            "target": "config",
            "form": "saved",
            "chapter": "查看生效结果",
            "text": "返回配置后查看全局通知。数据源与上限支持保存后立即生效，不需要重载整个插件。",
            "example": {"source": "eastmoney", "watchlist_limit": 8},
        },
        {
            "target": "config",
            "chapter": "查询一只股票",
            "text": "在私聊发送 /行情 002456 查代码，或发送 /行情 TCL科技 查名称。演示不会代发消息。",
            "command": {
                "text": "/行情 002456",
                "context": "私聊，或群聊中 @机器人",
                "reply": "002456 欧菲光\n最新价：5.78 元\n涨跌额：+0.10 元\n涨跌幅：+1.76%\n数据源：雪球",
            },
        },
        {
            "target": "config",
            "chapter": "添加自选股",
            "text": "发送 /自选股 添加 002456；删除用 /自选股 删除 002456，查看列表用 /自选股 列表。",
            "command": {
                "text": "/自选股 添加 002456",
                "context": "私聊，或群聊中 @机器人",
                "reply": "已添加自选股：002456 欧菲光（1/5）",
            },
        },
        {
            "target": "config",
            "chapter": "查询自选股与大盘",
            "text": "/自选股信息 会依次查询列表中的股票，每次之间间隔 1 秒；/A股概况 查询上证综指、深证成指和北证 50。",
            "command": {
                "text": "/A股概况",
                "context": "私聊，或群聊中 @机器人",
                "reply": "A股概况 · 价格 / 涨跌额 / 涨跌幅\n1. 上证综指（000001）：3689.91  +3.74  +0.10%\n2. 深证成指（399001）：11471.09  +135.41  +1.19%\n3. 北证50（899050）：910.07  +15.99  +1.79%\n数据源：雪球",
            },
        },
    ],
    demo={"config": {"source": "xueqiu", "watchlist_limit": 8}},
)
