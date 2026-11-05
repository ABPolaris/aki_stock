"""真实 SDK 加载测试：入口、配置模型、命令注册与卸载。"""

from __future__ import annotations

from pathlib import Path

import pytest
from neobot_modloader.context import RuntimePluginContext
from neobot_modloader.loader import FilesystemPluginLoader


class Adapter:
    self_id = 10001

    def __init__(self) -> None:
        self.messages = []

    async def send(self, conversation, message):
        self.messages.append(message)
        return "test-message"


class Commands:
    """模拟宿主命令表，只验证插件注册结果。"""

    available = True

    def __init__(self) -> None:
        self.items = {}

    def register(self, command):
        if command.name in self.items:
            raise ValueError("命令已注册")
        self.items[command.name] = command

    def unregister(self, name):
        return self.items.pop(name, None) is not None


@pytest.mark.asyncio
async def test_plugin_loads_and_registers_commands(tmp_path):
    loader = FilesystemPluginLoader()
    loaded = loader.load_one(Path(__file__).resolve().parents[1])
    assert loaded is not None and loaded.plugin is not None
    adapter = Adapter()
    commands = Commands()
    context = RuntimePluginContext(
        plugin_name="aki_stock",
        plugin_dir=loaded.plugin_dir,
        data_dir=tmp_path,
        config={"source": "xueqiu", "watchlist_limit": 5},
        logger=None,
        adapter=adapter,
        app_commands=commands,
    )
    try:
        await loaded.plugin.on_load(context)
        assert set(commands.items) == {"行情", "自选股", "自选股信息", "A股概况"}

        quote = commands.items["行情"]
        assert quote.permission == 0
        assert quote.source == "aki_stock"
        assert "股票代码或名称" in quote.help_line
        assert quote.aliases == ("股价", "quote")

        watchlist = commands.items["自选股"]
        assert "添加" in watchlist.usage
        assert commands.items["自选股信息"].aliases == ("自选行情", "自选股行情")
        assert commands.items["A股概况"].aliases == ("大盘", "股市概况", "a股概况")

        surface = loaded.plugin.surface.describe(loaded.plugin)
        assert surface["config"]["properties"]["watchlist_limit"]["maximum"] == 50
        assert surface["guides"][0]["id"] == "first-use"
    finally:
        await loaded.plugin.on_stop()
        context.app_commands.unregister_all()
        loader.clear_module_cache(loaded.module_names)
    assert commands.items == {}
