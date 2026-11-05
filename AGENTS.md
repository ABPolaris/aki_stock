# aki_stock 开发约定

本工程是 NeoBot 的第三方插件，名称固定为 `aki_stock`。动手前先读 `README.md` 和本文件；宿主源码只读，验证输出放在插件目录之外。

## 入口与命令

- 入口是 `__init__.py` 导出的 `plugin` 实例；不要使用旧的 `setup(ctx)`、`Matcher`、`on_command`。
- 命令在 `on_load` 里通过 `ctx.app_commands.register(...)` 注册。需要 `command_ctx.args`（已按 shell 规则切分）、`user_id`、`raw_args` 和 `/help` 的 usage/params，因此本工程不使用 `@plugin.command` 的 DSL。
- 所有命令权限为 `0`（所有人）；不要在聊天命令里直接调用 AKShare。
- 缺少参数、输入过多、代码/名称无效时返回明确中文提示，不发起查询、不写自选股。

## 模块边界

| 文件 | 只负责 |
| --- | --- |
| `__init__.py` | 命令注册、参数分派、回复格式、使用说明 |
| `config.py` | 面板配置模型（中文标题、说明、范围） |
| `quote.py` | 个股与批量查询流程、1 秒间隔、失败不中断 |
| `watchlist.py` | 自选股增删查与批量行情 |
| `overview.py` | 上证综指 / 深证成指 / 北证 50 |
| `sources.py` | AKShare 东财 / 雪球适配、解析、异常翻译、串行闸门 |
| `resolver.py` | 输入校验、代码/名称/指数解析、名称表缓存 |
| `storage.py` | 自选股 JSON、用户隔离、原子写入 |
| `runtime.py` | 容器、配置热生效消费者 |
| `models.py` | 数据类与用户可见异常 |

新增行情源：在 `sources.py` 实现 `fetch(ref) -> Quote`，在 `QuoteService` 和 `SOURCE_LABELS` 注册，同步扩展 `config.py` 的 `Literal` 与 `plugin.toml` 的 `[config]`。新增业务功能：优先扩展 `quote.py`，不要在命令层复制批量循环。

## 安全

- 用户输入只用于白名单匹配；传给数据源的是 `StockRef` 里的 6 位代码和市场标识。
- 名称必须命中 AKShare 沪深京 A 股列表；不要用模糊搜索、拼音或用户原文拼 URL / SQL / shell / 文件路径。
- 自选股按 `user_id` 隔离；上限读 `runtime.config.watchlist_limit`，默认 5、范围 1-50。
- 雪球访问令牌只放内存，不写配置、不写日志、不落盘。
- 不提交真实 QQ 号、Cookie、令牌或用户自选股数据；测试只用合成数据。

## 验证与交付

在插件目录执行：

```sh
uv run --project ../NeoBot --no-sync python -m neobot_modloader.sdk check .
uv run --project ../NeoBot --no-sync python -m neobot_modloader.sdk export .
uv run --project ../NeoBot --no-sync python -m neobot_modloader.sdk export . --check
uv run --project ../NeoBot --no-sync python ../NeoBot/scripts/plugin_ci.py . --output-dir ../aki_stock_acceptance
```

- 测试必须是真实业务断言，覆盖成功、缺少参数、输入过多、无效代码/名称、自选股上限与用户隔离、批量顺序与 1 秒间隔、单只失败不中断。
- 外部服务使用合成 `resolver` / `quotes`；不要为了测试把 AKShare 调用打进 `tests/`。
- `plugin.contract.json` 只由 `export` 生成；改配置字段或教程目标后重新导出并检查差异。
- 报告真实执行过的命令与结果；离线验收不能证明真实 QQ、东财/雪球网络状态或面板表单下发。
- 不修改 `NeoBot/` 下的任何文件；验收输出放在插件目录外。
