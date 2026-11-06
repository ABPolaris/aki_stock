# aki_stock

NeoBot 的 A 股行情插件：查询个股 / 指数、管理每位用户的自选股，以及查看 A 股概况。数据通过 [AKShare](https://akshare.akfamily.xyz/) 获取，支持东方财富与雪球两种行情源。

## 功能

| 命令 | 说明 |
| --- | --- |
| `/行情 <股票代码或名称>` | 查询一只股票或指数的最新价、涨跌额、涨跌幅。支持 `002456`、`TCL科技`、`上证综指` 等形式；一次只能查一只。 |
| `/自选股`、`/自选股 列表` | 查看自己的自选股列表。 |
| `/自选股 添加 <代码或名称>` | 添加一只自选股，支持代码或名称。 |
| `/自选股 删除 <代码或名称>` | 删除一只自选股，支持代码或名称。 |
| `/自选股信息` | 依次查询自选股行情，查询之间间隔 1 秒；每行依次为价格 / 涨跌额 / 涨跌幅，单只失败不会中断其余查询。 |
| `/A股概况` | 查询上证综指、深证成指、北证 50（北证指数，899050），查询之间间隔 1 秒；每行依次为价格 / 涨跌额 / 涨跌幅。 |

别名：

- `/行情`：`/股价`、`/quote`
- `/自选股`：`/自选`、`/watchlist`
- `/自选股信息`：`/自选行情`、`/自选股行情`
- `/A股概况`：`/大盘`、`/股市概况`

群聊中仍需先 @机器人 再发送命令；私聊直接发送。命令名被本体内置命令占用时，NeoBot 会自动加 `aki_stock__` 前缀，以 `/help` 显示的实际名称为准。

### 输入规则

- 一次只接受一个代码或名称；多个参数、逗号 / 分号 / 斜杠等分隔符会直接提示，不发起查询。
- 缺少参数时提示示例，不执行其他操作。
- 代码必须是 6 位数字，可带 `SH` / `SZ` / `BJ` 前缀；前缀与代码市场不一致会拒绝。
- 名称必须在 AKShare 返回的沪深京 A 股白名单中精确匹配；匹配时做全角转半角、忽略大小写。名称包含空格时请用引号包成一个参数。
- `000001` 同时是平安银行和上证综指，按“优先 A 股”处理为平安银行；查上证综指请用名称 `/行情 上证综指`。
- 代码或名称有误时只反馈“没有找到”，不会把用户原文拼进 URL、SQL、shell 或文件路径。

## 配置

在 NeoBot 面板打开 **插件 → aki_stock → 配置表单**，或在插件数据目录编辑：

```text
plugins_data/aki_stock/config.toml
```

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `source` | `xueqiu` | `xueqiu` = 雪球，`eastmoney` = 东方财富。面板以下拉框展示。 |
| `watchlist_limit` | `5` | 每位用户最多保存的自选股数量，范围 1-50。 |

两项都声明了运行期生效：保存后下一条命令即使用新值，不需要重载插件。

数据源补充：

- **雪球**：插件会访问雪球首页获取匿名 `xq_a_token` 并缓存在内存中；令牌失效时自动刷新一次。不需要手动填写 Cookie。
- **东方财富**：使用 AKShare 的个股报价与指数列表接口。部分共享 IP 或高频请求会被东财临时限流，错误提示会建议稍后再试或切换到雪球源。
- 名称解析（代码 / 名称 → 6 位代码）与行情源无关，使用 AKShare 的沪深京 A 股列表，缓存 1 小时。

## 安装到 NeoBot

把本目录放进实际部署的 `plugins.dir`（默认 `plugins/`），重启 NeoBot。插件声明了 `akshare` 依赖，安装器会按 `plugin.toml` 的 `python_dependencies` 安装。

首次使用：

1. 在面板 **插件 → aki_stock** 确认插件正在运行；
2. 打开“配置表单”，按网络情况选择数据源，设置自选股上限并保存；
3. 私聊发送 `/help` 核对实际命令名；
4. 发送 `/行情 002456`（欧菲光）或 `/行情 TCL科技`（000100）检查回复。

自选股保存在 `plugins_data/aki_stock/watchlists.json`，按 QQ 用户隔离；不会写回插件源码目录。

## 开发与验收

需要 Python 3.13 / 3.14、uv，以及与 NeoBot 源码同级的目录：

```text
NeoBot/
aki_stock/       ← 当前目录
aki_stock_acceptance/
```

```sh
uv sync --project ../NeoBot --all-packages --locked
uv run --project ../NeoBot --no-sync python -m neobot_modloader.sdk check .
uv run --project ../NeoBot --no-sync python -m neobot_modloader.sdk export .
uv run --project ../NeoBot --no-sync python -m neobot_modloader.sdk export . --check
uv run --project ../NeoBot --no-sync python ../NeoBot/scripts/plugin_ci.py . --output-dir ../aki_stock_acceptance
```

离线测试使用合成 resolver / quotes / 临时数据目录，不访问 AKShare、QQ 或付费模型；当前覆盖输入校验、代码/名称解析、两种数据源的返回结构、自选股持久化、命令反馈、批量顺序与 1 秒间隔。

真实数据源联调不属于离线验收：雪球源已在开发机实测个股与三个指数；东方财富源在本机验证时被 IP 限流，只完成了返回结构解析与异常翻译测试。真实 QQ 群聊 @、面板表单下发和部署网络仍需在实际环境核对。

## 代码结构

| 文件 | 职责 |
| --- | --- |
| `__init__.py` | 插件入口：命令注册、参数解析、用户可见回复、使用说明。 |
| `config.py` | Pydantic 配置模型，直接生成面板设置表单。 |
| `quote.py` | 个股查询与批量查询公共流程（1 秒间隔、失败不中断）。 |
| `watchlist.py` | 自选股添加 / 删除 / 列表 / 批量行情。 |
| `overview.py` | A 股概况的固定指数列表。 |
| `sources.py` | AKShare 东财 / 雪球适配、返回结构归一化、异常翻译、串行化闸门。 |
| `resolver.py` | 输入校验、代码 / 名称 / 指数解析、名称表缓存。 |
| `storage.py` | 自选股 JSON 持久化（按用户隔离、原子写入、损坏隔离）。 |
| `runtime.py` | 运行时容器与配置热生效消费者。 |
| `models.py` | 数据类与用户可见异常，不依赖 AKShare / NeoBot。 |

扩展建议：

- 个股增加 K 线、资金流等能力：在 `quote.py` 增加函数，复用 `sources.py` 的串行闸门；需要新字段时扩展 `Quote` 或新增数据类。
- 新增行情源：在 `sources.py` 增加一个带 `fetch(ref) -> Quote` 的类，并在 `QuoteService` 与 `SOURCE_LABELS` 注册；配置的 `Literal` 同步扩展。
- 新增命令：在 `__init__.py` 的 `_register_commands` 注册，业务逻辑放进对应模块；不要在命令层直接调用 AKShare。
- 新增配置：在 `config.py` 加字段、在 `plugin.toml` 的 `[config]` 写默认值与注释；若需运行期生效，在 `runtime.py` 的 `hot_reload_policies` 登记路径。

## 安全与边界

- 用户输入只用于白名单解析，最终传给数据源的是插件计算出的 6 位代码和市场标识。
- 自选股按 `user_id` 隔离，默认上限 5 只，可在面板调整。
- 批量查询串行执行，查询之间间隔 1 秒；AKShare 调用统一经过 `AkshareGate`，避免并发打满数据源。
- 不记录雪球访问令牌；日志只保留异常类型和截断后的消息。
- 行情数据仅供信息查询，不构成投资建议；停牌、退市或数据源异常时价格可能显示为 `—`。
