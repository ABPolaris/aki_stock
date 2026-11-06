"""插件配置模型：直接由 NeoBot 面板渲染为设置表单。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class Config(BaseModel):
    """面板配置与 ``plugins_data/aki_stock/config.toml`` 共用这一份模型。"""

    source: Literal["eastmoney", "xueqiu"] = Field(
        default="xueqiu",
        title="行情数据源",
        description="雪球源自动获取访问令牌；东方财富源在部分网络可能被限流。",
        json_schema_extra={
            "option_labels": {
                "eastmoney": "东方财富",
                "xueqiu": "雪球",
            }
        },
    )
    watchlist_limit: int = Field(
        default=5,
        ge=1,
        le=50,
        title="自选股数量上限",
        description="每位用户最多可保存的自选股数量，默认 5 只，范围 1-50。",
        json_schema_extra={"single_line": True},
    )
