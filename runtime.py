"""插件运行时容器：把配置、数据源、名称解析、自选股存储装在一起。

命令层只依赖这个容器，不直接 import AKShare，也不关心配置文件位置；
测试可以注入假 resolver / 假 quotes / 临时目录，完整走一遍命令逻辑。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import Config
from .resolver import StockResolver
from .sources import AkshareGate, QuoteService
from .storage import WatchlistStore


def coerce_config(value: Any) -> Config:
    if isinstance(value, Config):
        return value
    if hasattr(value, "model_dump"):
        value = value.model_dump()
    return Config.model_validate(value or {})


@dataclass(frozen=True, slots=True)
class PluginHotReloadRule:
    """与宿主 ``HotReloadRule`` 同形的轻量声明，避免第三方插件依赖 app 内部模块。"""

    path: str
    hot_reload: bool
    reason: str = ""


class AkiStockConfigConsumer:
    """让“数据源”和“自选股上限”保存后原地生效，不必重载整个插件。"""

    def __init__(self, runtime: AkiStockRuntime, plugin_name: str) -> None:
        self._runtime = runtime
        self.name = plugin_name
        self.config_paths = (f"plugin.{plugin_name}",)

    @property
    def hot_reload_policies(self) -> tuple[PluginHotReloadRule, ...]:
        prefix = f"plugin.{self.name}"
        return (
            PluginHotReloadRule(
                f"{prefix}.source",
                True,
                "数据源在每次查询时读取，保存后下一条命令即生效",
            ),
            PluginHotReloadRule(
                f"{prefix}.watchlist_limit",
                True,
                "自选股上限在增删时读取，保存后立即生效",
            ),
        )

    async def apply_config(self, config: Any) -> None:
        self._runtime.update_config(coerce_config(config))


class AkiStockRuntime:
    def __init__(
        self,
        config: Config,
        data_dir: Path,
        *,
        logger: Any | None = None,
        gate: AkshareGate | None = None,
        resolver: StockResolver | None = None,
        quotes: QuoteService | None = None,
        watchlists: WatchlistStore | None = None,
        sleep: Any | None = None,
    ) -> None:
        self.config = config
        self.data_dir = Path(data_dir)
        self.logger = logger
        self.gate = gate or AkshareGate()
        self.resolver = resolver or StockResolver(self.gate, logger=logger)
        self.quotes = quotes or QuoteService(config.source, self.gate, logger=logger)
        self.watchlists = watchlists or WatchlistStore(
            self.data_dir / "watchlists.json", logger=logger
        )
        self.sleep = sleep or asyncio.sleep
        self._ctx: Any | None = None
        self._consumer: AkiStockConfigConsumer | None = None

    @classmethod
    def from_context(cls, ctx: Any) -> AkiStockRuntime:
        runtime = cls(
            coerce_config(getattr(ctx, "config", None)),
            Path(getattr(ctx, "data_dir", Path.cwd())),
            logger=getattr(ctx, "logger", None),
        )
        runtime._ctx = ctx
        return runtime

    @property
    def plugin_name(self) -> str:
        return "aki_stock"

    def update_config(self, config: Config) -> None:
        self.config = config
        set_source = getattr(self.quotes, "set_source", None)
        if callable(set_source):
            set_source(config.source)

    def register_config_consumer(self) -> None:
        ctx = self._ctx
        control = getattr(ctx, "plugin_control", None)
        register = getattr(control, "register_config_consumer", None)
        if not callable(register):
            return
        plugin_name = str(getattr(ctx, "plugin_name", self.plugin_name) or self.plugin_name)
        consumer = AkiStockConfigConsumer(self, plugin_name)
        try:
            register(plugin_name, consumer)
        except Exception as exc:  # pragma: no cover - 宿主未提供时不影响主流程
            self.log_warning(f"注册配置热重载失败: {type(exc).__name__}: {exc}")
            return
        self._consumer = consumer

    async def close(self) -> None:
        if self._consumer is None:
            return
        ctx = self._ctx
        control = getattr(ctx, "plugin_control", None)
        unregister = getattr(control, "unregister_config_consumer", None)
        plugin_name = str(getattr(ctx, "plugin_name", self.plugin_name) or self.plugin_name)
        if callable(unregister):
            try:
                unregister(plugin_name)
            except Exception as exc:  # pragma: no cover - 卸载阶段只记日志
                self.log_warning(f"注销配置热重载失败: {type(exc).__name__}: {exc}")
        self._consumer = None

    def log_warning(self, message: str) -> None:
        if self.logger is None:
            return
        try:
            self.logger.warning(message[:500])
        except Exception:  # pragma: no cover - 日志失败不影响业务
            pass

    def log_error(self, message: str) -> None:
        if self.logger is None:
            return
        try:
            self.logger.error(message[:500])
        except Exception:  # pragma: no cover - 日志失败不影响业务
            pass


__all__ = [
    "AkiStockConfigConsumer",
    "AkiStockRuntime",
    "PluginHotReloadRule",
    "coerce_config",
]
